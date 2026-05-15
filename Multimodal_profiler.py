"""
Multimodal Profiler v7 — DB 우선 + 동적 Fusion + Pseudo-Label
================================================================
v7 개선점:
  - DB의 review_images.image_path를 1차 입력으로 사용 (CSV는 폴백)
  - 이미지에 연결된 리뷰 텍스트로 linked_text_vector 생성 (pseudo-label)
  - 텍스트와 CLIP이 충돌하면 confidence 자동 하향
  - 동적 fusion (text_conf vs image_conf 비율로 alpha 조정)
  - CLIP 임베딩 캐싱 (clip_cache/)
  - 이미지 깨짐/없음 → text_only fallback
  - 대표 이미지 선정 시 식당-축 dedup + linked_text 점수 반영
  - --debug : debug_multimodal_<keyword>.json 생성

실행:
  python Multimodal_profiler.py 버거
  python Multimodal_profiler.py 버거 --mode simple
  python Multimodal_profiler.py 버거 --debug --no-cache
"""

import argparse
import glob
import hashlib
import json
import math
import os
import sys
import urllib.parse
from collections import defaultdict
from typing import Optional

import numpy as np
import pandas as pd

from db import (
    get_conn,
    get_reviews_for_profiler,
    upsert_restaurant,
    upsert_multimodal_profile,
    upsert_representative_images,
    delete_representative_images,
    bulk_get_review_image_scores,
    upsert_review_image_scores_bulk,
)

IMAGE_MODE = "clip"     # "clip" | "simple"
ALPHA_BASE = 0.7
BETA_BASE  = 0.3
STAND_DIR  = os.path.join(os.path.dirname(os.path.abspath(__file__)), "stand")
CLIP_CACHE_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "clip_cache")
IMAGE_CACHE_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "image_cache", "s3")

_stand_cache = None


def load_stand():
    global _stand_cache
    if _stand_cache:
        return _stand_cache
    paths = {k: os.path.join(STAND_DIR, f"stand_{k}.json") for k in ("axes", "lexicon", "rules")}
    if not all(os.path.exists(p) for p in paths.values()):
        return None
    stand = {}
    for k, p in paths.items():
        with open(p, "r", encoding="utf-8") as f:
            stand[k] = json.load(f)
    _stand_cache = stand
    return stand


def get_search_terms(keyword):
    stand = load_stand()
    aliases = stand["axes"].get("keyword_aliases", {}) if stand else {}
    terms = list(aliases.get(keyword, [keyword]))
    if keyword not in terms:
        terms.append(keyword)
    return terms


# ── 리뷰 로드: DB 우선, CSV 폴백 ──────────────────────────────

def load_reviews_from_db(keyword: str, debug_dump: dict | None = None) -> Optional[pd.DataFrame]:
    terms = get_search_terms(keyword)
    try:
        with get_conn() as conn:
            if debug_dump is not None:
                rows, kw_debug = get_reviews_for_profiler(conn, keyword, terms, return_debug=True)
                debug_dump["keyword_filter"] = kw_debug
                print(f"  🔎 키워드 필터: 후보 {kw_debug.get('candidate_restaurants', 0)}곳 "
                      f"→ 통과 {kw_debug.get('kept_restaurants', 0)}곳 "
                      f"| density 제외 {kw_debug.get('excluded_by_density', 0)} "
                      f"| 카테고리 제외 {kw_debug.get('excluded_by_category', 0)} "
                      f"| 이름보호 {kw_debug.get('protected_by_name', 0)}")
            else:
                rows = get_reviews_for_profiler(conn, keyword, terms)
    except Exception as e:
        print(f"  ⚠️ DB 로드 실패: {e}")
        return None
    if not rows:
        return None
    df = pd.DataFrame(rows)
    df["HasPicture"] = df.apply(
        lambda r: 1 if (str(r.get("ImageURLs", "")) not in ("", "nan")
                        or str(r.get("ImagePaths", "")) not in ("", "nan")) else 0,
        axis=1,
    )
    print(f"  DB: {len(df)}건 (식당 {df['Restaurant'].nunique()}개)")
    return df


def load_reviews_from_csv(keyword: str) -> Optional[pd.DataFrame]:
    """
    CSV 로더 — keyword 경계 강제. Food_profiler.load_from_csv와 동일 정책.
    파일명에 keyword가 들어간 CSV만 우선. 없으면 fallback으로 모든 CSV를 보되
    CrawlKeyword 컬럼이 있으면 그 값을 기준으로 다른 keyword 행을 제거.
    """
    terms = get_search_terms(keyword)
    all_csvs = [f for f in sorted(glob.glob("*.csv"))
                if not any(x in f for x in ["_dictionary", "_vectors", "_axes",
                                            "candidate", "eval_labels"])]
    scoped = [f for f in all_csvs
              if any(t in os.path.basename(f) for t in terms)]
    target_csvs = scoped if scoped else all_csvs

    frames = []
    for f in target_csvs:
        try:
            tmp = pd.read_csv(f, encoding="utf-8-sig")
            if "Review" in tmp.columns and "Restaurant" in tmp.columns:
                tmp["__source_csv__"] = os.path.basename(f)
                frames.append(tmp)
        except Exception:
            pass
    if not frames:
        return None
    df = pd.concat(frames, ignore_index=True)
    df = df.drop_duplicates(subset=["Restaurant", "Date", "Review"], keep="first")

    if "CrawlKeyword" in df.columns:
        df["CrawlKeyword"] = df["CrawlKeyword"].fillna("").astype(str)
        has_crawl_meta = df["CrawlKeyword"].str.strip() != ""
        crawl_match = df["CrawlKeyword"].apply(lambda v: any(t == v.strip() for t in terms))
        df = df[(~has_crawl_meta) | crawl_match].copy()

    def has_kw(row):
        text = (str(row.get("Menu", "")) + " " + str(row.get("Review", ""))).lower()
        return any(t.lower() in text for t in terms)
    df_filtered = df[df.apply(has_kw, axis=1)].copy()

    def check_img(row):
        for col in ["ImageURLs", "ImagePaths", "Image_Links", "review_images", "image_url"]:
            val = str(row.get(col, ""))
            if val and val not in ["", "nan", "[]"]:
                return 1
        return 0
    if "HasPicture" not in df_filtered.columns:
        df_filtered["HasPicture"] = df_filtered.apply(check_img, axis=1)
    return df_filtered


# ── CLIP ──────────────────────────────────────────────────────

_clip = {"model": None, "preprocess": None, "tokenizer": None, "device": None, "cache": {}}


def _get_device():
    import torch
    if torch.cuda.is_available():
        return torch.device("cuda")
    if hasattr(torch.backends, "mps") and torch.backends.mps.is_available():
        return torch.device("mps")
    return torch.device("cpu")


def _load_clip():
    if _clip["model"]:
        return True
    try:
        import open_clip
        device = _get_device()
        model, _, preprocess = open_clip.create_model_and_transforms(
            'ViT-B-32', pretrained='laion2b_s34b_b79k')
        model = model.to(device).eval()
        tokenizer = open_clip.get_tokenizer('ViT-B-32')
        _clip.update({"model": model, "preprocess": preprocess,
                      "tokenizer": tokenizer, "device": device})
        print(f"  ✅ CLIP (ViT-B-32, {device})")
        return True
    except ImportError:
        print("  ⚠️ open_clip 미설치 → simple 모드")
        return False
    except Exception as e:
        print(f"  ⚠️ CLIP 실패: {e}")
        return False


def _text_emb(prompt):
    import torch
    if prompt in _clip["cache"]:
        return _clip["cache"][prompt]
    tokens = _clip["tokenizer"]([prompt]).to(_clip["device"])
    with torch.no_grad():
        emb = _clip["model"].encode_text(tokens)
        emb = emb / emb.norm(dim=-1, keepdim=True)
    _clip["cache"][prompt] = emb
    return emb


def _image_cache_path(image_path: str) -> str:
    h = hashlib.sha1(image_path.encode("utf-8")).hexdigest()
    return os.path.join(CLIP_CACHE_DIR, f"{h}.npy")


def _image_emb(image_path: str, use_cache: bool = True):
    import torch
    from PIL import Image

    cache_p = _image_cache_path(image_path)
    if use_cache and os.path.exists(cache_p):
        try:
            arr = np.load(cache_p)
            return torch.from_numpy(arr).to(_clip["device"])
        except Exception:
            pass

    img = _clip["preprocess"](Image.open(image_path)).unsqueeze(0).to(_clip["device"])
    with torch.no_grad():
        ie = _clip["model"].encode_image(img)
        ie = ie / ie.norm(dim=-1, keepdim=True)

    if use_cache:
        try:
            os.makedirs(CLIP_CACHE_DIR, exist_ok=True)
            np.save(cache_p, ie.cpu().numpy())
        except Exception:
            pass
    return ie


def analyze_clip(image_path, pos_prompts, neg_prompts, use_cache=True):
    try:
        if not _load_clip():
            return 0.0
        ie = _image_emb(image_path, use_cache=use_cache)
        ps = [(ie @ _text_emb(p).T).squeeze().item() for p in pos_prompts]
        ns = [(ie @ _text_emb(p).T).squeeze().item() for p in neg_prompts]
        mp = (sum(ps) / len(ps)) if ps else 0
        mn = (sum(ns) / len(ns)) if ns else 0
        d = abs(mp) + abs(mn)
        return float(np.clip((mp - mn) / d, -1.0, 1.0)) if d > 0.01 else 0.0
    except Exception:
        return 0.0


def _ensure_local_image(image_path: str, image_url: str, image_id: int) -> tuple[str, bool, str | None]:
    """
    CLIP 입력으로 사용할 로컬 파일 경로를 보장한다.

    반환: (resolved_path, downloaded_from_url, error_reason)
      - 실제 파일이 있는 경로를 resolved_path로 반환
      - URL에서 새로 받았으면 downloaded_from_url=True
      - 실패 시 resolved_path="" 와 error_reason 반환
    """
    if image_path and os.path.exists(image_path):
        return image_path, False, None

    if not image_url:
        return "", False, "no_image_source"

    try:
        os.makedirs(IMAGE_CACHE_DIR, exist_ok=True)
    except Exception:
        pass

    # 파일명: image_id 우선, 없으면 URL의 sha1
    suffix = os.path.splitext(image_url.split("?")[0])[1].lower()
    if suffix not in (".jpg", ".jpeg", ".png", ".webp", ".bmp", ".gif"):
        suffix = ".jpg"
    if image_id:
        fname = f"{int(image_id)}{suffix}"
    else:
        h = hashlib.sha1(image_url.encode("utf-8")).hexdigest()
        fname = f"{h}{suffix}"
    cached = os.path.join(IMAGE_CACHE_DIR, fname)

    if os.path.exists(cached) and os.path.getsize(cached) > 0:
        return cached, False, None

    try:
        import requests
        resp = requests.get(image_url, timeout=15, stream=True)
        if resp.status_code != 200:
            return "", False, f"image_download_failed_http_{resp.status_code}"
        with open(cached, "wb") as f:
            for chunk in resp.iter_content(chunk_size=64 * 1024):
                if chunk:
                    f.write(chunk)
        if os.path.getsize(cached) <= 0:
            try:
                os.remove(cached)
            except Exception:
                pass
            return "", False, "image_download_failed_empty"
        return cached, True, None
    except Exception as e:
        return "", False, f"image_download_failed_{type(e).__name__}"


def image_embedding_np(image_path: str, use_cache: bool = True):
    """duplicate 검출용 — 이미지 임베딩을 numpy로 반환. 실패 시 None."""
    try:
        if not _load_clip():
            return None
        ie = _image_emb(image_path, use_cache=use_cache)
        return ie.cpu().numpy().reshape(-1)
    except Exception:
        return None


def clip_prompt_score(image_path: str, prompts: list[str], use_cache: bool = True) -> float:
    """
    이미지와 prompt 리스트의 평균 코사인 유사도. 음식 존재 / 비음식 패널티 등에 사용.
    [0, 1] 범위 (CLIP 유사도는 -1~1이지만 보통 0~0.4 사이)
    """
    try:
        if not _load_clip() or not prompts:
            return 0.0
        ie = _image_emb(image_path, use_cache=use_cache)
        sims = [(ie @ _text_emb(p).T).squeeze().item() for p in prompts]
        if not sims:
            return 0.0
        return float(max(0.0, sum(sims) / len(sims)))
    except Exception:
        return 0.0


def simple_sentiment(review_text):
    score = 0.0
    if isinstance(review_text, str):
        if any(w in review_text for w in ["맛있", "좋", "짱", "최고", "굿"]):
            score += 0.5
        if any(w in review_text for w in ["맛없", "별로", "최악", "비추"]):
            score -= 0.5
    return float(np.clip(score, -1.0, 1.0))


def make_prompts(axis_name, axis_info, keyword="food"):
    pos = axis_info.get("clip_prompt_pos", [])
    neg = axis_info.get("clip_prompt_neg", [])
    if not pos:
        kws = axis_info.get("positive_keywords", axis_info.get("positive", []))
        pos = [f"a photo of {kw} {keyword}" for kw in kws[:3]] or [f"a photo of {axis_name} {keyword}"]
    if not neg:
        kws = axis_info.get("negative_keywords", axis_info.get("negative", []))
        neg = [f"a photo of {kw} {keyword}" for kw in kws[:2]] or [f"a photo of {keyword} lacking {axis_name}"]
    return pos[:3], neg[:3]


# ── linked_text 벡터 (pseudo-label) ──────────────────────────

def _compute_linked_text(text: str, axes_config: dict, rules: dict) -> dict:
    """
    이미지에 연결된 리뷰 텍스트 → 축별 점수.
    Food_profiler.score_review의 간소화 버전 (절 분리, 부정/강조 적용).
    """
    try:
        from Food_profiler import score_review as fp_score
        # axes_config를 taste_axes 형식으로 변환
        taste = {
            n: {"positive": v.get("positive_keywords", v.get("positive", [])),
                "negative": v.get("negative_keywords", v.get("negative", []))}
            for n, v in axes_config.items() if not v.get("is_meta")
        }
        meta = {
            n: {"positive": v.get("positive_keywords", v.get("positive", [])),
                "negative": v.get("negative_keywords", v.get("negative", []))}
            for n, v in axes_config.items() if v.get("is_meta")
        }
        scores = fp_score({"Review": text}, taste, meta, rules, collect_evidence=False)
        return {a: float(s) for a, s in scores.items() if not a.startswith("_")}
    except Exception:
        return {}


# ── 이미지 벡터 생성 (pseudo-label 결합) ──────────────────────

def build_image_vectors(df, axes_config, keyword, mode, rules,
                        use_cache=True, debug_dump=None):
    img_reviews = df[df["HasPicture"] == 1].copy()
    if len(img_reviews) == 0:
        print("  ⚠️ 이미지 리뷰 없음")
        return {}, {}

    print(f"  📷 이미지 리뷰: {len(img_reviews)}건")

    rest_scores: dict[str, dict[str, list[tuple[float, float]]]] = defaultdict(lambda: defaultdict(list))
    rest_linked: dict[str, dict[str, list[float]]] = defaultdict(lambda: defaultdict(list))
    clip_calls = 0
    debug_records = []

    for _, row in img_reviews.iterrows():
        text = str(row.get("Review", "") or "")
        restaurant = str(row.get("Restaurant", "") or "")
        if not restaurant:
            continue

        # 이미지 path 후보: ImagePaths 또는 ImageURLs (URL은 다운로드된 경우만 사용)
        img_path = None
        paths_str = str(row.get("ImagePaths", "") or "")
        if paths_str and paths_str not in ("", "nan"):
            for p in paths_str.split(" | "):
                p = p.strip()
                if p and p not in ("nan",) and os.path.exists(p):
                    img_path = p
                    break

        # 연결된 리뷰 → linked_text_vector
        linked = _compute_linked_text(text, axes_config, rules)

        for ax_name, ax_info in axes_config.items():
            pos_kws = ax_info.get("positive_keywords", ax_info.get("positive", []))
            neg_kws = ax_info.get("negative_keywords", ax_info.get("negative", []))
            pos_hit = any(kw in text for kw in pos_kws) if pos_kws else False
            neg_hit = any(kw in text for kw in neg_kws) if neg_kws else False
            linked_score = float(linked.get(ax_name, 0.0))

            # 텍스트가 키워드를 가진 경우 OR linked가 강한 경우만 처리
            if not (pos_hit or neg_hit or abs(linked_score) > 0.3):
                continue

            # CLIP score
            clip_score = 0.0
            if mode == "clip" and img_path:
                ap, an = make_prompts(ax_name, ax_info, keyword)
                clip_score = analyze_clip(img_path, ap, an, use_cache=use_cache)
                clip_calls += 1
            else:
                clip_score = simple_sentiment(text)

            if clip_score == 0 and abs(linked_score) < 0.1:
                continue

            # 부호 결정
            if neg_hit and not pos_hit:
                clip_score = -abs(clip_score)
                linked_score = -abs(linked_score) if linked_score == 0 else linked_score

            # confidence: 텍스트와 CLIP이 같은 방향이면 1.0, 다르면 0.5, 텍스트 0이면 0.4
            if abs(linked_score) > 0.05:
                agreement = (clip_score * linked_score) >= 0
                conf = 1.0 if agreement else 0.5
                final_score = 0.6 * clip_score + 0.4 * linked_score
            else:
                conf = 0.4
                final_score = clip_score

            rest_scores[restaurant][ax_name].append((final_score, conf))
            rest_linked[restaurant][ax_name].append(linked_score)

            if debug_dump is not None and len(debug_records) < 50:
                debug_records.append({
                    "restaurant": restaurant,
                    "axis": ax_name,
                    "image_path": img_path,
                    "text_snippet": text[:80],
                    "clip_score": round(clip_score, 3),
                    "linked_text_score": round(linked_score, 3),
                    "confidence": round(conf, 3),
                    "final_score": round(final_score, 3),
                })

    # weighted mean
    img_vecs = {}
    for rest, axes in rest_scores.items():
        img_vecs[rest] = {}
        for ax, items in axes.items():
            scores = [s for s, _ in items]
            weights = [w for _, w in items]
            wsum = sum(weights) or 1.0
            img_vecs[rest][ax] = round(sum(s * w for s, w in items) / wsum, 4)

    if debug_dump is not None:
        debug_dump["image_pipeline_records"] = debug_records

    print(f"  ✅ {len(img_vecs)}개 식당 | CLIP {clip_calls}회")
    return img_vecs, dict(rest_linked)


# ── 동적 Fusion ───────────────────────────────────────────────

def _confidence(vec: dict) -> float:
    if not vec:
        return 0.0
    vals = [abs(float(v)) for k, v in vec.items() if not str(k).startswith("_")]
    if not vals:
        return 0.0
    return float(min((sum(vals) / len(vals)) * 2.0, 1.0))


def late_fusion_dynamic(text_vecs, img_vecs, dynamic=True):
    fused = {}
    weights_used = {}
    for rest, tv in text_vecs.items():
        iv = img_vecs.get(rest, {})
        if not iv or not any(abs(float(v)) > 0.001 for v in iv.values()):
            fused[rest] = dict(tv)
            fused[rest]["_img_coverage"] = 0.0
            weights_used[rest] = {"text": 1.0, "image": 0.0}
            continue

        if dynamic:
            t_conf = _confidence(tv)
            i_conf = _confidence(iv)
            tot = t_conf + i_conf
            alpha = max(0.5, min(0.9, 0.5 + 0.4 * (t_conf / tot))) if tot > 1e-6 else ALPHA_BASE
        else:
            alpha = ALPHA_BASE
        beta = 1.0 - alpha

        merged = {}
        for ax in set(list(tv.keys()) + list(iv.keys())):
            if str(ax).startswith("_"):
                continue
            t = float(tv.get(ax, 0.0))
            i = float(iv.get(ax, 0.0))
            if abs(t) > 0.001 and abs(i) > 0.001:
                merged[ax] = round(alpha * t + beta * i, 4)
            elif abs(t) > 0.001:
                merged[ax] = round(t, 4)
            else:
                merged[ax] = round(beta * i, 4)
        merged["_img_coverage"] = round(len(iv) / max(len(tv), 1), 2)
        fused[rest] = merged
        weights_used[rest] = {"text": round(alpha, 3), "image": round(beta, 3)}
    return fused, weights_used


# ── 대표 이미지 선정 ──────────────────────────────────────────

# ── keyword 경계: S3 URL prefix 검사 ─────────────────────────
#
# 크롤러가 `python Naver_place_crawler.py 김밥`로 수집한 이미지는 S3에
# `reviews/김밥/{filename}` prefix로 업로드되고, review_images.image_url에는
#   https://<bucket>.s3.<region>.amazonaws.com/reviews/%EA%B9%80%EB%B0%A5/{filename}
# 형태(한글이 URL 인코딩) 또는 raw 한글이 그대로 들어간 형태로 저장된다.
#
# 다른 keyword 폴더의 이미지가 한 식당의 review_images에 섞여 있을 수 있으므로,
# 현재 keyword의 `/reviews/<keyword>/` prefix를 가진 이미지만 후보로 허용한다.

def _keyword_prefix_variants(keyword: str) -> list[str]:
    """현재 keyword의 S3 prefix 후보(raw / URL-encoded). 둘 다 매칭."""
    if not keyword:
        return []
    raw = f"/reviews/{keyword}/"
    enc = f"/reviews/{urllib.parse.quote(keyword, safe='')}/"
    variants = [raw]
    if enc != raw:
        variants.append(enc)
    return variants


def _image_url_matches_keyword(image_url: str, keyword: str) -> tuple[bool, str]:
    """
    image_url이 현재 keyword의 S3 prefix를 갖는지 검사.

    반환: (matches, classification)
      - matches=True  : prefix 일치 또는 S3 prefix가 없는 데이터(local/dev)
      - classification: "match" | "mismatch_other_keyword" | "no_reviews_prefix"

    규칙:
      1) image_url에 `/reviews/`가 아예 없으면 → 로컬·dev 데이터로 간주, 통과(True)
      2) `/reviews/<keyword>/` (raw 또는 URL-encoded) 포함 → 통과
      3) `/reviews/`는 있는데 현재 keyword가 아니면 → 차단
    """
    if not image_url:
        return True, "no_reviews_prefix"  # URL 없으면 image_path로 대체될 수 있음 → 통과
    url = str(image_url)
    if "/reviews/" not in url:
        return True, "no_reviews_prefix"
    for v in _keyword_prefix_variants(keyword):
        if v in url:
            return True, "match"
    return False, "mismatch_other_keyword"


def _expand_images_per_review(df) -> list[dict]:
    """
    DataFrame의 각 row(리뷰)를 image_id 단위로 펼친다.
    DB get_reviews_for_profiler는 'ImageURLs'·'ImagePaths'·'ImageIDs'를 ' | ' join으로 내려준다.

    2차-A: 실제 review_images.image_id를 끝까지 유지한다 (review_image_scores 캐시
    키로 사용). ImageIDs 컬럼이 비어 있는 옛 데이터에서는 fallback id를 만들고
    fake_image_id_used=True 플래그로 표시 → 캐시 조회/저장을 skip한다.
    """
    out: list[dict] = []
    fake_count = 0
    for _, row in df[df.get("HasPicture") == 1].iterrows():
        rest = str(row.get("Restaurant", "") or "")
        rid_raw = row.get("review_id") if "review_id" in row else None
        try:
            rid = int(rid_raw) if rid_raw is not None else 0
        except Exception:
            rid = 0
        text = str(row.get("Review", "") or "")

        urls_str = str(row.get("ImageURLs", "") or "")
        paths_str = str(row.get("ImagePaths", "") or "")
        ids_str = str(row.get("ImageIDs", "") or "")
        urls = [u.strip() for u in urls_str.split(" | ") if u.strip() and u.strip() != "nan"]
        paths = [p.strip() for p in paths_str.split(" | ") if p.strip() and p.strip() != "nan"]
        real_ids: list[int] = []
        for tok in ids_str.split(" | "):
            tok = tok.strip()
            if not tok or tok == "nan":
                continue
            try:
                real_ids.append(int(tok))
            except (TypeError, ValueError):
                pass

        n = max(len(urls), len(paths))
        for i in range(n):
            url = urls[i] if i < len(urls) else ""
            path = paths[i] if i < len(paths) else ""
            if not url and not path:
                continue
            if i < len(real_ids):
                img_id = real_ids[i]
                fake = False
            else:
                img_id = (rid * 100 + i) if rid else (len(out) + 1)
                fake = True
                fake_count += 1
            out.append({
                "image_id":            img_id,
                "review_id":           rid,
                "restaurant":          rest,
                "review_text":         text,
                "image_url":           url,
                "image_path":          path,
                "source_order":        i,
                "fake_image_id_used":  fake,
            })
    if fake_count and out:
        print(f"  ⚠️  ImageIDs 컬럼이 비어 있어 {fake_count}장에 fake image_id 사용 "
              f"(review_image_scores 캐시는 fake id에 대해 skip)")
    return out


JJAMPPONG_IMAGE_POLICY_KEYWORDS = {"짬뽕", "해물짬뽕", "불짬뽕", "고기짬뽕"}
JJAMPPONG_IMAGE_POLICY_KEY = "짬뽕"

JAJANG_IMAGE_POLICY_KEYWORDS = {
    "짜장", "짜장면", "자장면", "간짜장", "유니짜장", "해물짜장", "쟁반짜장",
}
JAJANG_IMAGE_POLICY_KEY = "짜장"

# per-keyword strict image policy mapping. key → (debug_slug, alias_set)
# 새 keyword 추가 시 여기와 stand_rules.json 양쪽을 채우면 동일한 gate가 적용된다.
PER_KEYWORD_IMAGE_POLICY_MAP = {
    JJAMPPONG_IMAGE_POLICY_KEY: ("jjamppong", JJAMPPONG_IMAGE_POLICY_KEYWORDS),
    JAJANG_IMAGE_POLICY_KEY:    ("jajang",    JAJANG_IMAGE_POLICY_KEYWORDS),
}
PER_KEYWORD_IMAGE_POLICY_KEYS = set(PER_KEYWORD_IMAGE_POLICY_MAP.keys())

# 불맛 계열 축은 reject_score가 required+0.02 이상일 때만 컷오프 (시각적으로
# 다른 음식과 혼동되기 쉬워 일반 0.20 lower-bound로는 너무 자주 잘림).
_WOK_LIKE_AXES = {"온도", "불맛", "불맛웍향"}


def _representative_image_policy_key(keyword: str) -> str:
    """대표 이미지 선정에만 쓰는 이미지 정책 key."""
    keyword = str(keyword or "")
    for policy_key, (_slug, members) in PER_KEYWORD_IMAGE_POLICY_MAP.items():
        if keyword == policy_key or keyword in members:
            return policy_key
    return keyword


def _representative_image_policy_slug(policy_key: str) -> str:
    """debug JSON에서 keyword별 reject 카운터를 구분하기 위한 영문 slug."""
    info = PER_KEYWORD_IMAGE_POLICY_MAP.get(policy_key)
    return info[0] if info else str(policy_key or "")


def _policy_entry(block: dict, keyword: str, policy_key: str, default=None):
    if not isinstance(block, dict):
        return default
    if keyword in block:
        return block.get(keyword) or default
    if policy_key and policy_key in block:
        return block.get(policy_key) or default
    return block.get("_default", default) or default


def _policy_list(block: dict, keyword: str, policy_key: str) -> list:
    value = _policy_entry(block, keyword, policy_key, [])
    return list(value) if isinstance(value, list) else []


def _policy_dict(block: dict, keyword: str, policy_key: str) -> dict:
    value = _policy_entry(block, keyword, policy_key, {})
    return dict(value) if isinstance(value, dict) else {}


_DEFAULT_REPRESENTATIVE_GATE_THRESHOLDS = {
    "min_food_presence":               0.15,
    "min_category_presence":           0.15,
    "max_non_food_penalty":            0.22,
    "min_category_margin":             0.03,
    "wrong_food_threshold":            0.20,
    "wrong_food_margin_threshold":     0.08,
    "non_food_strict_threshold":       1.0,
    "non_food_strict_margin_threshold": 0.0,
    "required_score_min":              0.15,
    "required_weak_threshold":         0.17,
    "category_weak_threshold":         0.17,
    "logo_text_threshold":             0.20,
    "logo_text_category_weak":         0.20,
    "logo_text_non_food_min":          0.15,
    # 축별 preference override — axis_required/reject score를 final_score에 가산/감산.
    # 0.0이면 기존 동작 (final_score는 axis_clip + category - non_food - wrong 만 사용).
    "axis_required_bonus_weight":      0.0,
    "axis_reject_penalty_weight":      0.0,
}


def _resolve_gate_thresholds(image_filtering: dict, policy_key: str) -> dict:
    """policy_key 단위 representative gate 임계값 dict (없으면 _default)."""
    block = (image_filtering or {}).get("representative_gate_thresholds") or {}
    merged = dict(_DEFAULT_REPRESENTATIVE_GATE_THRESHOLDS)
    base = block.get("_default")
    if isinstance(base, dict):
        merged.update({k: v for k, v in base.items() if not str(k).startswith("_")})
    override = block.get(policy_key) if policy_key else None
    if isinstance(override, dict):
        merged.update({k: v for k, v in override.items() if not str(k).startswith("_")})
    return merged


def _per_keyword_representative_reject_reason(food: float, category: float,
                                              wrong: float, non_food: float,
                                              required_score: float,
                                              reject_group_scores: dict,
                                              low_required_key: str = "rejected_low_required",
                                              gate_thresholds: dict | None = None) -> str | None:
    """
    per-keyword 대표 이미지 후보 gate (짬뽕/짜장 공통).
    gate_thresholds로 keyword별 임계값을 주입한다 (짬뽕은 _default, 짜장은 더 엄격).
    """
    gt = dict(_DEFAULT_REPRESENTATIVE_GATE_THRESHOLDS)
    if gate_thresholds:
        gt.update({k: v for k, v in gate_thresholds.items() if not str(k).startswith("_")})

    min_food          = float(gt["min_food_presence"])
    min_cat           = float(gt["min_category_presence"])
    max_non           = float(gt["max_non_food_penalty"])
    min_margin        = float(gt["min_category_margin"])
    wrong_thr         = float(gt["wrong_food_threshold"])
    wrong_margin_thr  = float(gt["wrong_food_margin_threshold"])
    non_strict_thr    = float(gt["non_food_strict_threshold"])
    non_strict_marg   = float(gt["non_food_strict_margin_threshold"])
    required_min      = float(gt["required_score_min"])
    required_weak     = float(gt["required_weak_threshold"])
    category_weak     = float(gt["category_weak_threshold"])
    logo_thr          = float(gt["logo_text_threshold"])
    logo_cat_weak     = float(gt["logo_text_category_weak"])
    logo_non_min      = float(gt["logo_text_non_food_min"])

    category_margin = float(category) - float(wrong)
    required_is_weak = float(required_score) < required_weak

    empty_score = float(reject_group_scores.get("empty_bowl_or_leftover", 0.0))
    side_score = float(reject_group_scores.get("side_dish_only", 0.0))
    table_score = float(reject_group_scores.get("table_or_drinks", 0.0))
    wrong_group_score = float(reject_group_scores.get("wrong_food", 0.0))
    logo_score = float(reject_group_scores.get("logo_or_text", 0.0))

    # 로고/메뉴/포장지 우세 — 음식이 약하거나 비음식 점수가 높으면 우선 컷.
    if logo_score >= logo_thr and (
        required_is_weak or category < logo_cat_weak or non_food >= logo_non_min
    ):
        return "rejected_logo_or_text"

    if empty_score >= 0.20 and (required_is_weak or category < category_weak):
        return "rejected_empty_bowl_or_leftover"
    if side_score >= 0.20 and (required_is_weak or category < category_weak):
        return "rejected_side_dish_only"
    if table_score >= 0.20 and (required_is_weak or non_food > 0.20):
        return "rejected_table_or_drinks"
    if max(float(wrong), wrong_group_score) >= wrong_thr and category_margin < wrong_margin_thr:
        return "rejected_wrong_food"

    # non_food strict (짜장 등): non_food>=non_strict_thr AND margin<non_strict_marg → 비음식
    if non_food >= non_strict_thr and category_margin < non_strict_marg:
        if logo_score >= 0.15:
            return "rejected_logo_or_text"
        return "rejected_table_or_drinks" if table_score >= 0.18 else "rejected_non_food"

    if non_food > max_non:
        if logo_score >= 0.15:
            return "rejected_logo_or_text"
        return "rejected_table_or_drinks" if table_score >= 0.18 else "rejected_non_food"
    if food < min_food or category < min_cat or category_margin < min_margin:
        return low_required_key
    if required_score < required_min:
        return low_required_key
    return None


def _per_keyword_axis_reject_reason(axis_name: str, required_score: float,
                                    reject_score: float,
                                    has_required_prompts: bool) -> str | None:
    if has_required_prompts and required_score < 0.15:
        return f"rejected_axis_required_{axis_name}"
    if reject_score >= 0.20:
        if axis_name in _WOK_LIKE_AXES:
            if reject_score >= max(required_score + 0.02, 0.20):
                return f"rejected_axis_reject_{axis_name}"
        elif (not has_required_prompts) or required_score < 0.17:
            return f"rejected_axis_reject_{axis_name}"
    return None


def extract_rep_images(df, axes_config, img_vecs, rest_linked, keyword,
                        rules: dict, top_n: int = 6, use_cache: bool = True,
                        debug_dump: dict | None = None,
                        refresh_image_scores: bool = False,
                        max_score_candidates: int = 0):
    """
    대표 이미지 선정 v3 — image_id 단위 평가 + category/wrong_food/duplicate.

    각 image_id에 대해 keyword 단위 점수(food_presence/non_food/category/wrong_food)는 1회만 계산.
    각 image × axis 조합으로 image_axis_clip_score 계산 후 최종 점수로 선정.
    """
    image_filtering = (rules or {}).get("image_filtering", {})
    food_prompts = image_filtering.get("food_presence_prompts") or []
    non_food_prompts = image_filtering.get("non_food_penalty_prompts") or []
    cat_map = image_filtering.get("category_presence_prompts") or {}
    wrong_map = image_filtering.get("wrong_food_prompts") or {}
    image_policy_key = _representative_image_policy_key(keyword)
    uses_per_keyword_image_policy = image_policy_key in PER_KEYWORD_IMAGE_POLICY_KEYS
    policy_slug = _representative_image_policy_slug(image_policy_key)
    low_required_key = f"rejected_low_{policy_slug}_required" if policy_slug else "rejected_low_required"
    gate_thresholds_for_kw = _resolve_gate_thresholds(image_filtering, image_policy_key)
    cat_prompts = _policy_list(cat_map, keyword, image_policy_key) or cat_map.get("_default") or []
    wrong_prompts = _policy_list(wrong_map, keyword, image_policy_key) or wrong_map.get("_default") or []

    # 짬뽕/짜장처럼 사진 특화 prompt가 필요한 keyword를 위해 image_axis_visual_prompts /
    # image_axis_priority / image_axis_fallback 을 읽어둔다. 미정의 keyword는 기존
    # 동작(stand_lexicon clip_prompt_pos/neg + 점수 기반 정렬) 그대로.
    visual_prompts_map = _policy_dict(
        image_filtering.get("image_axis_visual_prompts") or {}, keyword, image_policy_key
    )
    priority_axes_for_kw = _policy_list(
        image_filtering.get("image_axis_priority") or {}, keyword, image_policy_key
    )
    fallback_axes_for_kw = _policy_list(
        image_filtering.get("image_axis_fallback") or {}, keyword, image_policy_key
    )
    blocked_axes_for_kw = set()
    for block_name in ("representative_blocked_axes", "image_axis_blocked", "image_axis_blocked_axes"):
        blocked_axes_for_kw.update(_policy_list(image_filtering.get(block_name) or {}, keyword, image_policy_key))
    required_prompts_for_kw = (
        _policy_list(image_filtering.get("representative_required_prompts") or {}, keyword, image_policy_key)
        or _policy_list(image_filtering.get("image_candidate_required_prompts") or {}, keyword, image_policy_key)
    )
    reject_prompts_for_kw = (
        _policy_list(image_filtering.get("representative_reject_prompts") or {}, keyword, image_policy_key)
        or _policy_list(image_filtering.get("image_candidate_reject_prompts") or {}, keyword, image_policy_key)
    )
    reject_prompt_groups_for_kw = _policy_dict(
        image_filtering.get("representative_reject_prompt_groups") or {}, keyword, image_policy_key
    )
    axis_required_prompts_for_kw = _policy_dict(
        image_filtering.get("representative_axis_required_prompts") or {}, keyword, image_policy_key
    )
    axis_reject_prompts_for_kw = _policy_dict(
        image_filtering.get("representative_axis_reject_prompts") or {}, keyword, image_policy_key
    )
    safe_axes_for_kw = []
    if uses_per_keyword_image_policy:
        for ax in list(priority_axes_for_kw) + list(fallback_axes_for_kw):
            if ax not in blocked_axes_for_kw and ax not in safe_axes_for_kw:
                safe_axes_for_kw.append(ax)
    image_axis_policy_debug = {
        "image_axis_policy_key": image_policy_key,
        "image_axis_preferred_axes": list(priority_axes_for_kw),
        "image_axis_fallback_axes": list(fallback_axes_for_kw),
        "image_axis_blocked_axes": sorted(blocked_axes_for_kw),
        "image_axis_gate_thresholds": dict(gate_thresholds_for_kw),
        "rep_score_gate_total": 0,
        "rep_score_gate_passed": 0,
        "rejected_logo_or_text": 0,
        "rejected_empty_bowl_or_leftover": 0,
        "rejected_side_dish_only": 0,
        "rejected_table_or_drinks": 0,
        "rejected_wrong_food": 0,
        "rejected_non_food": 0,
        low_required_key: 0,
        "rejected_blocked_axis": 0,
        "selected_safe_axes": [],
        "selected_count": 0,
    }

    th = image_filtering.get("thresholds", {}) or {}
    min_food   = float(th.get("min_food_presence", 0.18))
    max_non    = float(th.get("max_non_food_penalty", 0.18))
    min_cat    = float(th.get("min_category_presence", 0.15))
    max_wrong  = float(th.get("max_wrong_food_penalty", 0.20))
    w_food     = float(th.get("food_presence_weight", 0.0))
    w_non      = float(th.get("non_food_penalty_weight", 0.6))
    w_cat      = float(th.get("category_bonus_weight", 0.30))
    w_wrong    = float(th.get("wrong_food_weight", 0.70))
    w_link     = float(th.get("linked_text_weight", 0.15))
    dup_thresh = float(th.get("duplicate_threshold", 0.92))
    w_dup      = float(th.get("duplicate_penalty_weight", 0.30))
    max_per_review = int(th.get("max_per_review", 1))
    max_per_review_loose = int(th.get("max_per_review_loose", 2))
    max_per_restaurant = int(th.get("max_per_restaurant", 2))
    max_per_rest_axis = int(th.get("max_per_restaurant_axis", 1))
    fallback_target = int(th.get("fallback_target_count", top_n))
    fallback_min_combined = float(th.get("fallback_min_combined", 0.10))

    # per-keyword axis preference 가중치 (짜장의 면컨디션↔불맛웍향 같은 혼동 방지용).
    # _default는 0.0이므로 짬뽕 등 기존 keyword는 영향 없음.
    w_axis_req = float(gate_thresholds_for_kw.get("axis_required_bonus_weight", 0.0))
    w_axis_rej = float(gate_thresholds_for_kw.get("axis_reject_penalty_weight", 0.0))

    # image_id 단위로 펼치기
    images_all = _expand_images_per_review(df)
    if not images_all:
        if debug_dump is not None:
            debug_dump.update(image_axis_policy_debug)
        return []

    # ── (0) keyword 경계 강제: S3 prefix `/reviews/<keyword>/` 검사 ──
    # 다른 keyword 폴더의 이미지가 같은 식당 review_images에 섞여 있을 수 있으므로,
    # CLIP 평가 전에 prefix가 어긋난 이미지는 후보에서 완전히 제거한다.
    images: list[dict] = []
    skipped_keyword_mismatch: list[dict] = []
    for im in images_all:
        ok, classification = _image_url_matches_keyword(im.get("image_url", ""), keyword)
        if ok:
            images.append(im)
        else:
            skipped_keyword_mismatch.append({
                "image_id": im.get("image_id"),
                "review_id": im.get("review_id"),
                "restaurant_name": im.get("restaurant", ""),
                "image_url": im.get("image_url", ""),
                "expected_prefix_variants": _keyword_prefix_variants(keyword),
                "classification": classification,  # 항상 "mismatch_other_keyword"
                "excluded_reason": "keyword_prefix_mismatch",
            })

    if skipped_keyword_mismatch:
        print(f"  🚧 keyword 경계 컷오프(다른 폴더의 S3 이미지): {len(skipped_keyword_mismatch)}장")
    image_axis_policy_debug["rep_score_gate_total"] = len(images)
    if not images:
        if debug_dump is not None:
            debug_dump["skipped_keyword_mismatch_count"] = len(skipped_keyword_mismatch)
            debug_dump["skipped_keyword_mismatch"] = skipped_keyword_mismatch[:200]
            debug_dump.update(image_axis_policy_debug)
        return []

    # ── (0.7) 후보 상한 (옵션 --max-score-candidates) ──
    # 기본 0 = 무제한 → 기존 동작 유지. 사용자가 양수를 주면 식당 round-robin sampling.
    if max_score_candidates and len(images) > max_score_candidates > 0:
        from collections import defaultdict as _dd
        by_rest: dict = _dd(list)
        for im in images:
            by_rest[im.get("restaurant", "") or "(unknown)"].append(im)
        sampled: list[dict] = []
        rest_keys = list(by_rest.keys())
        iters = {r: iter(lst) for r, lst in by_rest.items()}
        while len(sampled) < max_score_candidates and rest_keys:
            still: list = []
            for r in rest_keys:
                try:
                    sampled.append(next(iters[r]))
                    still.append(r)
                    if len(sampled) >= max_score_candidates:
                        break
                except StopIteration:
                    continue
            rest_keys = still
        print(f"  ✂️  --max-score-candidates 적용: {len(images)}장 → {len(sampled)}장")
        images = sampled
        image_axis_policy_debug["rep_score_gate_total"] = len(images)

    # ── (1) image_id 단위로 keyword-level CLIP 점수 1회 캐시 ──
    # 2차-A: review_image_scores에서 (image_id, keyword) 캐시를 bulk 조회.
    # 캐시 hit이면 CLIP prompt 호출을 skip하고 저장된 score를 그대로 사용.
    # fake_image_id_used 인 행은 캐시 키로 부적합 (review_images.image_id가 실제가 아님) → 항상 miss.
    cache_keyword = str(keyword)  # 이번 단계는 raw keyword를 캐시 키로 사용
    cache_hits = 0
    cache_misses = 0
    score_cache: dict[int, dict] = {}
    candidate_image_ids = [im["image_id"] for im in images
                            if not im.get("fake_image_id_used")]
    if not refresh_image_scores and candidate_image_ids:
        try:
            with get_conn() as conn:
                score_cache = bulk_get_review_image_scores(conn, candidate_image_ids, cache_keyword)
            print(f"  🧠 review_image_scores cache: hit={len(score_cache)} miss={len(candidate_image_ids) - len(score_cache)} refresh={refresh_image_scores}")
        except Exception as e:
            print(f"  ⚠️  review_image_scores 조회 실패 (캐시 skip): {type(e).__name__}: {e}")
            score_cache = {}
    elif refresh_image_scores:
        print(f"  🔄 review_image_scores cache: refresh=True (캐시 무시하고 모두 재평가)")

    print(f"  🔍 후보 이미지 {len(images)}장 (image_id 단위 평가) "
          f"| keyword 경계 컷오프 {len(skipped_keyword_mismatch)}장")
    img_keyword_meta: dict[int, dict] = {}
    img_embeddings: dict[int, "np.ndarray"] = {}
    download_count = 0
    new_score_records: list[dict] = []   # 캐시 miss → bulk upsert 대상

    for im in images:
        img_id = im["image_id"]
        is_fake = bool(im.get("fake_image_id_used"))
        cached_row = None if is_fake else score_cache.get(img_id)

        # 로컬 파일 보장 — image_path → 없으면 image_url에서 다운로드.
        # 캐시 hit이라도 embedding(dup 검출용)을 위해 로컬 파일은 필요.
        resolved_path, downloaded, err = _ensure_local_image(
            im.get("image_path", ""), im.get("image_url", ""), im.get("image_id", 0)
        )
        im["resolved_image_path"] = resolved_path
        im["downloaded_from_url"] = downloaded
        if downloaded:
            download_count += 1

        if not resolved_path:
            img_keyword_meta[img_id] = {
                "food": 0.0, "non_food": 0.0, "category": 0.0, "wrong": 0.0,
                "category_margin": 0.0,
                "required": 0.0,
                "reject": 0.0,
                "reject_group_scores": {},
                "ok": False, "excluded_reason": err or "no_image_source",
            }
            continue

        if cached_row is not None and not refresh_image_scores:
            # 캐시 hit — CLIP prompt 호출 skip, 저장된 점수 사용
            food = float(cached_row.get("food_presence_score") or 0.0)
            non_food = float(cached_row.get("non_food_penalty") or 0.0)
            category = float(cached_row.get("category_presence_score") or 0.0)
            wrong = float(cached_row.get("wrong_food_penalty") or 0.0)
            cache_hits += 1
            is_cache_hit = True
        else:
            # 캐시 miss — 기존 CLIP prompt 평가
            food = clip_prompt_score(resolved_path, food_prompts, use_cache=use_cache) if food_prompts else 0.0
            non_food = clip_prompt_score(resolved_path, non_food_prompts, use_cache=use_cache) if non_food_prompts else 0.0
            category = clip_prompt_score(resolved_path, cat_prompts, use_cache=use_cache) if cat_prompts else 1.0
            wrong = clip_prompt_score(resolved_path, wrong_prompts, use_cache=use_cache) if wrong_prompts else 0.0
            cache_misses += 1
            is_cache_hit = False

        category_margin = float(category) - float(wrong)
        required_score = 0.0
        reject_score = 0.0
        reject_group_scores: dict[str, float] = {}
        if uses_per_keyword_image_policy:
            if required_prompts_for_kw:
                required_score = clip_prompt_score(
                    resolved_path, required_prompts_for_kw, use_cache=use_cache
                )
            else:
                required_score = float(category)
            if reject_prompt_groups_for_kw:
                for group_name, prompts in reject_prompt_groups_for_kw.items():
                    if isinstance(prompts, list) and prompts:
                        reject_group_scores[group_name] = clip_prompt_score(
                            resolved_path, prompts, use_cache=use_cache
                        )
                reject_score = max(reject_group_scores.values(), default=0.0)
            elif reject_prompts_for_kw:
                reject_score = clip_prompt_score(
                    resolved_path, reject_prompts_for_kw, use_cache=use_cache
                )

        # excluded_reason 표준 명칭 (debug JSON / API 응답 일관성):
        #   rejected_non_food            — 메뉴판/영수증/매장 외관/사람 등 음식이 아닌 사진
        #   rejected_low_foodness        — 음식 자체가 거의 보이지 않음 (food/category 둘 다 낮음)
        #   rejected_wrong_visual        — 현재 keyword와 다른 음식이 잡힘
        ok, excluded = True, None
        if uses_per_keyword_image_policy:
            excluded = _per_keyword_representative_reject_reason(
                food, category, wrong, non_food, required_score, reject_group_scores,
                low_required_key=low_required_key,
                gate_thresholds=gate_thresholds_for_kw,
            )
            ok = excluded is None
            if ok:
                image_axis_policy_debug["rep_score_gate_passed"] += 1
            elif excluded == "rejected_logo_or_text":
                image_axis_policy_debug["rejected_logo_or_text"] += 1
            elif excluded == "rejected_empty_bowl_or_leftover":
                image_axis_policy_debug["rejected_empty_bowl_or_leftover"] += 1
            elif excluded == "rejected_side_dish_only":
                image_axis_policy_debug["rejected_side_dish_only"] += 1
            elif excluded == "rejected_table_or_drinks":
                image_axis_policy_debug["rejected_table_or_drinks"] += 1
            elif excluded == "rejected_wrong_food":
                image_axis_policy_debug["rejected_wrong_food"] += 1
            elif excluded == "rejected_non_food":
                image_axis_policy_debug["rejected_non_food"] += 1
            elif excluded == low_required_key:
                image_axis_policy_debug[low_required_key] += 1
        else:
            if non_food > max_non:
                ok, excluded = False, "rejected_non_food"
            elif food < min_food:
                ok, excluded = False, "rejected_low_foodness"
            elif wrong > max_wrong:
                ok, excluded = False, "rejected_wrong_visual"
            elif category < min_cat:
                ok, excluded = False, "rejected_low_foodness"

        img_keyword_meta[img_id] = {
            "food": food, "non_food": non_food, "category": category, "wrong": wrong,
            "category_margin": category_margin,
            "required": required_score,
            "reject": reject_score,
            "reject_group_scores": reject_group_scores,
            "ok": ok, "excluded_reason": excluded,
        }

        # 캐시 miss는 새 record로 큐에 추가 (fake image_id는 캐시 키 부적합 → skip)
        if (not is_cache_hit) and (not is_fake):
            new_score_records.append({
                "image_id": int(img_id),
                "keyword":  cache_keyword,
                "resolved_category_key": cache_keyword,
                "food_presence_score":     float(food),
                "category_presence_score": float(category),
                "wrong_food_penalty":      float(wrong),
                "non_food_penalty":        float(non_food),
                "clip_debug": {
                    "image_url":               im.get("image_url", ""),
                    "image_path":              im.get("image_path", ""),
                    "review_id":               im.get("review_id"),
                    "restaurant_name":         im.get("restaurant", ""),
                    "raw_keyword":             str(keyword),
                    "resolved_category_key":   cache_keyword,
                    "food_presence_score":     round(float(food), 4),
                    "category_presence_score": round(float(category), 4),
                    "wrong_food_penalty":      round(float(wrong), 4),
                    "non_food_penalty":        round(float(non_food), 4),
                    "category_margin":         round(category_margin, 4),
                    "representative_required_score": round(float(required_score), 4),
                    "representative_reject_score":   round(float(reject_score), 4),
                    "representative_reject_group_scores": {
                        k: round(float(v), 4) for k, v in reject_group_scores.items()
                    },
                    "excluded_reason":         excluded or "",
                    "used_category_prompts_count": len(cat_prompts or []),
                    "used_wrong_prompts_count":    len(wrong_prompts or []),
                    "fake_image_id_used":      False,
                    "created_by":              "Multimodal_profiler",
                },
            })

        if ok:
            emb = image_embedding_np(resolved_path, use_cache=use_cache)
            if emb is not None:
                img_embeddings[img_id] = emb

    if download_count:
        print(f"  ⬇️  S3/URL 다운로드: {download_count}장 (cache={IMAGE_CACHE_DIR})")

    # ── (1.5) 신규 score record bulk upsert ──
    score_upserted = 0
    score_upsert_failed = 0
    if new_score_records:
        try:
            with get_conn() as conn:
                score_upserted, score_upsert_failed = upsert_review_image_scores_bulk(
                    conn, new_score_records
                )
            print(f"  💾 review_image_scores upsert: {score_upserted}개 저장 "
                  f"(실패 {score_upsert_failed}개)")
        except Exception as e:
            # 권한·연결 등 outer 실패 — 전체 Multimodal_profiler는 계속 진행.
            print(f"  ⚠️  review_image_scores upsert 실패 (캐시 저장 skip): "
                  f"{type(e).__name__}: {e}")
            score_upsert_failed = len(new_score_records)

    # ── debug_dump에 캐시 통계 노출 ──
    if debug_dump is not None:
        debug_dump["score_cache"] = {
            "total_image_candidates":   len(images),
            "candidate_image_ids":      len(candidate_image_ids),
            "score_cache_hits":         cache_hits,
            "score_cache_misses":       cache_misses,
            "newly_scored_images":      len(new_score_records),
            "score_upserted_images":    score_upserted,
            "score_upsert_failed_images": score_upsert_failed,
            "fake_image_id_used_count": sum(1 for im in images if im.get("fake_image_id_used")),
            "refresh_image_scores":     bool(refresh_image_scores),
            "cache_keyword":            cache_keyword,
        }

    # ── (2) image × axis 평가 ──
    candidates_per_axis: dict[str, list[dict]] = defaultdict(list)
    all_records: list[dict] = []   # debug 풀 기록

    if uses_per_keyword_image_policy:
        fallback_axis_set = set(fallback_axes_for_kw)
        taste_axes_only = [
            (n, info) for n, info in axes_config.items()
            if (not info.get("is_meta")) or n in fallback_axis_set
        ]
    else:
        taste_axes_only = [(n, info) for n, info in axes_config.items() if not info.get("is_meta")]
    print(f"  ⚙️  축 {len(taste_axes_only)}개 × 이미지 {sum(1 for m in img_keyword_meta.values() if m['ok'])}장 평가")

    blocked_axis_names_seen: set[str] = set()
    for ax_name, ax_info in taste_axes_only:
        if uses_per_keyword_image_policy and ax_name in blocked_axes_for_kw:
            if ax_name not in blocked_axis_names_seen:
                image_axis_policy_debug["rejected_blocked_axis"] += 1
                blocked_axis_names_seen.add(ax_name)
            continue
        if uses_per_keyword_image_policy and safe_axes_for_kw and ax_name not in safe_axes_for_kw:
            continue

        ax_pos = ax_info.get("clip_prompt_pos") or []
        ax_neg = ax_info.get("clip_prompt_neg") or []
        ax_label = ax_info.get("label") or ax_name
        axis_required_prompts = []
        axis_reject_prompts = []
        if uses_per_keyword_image_policy:
            axis_required_prompts = axis_required_prompts_for_kw.get(ax_name) or []
            axis_reject_prompts = axis_reject_prompts_for_kw.get(ax_name) or []

        # image_axis_visual_prompts override — keyword × axis 사진 특화 prompt가 있으면
        # stand_lexicon의 일반 axis prompt 대신 사용. neg를 정의 안 했으면 기존 neg 유지.
        _vp = visual_prompts_map.get(ax_name) if isinstance(visual_prompts_map, dict) else None
        if isinstance(_vp, dict):
            if _vp.get("pos"):
                ax_pos = list(_vp.get("pos") or [])
            if _vp.get("neg"):
                ax_neg = list(_vp.get("neg") or [])

        for im in images:
            meta = img_keyword_meta[im["image_id"]]
            if not meta["ok"]:
                # 컷오프된 이미지도 1번만 디버그에 기록 (axis 무관)
                if not any(r["image_id"] == im["image_id"] and r["axis"] == "_excluded_"
                           for r in all_records):
                    all_records.append({
                        "image_id": im["image_id"], "review_id": im["review_id"],
                        "restaurant_name": im["restaurant"], "source_order": im["source_order"],
                        "axis": "_excluded_", "axis_label": "(컷오프)",
                        "image_url": im["image_url"],
                        "original_image_path": im.get("image_path", ""),
                        "resolved_image_path": im.get("resolved_image_path", ""),
                        "downloaded_from_url": bool(im.get("downloaded_from_url", False)),
                        "food_presence_score": round(meta["food"], 4),
                        "category_presence_score": round(meta["category"], 4),
                        "category_margin": round(meta.get("category_margin", 0.0), 4),
                        "representative_required_score": round(meta.get("required", 0.0), 4),
                        "representative_reject_score": round(meta.get("reject", 0.0), 4),
                        "representative_reject_group_scores": {
                            k: round(float(v), 4)
                            for k, v in (meta.get("reject_group_scores") or {}).items()
                        },
                        "image_axis_clip_score": 0.0,
                        "axis_match_score": 0.0,
                        "linked_review_axis_score": 0.0,
                        "non_food_penalty": round(meta["non_food"], 4),
                        "wrong_food_penalty": round(meta["wrong"], 4),
                        "duplicate_penalty": 0.0,
                        "final_score": None,
                        "selected": False,
                        "excluded_reason": meta["excluded_reason"],
                    })
                continue

            # 이미지×축 CLIP — resolved_image_path 사용
            local = im.get("resolved_image_path", "")
            axis_required_score = 0.0
            axis_reject_score = 0.0
            axis_gate_reason = None
            if uses_per_keyword_image_policy and (axis_required_prompts or axis_reject_prompts):
                if axis_required_prompts:
                    axis_required_score = clip_prompt_score(
                        local, axis_required_prompts, use_cache=use_cache
                    )
                if axis_reject_prompts:
                    axis_reject_score = clip_prompt_score(
                        local, axis_reject_prompts, use_cache=use_cache
                    )
                axis_gate_reason = _per_keyword_axis_reject_reason(
                    ax_name, axis_required_score, axis_reject_score, bool(axis_required_prompts)
                )
            if axis_gate_reason:
                all_records.append({
                    "image_id": im["image_id"], "review_id": im["review_id"],
                    "restaurant_name": im["restaurant"], "source_order": im["source_order"],
                    "axis": ax_name, "axis_label": ax_label,
                    "image_url": im["image_url"],
                    "original_image_path": im.get("image_path", ""),
                    "resolved_image_path": local,
                    "downloaded_from_url": bool(im.get("downloaded_from_url", False)),
                    "food_presence_score": round(meta["food"], 4),
                    "category_presence_score": round(meta["category"], 4),
                    "category_margin": round(meta.get("category_margin", 0.0), 4),
                    "representative_required_score": round(meta.get("required", 0.0), 4),
                    "representative_reject_score": round(meta.get("reject", 0.0), 4),
                    "representative_reject_group_scores": {
                        k: round(float(v), 4)
                        for k, v in (meta.get("reject_group_scores") or {}).items()
                    },
                    "axis_required_score": round(axis_required_score, 4),
                    "axis_reject_score": round(axis_reject_score, 4),
                    "image_axis_clip_score": 0.0,
                    "axis_match_score": 0.0,
                    "linked_review_axis_score": 0.0,
                    "non_food_penalty": round(meta["non_food"], 4),
                    "wrong_food_penalty": round(meta["wrong"], 4),
                    "duplicate_penalty": 0.0,
                    "final_score": None,
                    "selected": False,
                    "excluded_reason": axis_gate_reason,
                })
                continue
            if ax_pos or ax_neg:
                axis_clip = analyze_clip(local, ax_pos[:3], ax_neg[:3], use_cache=use_cache)
            else:
                axis_clip = 0.0

            # linked review (해당 리뷰 텍스트만)
            linked_dict = _compute_linked_text(im["review_text"], axes_config, rules)
            linked_score = float(linked_dict.get(ax_name, 0.0))

            # axis_required/reject bonus — 짜장의 면컨디션↔불맛웍향 같은 축 혼동 방지용.
            # _default(=짬뽕 등)는 가중치 0이라 영향 없음.
            axis_req_bonus = (w_axis_req * axis_required_score) if uses_per_keyword_image_policy else 0.0
            axis_rej_penalty = (w_axis_rej * axis_reject_score) if uses_per_keyword_image_policy else 0.0

            final_score = (
                axis_clip
                + w_cat   * meta["category"]
                + w_link  * abs(linked_score)
                - w_non   * meta["non_food"]
                - w_wrong * meta["wrong"]
                + axis_req_bonus
                - axis_rej_penalty
            )

            cand = {
                "image_id":      im["image_id"],
                "review_id":     im["review_id"],
                "restaurant":    im["restaurant"],
                "source_order":  im["source_order"],
                "image_src":     im["image_url"],
                "image_path":    im.get("image_path", ""),
                "resolved_image_path": local,
                "downloaded_from_url": bool(im.get("downloaded_from_url", False)),
                "axis":          ax_name,
                "label":         ax_label,
                "score":         round(float(final_score), 4),
                "review_snippet": (im["review_text"] or "")[:120],
                "_axis_clip":    round(axis_clip, 4),
                "_category":     round(meta["category"], 4),
                "_food":         round(meta["food"], 4),
                "_non_food":     round(meta["non_food"], 4),
                "_wrong":        round(meta["wrong"], 4),
                "_category_margin": round(meta.get("category_margin", 0.0), 4),
                "_required":     round(meta.get("required", 0.0), 4),
                "_reject":       round(meta.get("reject", 0.0), 4),
                "_axis_required": round(axis_required_score, 4),
                "_axis_reject":  round(axis_reject_score, 4),
                "_axis_req_bonus": round(axis_req_bonus, 4),
                "_axis_rej_penalty": round(axis_rej_penalty, 4),
                "_linked":       round(linked_score, 4),
            }
            candidates_per_axis[ax_name].append(cand)

            all_records.append({
                "image_id": im["image_id"], "review_id": im["review_id"],
                "restaurant_name": im["restaurant"], "source_order": im["source_order"],
                "axis": ax_name, "axis_label": ax_label,
                "image_url": im["image_url"],
                "original_image_path": im.get("image_path", ""),
                "resolved_image_path": local,
                "downloaded_from_url": bool(im.get("downloaded_from_url", False)),
                "food_presence_score": round(meta["food"], 4),
                "category_presence_score": round(meta["category"], 4),
                "category_margin": round(meta.get("category_margin", 0.0), 4),
                "representative_required_score": round(meta.get("required", 0.0), 4),
                "representative_reject_score": round(meta.get("reject", 0.0), 4),
                "representative_reject_group_scores": {
                    k: round(float(v), 4)
                    for k, v in (meta.get("reject_group_scores") or {}).items()
                },
                "axis_required_score": round(axis_required_score, 4),
                "axis_reject_score": round(axis_reject_score, 4),
                "image_axis_clip_score": round(axis_clip, 4),
                "axis_match_score": round(axis_clip, 4),
                "linked_review_axis_score": round(linked_score, 4),
                "non_food_penalty": round(meta["non_food"], 4),
                "wrong_food_penalty": round(meta["wrong"], 4),
                "duplicate_penalty": 0.0,
                "final_score": round(float(final_score), 4),
                "selected": False,
                "excluded_reason": None,
            })

    # ── (3) 같은 review_id 내 후보 풀 상한 ──
    # 풀 단계에서는 loose cap(=2)까지 허용하고, 선정 단계에서 strict cap(=1)을 적용.
    # fallback 단계에서 loose cap을 허용해 같은 review에서 1장 추가 가능.
    for ax_name, cands in candidates_per_axis.items():
        cands.sort(key=lambda c: -c["score"])
        per_review_count: dict[int, int] = defaultdict(int)
        kept = []
        for c in cands:
            rid = c["review_id"] or 0
            if rid and per_review_count[rid] >= max_per_review_loose:
                continue
            per_review_count[rid] += 1
            kept.append(c)
        candidates_per_axis[ax_name] = kept

    # ── (4) 축별 선정 ──
    # 같은 (restaurant, axis) 1장 (max_per_rest_axis), 같은 restaurant 전체 누적 max_per_restaurant 장,
    # 같은 image_id 1회, duplicate_penalty 적용.
    rep: list[dict] = []
    seen_pairs: set[tuple[str, str]] = set()
    seen_image_ids: set[int] = set()
    per_restaurant_count: dict[str, int] = defaultdict(int)
    per_review_selected: dict[int, int] = defaultdict(int)
    selected_embs: list["np.ndarray"] = []
    # 선정 단계 거절 로그 — debug_dump.selection_rejections에 적재.
    # phase: "primary" | "fallback" | "fallback_relaxed"
    selection_rejections: list[dict] = []

    # axis_order: image_axis_priority가 정의된 keyword면 그 순서를 먼저 쓰고,
    # 그다음 image_axis_fallback, 마지막으로 점수 기반 정렬 (나머지 축).
    # 짬뽕처럼 짠맛은 priority/fallback에 없어 후순위로 밀린다 (사진으로 직접 보이지 않으므로).
    _score_sorted_axes = sorted(candidates_per_axis.keys(),
                                key=lambda a: -sum(c["score"] for c in candidates_per_axis[a][:3]))
    if uses_per_keyword_image_policy:
        axis_order: list[str] = []
        for a in list(priority_axes_for_kw) + list(fallback_axes_for_kw):
            if a in candidates_per_axis and a not in blocked_axes_for_kw and a not in axis_order:
                axis_order.append(a)
        print(f"  📐 axis priority 적용 ({keyword}→{image_policy_key}): {axis_order[:6]}")
    elif priority_axes_for_kw or fallback_axes_for_kw:
        axis_order: list[str] = []
        for a in priority_axes_for_kw:
            if a in candidates_per_axis and a not in axis_order:
                axis_order.append(a)
        for a in fallback_axes_for_kw:
            if a in candidates_per_axis and a not in axis_order:
                axis_order.append(a)
        for a in _score_sorted_axes:
            if a not in axis_order:
                axis_order.append(a)
        print(f"  📐 axis priority 적용 ({keyword}): {axis_order[:6]}")
    else:
        axis_order = _score_sorted_axes

    def _try_select(cand, fallback: bool, allow_relax_restaurant: bool = False) -> tuple[bool, str | None]:
        """선정 시도. 통과하면 rep에 추가하고 (True, None) 반환, 차단되면 (False, reason)."""
        # 1) image_id 중복 (같은 사진이 여러 축을 차지하지 않게)
        if cand["image_id"] in seen_image_ids:
            return False, "rejected_duplicate_image"
        # 2) (restaurant, axis) 중복
        if (cand["restaurant"], cand["axis"]) in seen_pairs:
            return False, "rejected_duplicate_restaurant"
        # 3) 같은 review_id 누적 cap: primary는 strict(=1), fallback은 loose(=2)
        review_cap = max_per_review_loose if fallback else max_per_review
        rid = cand["review_id"] or 0
        if rid and per_review_selected[rid] >= review_cap:
            return False, "rejected_duplicate_review"
        # 4) 같은 restaurant 누적 cap (다양성). fallback 2단계에서 +1 허용.
        rest_cap = max_per_restaurant + (1 if allow_relax_restaurant else 0)
        if cand["restaurant"] and per_restaurant_count[cand["restaurant"]] >= rest_cap:
            return False, "rejected_duplicate_restaurant"

        # duplicate_penalty 적용
        dup_pen = 0.0
        emb = img_embeddings.get(cand["image_id"])
        if emb is not None and selected_embs:
            sims = [float(np.dot(emb, e)) for e in selected_embs]
            if sims and max(sims) >= dup_thresh:
                dup_pen = w_dup
                cand["score"] = round(cand["score"] - dup_pen, 4)

        seen_pairs.add((cand["restaurant"], cand["axis"]))
        seen_image_ids.add(cand["image_id"])
        per_restaurant_count[cand["restaurant"]] += 1
        if rid:
            per_review_selected[rid] += 1
        cand["_duplicate_penalty"] = dup_pen
        cand["clip_vector"] = img_vecs.get(cand["restaurant"], {})
        cand["reason"] = (f"axis={cand['_axis_clip']:+.2f} cat={cand['_category']:.2f} "
                          f"non_food={cand['_non_food']:.2f} wrong={cand['_wrong']:.2f} "
                          f"ax_req={cand.get('_axis_req_bonus', 0.0):+.2f} "
                          f"ax_rej={cand.get('_axis_rej_penalty', 0.0):+.2f} "
                          f"linked={cand['_linked']:+.2f} dup={dup_pen:.2f}"
                          f"{' [fallback]' if fallback else ''}")
        cand["selected_fallback"] = bool(fallback)
        rep.append(cand)
        if emb is not None:
            selected_embs.append(emb)
        # debug_records 동기화
        for r in all_records:
            if r["image_id"] == cand["image_id"] and r["axis"] == cand["axis"]:
                r["selected"] = True
                r["selected_fallback"] = bool(fallback)
                r["duplicate_penalty"] = dup_pen
                r["final_score"] = cand["score"]
                break
        return True, None

    # (4a) 축별 1차 선정 — 각 축에서 점수 최상위 후보 1장씩 시도
    for ax_name in axis_order:
        cands = candidates_per_axis[ax_name]
        for cand in cands:
            ok_sel, sel_reason = _try_select(cand, fallback=False, allow_relax_restaurant=False)
            if ok_sel:
                break
            selection_rejections.append({
                "phase": "primary",
                "image_id": cand["image_id"],
                "review_id": cand["review_id"],
                "restaurant": cand["restaurant"],
                "axis": ax_name,
                "score": cand["score"],
                "reason": sel_reason,  # rejected_duplicate_image / _restaurant / _review
            })
        if len(rep) >= top_n:
            break

    # ── (5) 6개 미만이면 fallback fill ──
    # 같은 keyword 내 깨끗한 후보(meta gate 통과 + foodness 충분) 중에서
    # rep에 아직 없는 image를 점수 순으로 추가. 다른 keyword prefix는 절대 사용하지 않는다
    # (이 함수가 받는 images 자체가 이미 prefix 컷오프된 상태).
    fallback_added = 0
    fallback_relaxed_restaurant = False
    if len(rep) < fallback_target:
        # 모든 (image × axis) 후보를 flat하게 모아 점수 순으로 정렬
        all_cands_flat: list[dict] = []
        fallback_axis_source = axis_order if uses_per_keyword_image_policy else list(candidates_per_axis.keys())
        for ax in fallback_axis_source:
            all_cands_flat.extend(candidates_per_axis.get(ax, []))
        all_cands_flat.sort(key=lambda c: -c["score"])

        # foodness 결합 점수 — meta gate를 한 번 더 보수적으로 확인
        def _foodness_combined(c: dict) -> float:
            return (float(c.get("_category", 0.0)) + float(c.get("_food", 0.0))
                    - float(c.get("_non_food", 0.0)) - float(c.get("_wrong", 0.0)))

        # 1단계: 기존 cap 그대로
        for cand in all_cands_flat:
            if len(rep) >= fallback_target:
                break
            if _foodness_combined(cand) < fallback_min_combined:
                selection_rejections.append({
                    "phase": "fallback",
                    "image_id": cand["image_id"], "review_id": cand["review_id"],
                    "restaurant": cand["restaurant"], "axis": cand["axis"],
                    "score": cand["score"], "reason": "rejected_low_foodness",
                })
                continue
            ok_sel, sel_reason = _try_select(cand, fallback=True, allow_relax_restaurant=False)
            if ok_sel:
                fallback_added += 1
            else:
                selection_rejections.append({
                    "phase": "fallback",
                    "image_id": cand["image_id"], "review_id": cand["review_id"],
                    "restaurant": cand["restaurant"], "axis": cand["axis"],
                    "score": cand["score"], "reason": sel_reason,
                })

        # 2단계: 여전히 모자라면 per_restaurant cap을 +1 완화
        if len(rep) < fallback_target:
            fallback_relaxed_restaurant = True
            for cand in all_cands_flat:
                if len(rep) >= fallback_target:
                    break
                if _foodness_combined(cand) < fallback_min_combined:
                    continue
                ok_sel, sel_reason = _try_select(cand, fallback=True, allow_relax_restaurant=True)
                if ok_sel:
                    fallback_added += 1
                else:
                    selection_rejections.append({
                        "phase": "fallback_relaxed",
                        "image_id": cand["image_id"], "review_id": cand["review_id"],
                        "restaurant": cand["restaurant"], "axis": cand["axis"],
                        "score": cand["score"], "reason": sel_reason,
                    })

    if uses_per_keyword_image_policy:
        axis_rank = {ax: i for i, ax in enumerate(axis_order)}
        rep.sort(key=lambda x: (axis_rank.get(x["axis"], 999), -x["score"]))
    else:
        rep.sort(key=lambda x: -x["score"])

    # ── (6) 요약 로그 ──
    cutoff_count = sum(1 for r in all_records if r.get("excluded_reason"))
    raw_candidate_count = len(images)
    meta_ok_count = sum(1 for m in img_keyword_meta.values() if m.get("ok"))
    base_selected = len(rep) - fallback_added
    print(f"  🖼️  대표 이미지 {len(rep)}장 "
          f"(축별 {base_selected} + fallback {fallback_added}) "
          f"| 후보 {raw_candidate_count}장 → meta-OK {meta_ok_count}장 → 컷오프 {cutoff_count}건")
    if uses_per_keyword_image_policy:
        selected_safe_axes = [str(r.get("axis", "")) for r in rep]
        image_axis_policy_debug["selected_safe_axes"] = selected_safe_axes
        image_axis_policy_debug["selected_count"] = len(rep)
        print(f"  🛂 {policy_slug} representative gate: "
              f"total={image_axis_policy_debug['rep_score_gate_total']} "
              f"passed={image_axis_policy_debug['rep_score_gate_passed']} "
              f"logo={image_axis_policy_debug['rejected_logo_or_text']} "
              f"non_food={image_axis_policy_debug['rejected_non_food']} "
              f"wrong={image_axis_policy_debug['rejected_wrong_food']} "
              f"empty={image_axis_policy_debug['rejected_empty_bowl_or_leftover']} "
              f"side={image_axis_policy_debug['rejected_side_dish_only']} "
              f"table={image_axis_policy_debug['rejected_table_or_drinks']} "
              f"low_required={image_axis_policy_debug.get(low_required_key, 0)} "
              f"blocked_axis={image_axis_policy_debug['rejected_blocked_axis']}")
        print(f"  🖼️ selected axes: {', '.join(selected_safe_axes) if selected_safe_axes else '(none)'}")
    if fallback_relaxed_restaurant:
        print(f"    ↪️ per_restaurant cap 완화 적용 (한 식당이 {max_per_restaurant + 1}장까지)")
    if len(rep) < fallback_target:
        # 6개를 못 채운 사유 진단
        if raw_candidate_count == 0:
            reason = "raw image 0장 — 해당 keyword로 크롤링된 이미지가 없음"
        elif meta_ok_count < fallback_target:
            reason = (f"foodness gate 통과 {meta_ok_count}장 < {fallback_target} "
                      f"— 비음식/저품질 사진이 너무 많음")
        else:
            reason = "다양성 제한으로 후보가 부족 — 같은 식당/리뷰 반복"
        print(f"  ⚠️  {len(rep)}/{fallback_target}장 (부족 사유: {reason})")
    if not rep:
        print(f"  ⚠️ 현재 keyword({keyword})로 음식이 보이는 후보가 부족 → 대표 이미지 생략")

    safe = keyword.replace(" ", "_")
    _drop_keys = {"image_path", "resolved_image_path", "downloaded_from_url"}
    rep_for_json = [{k: v for k, v in r.items() if k not in _drop_keys} for r in rep]
    with open(f"{safe}_representative_images.json", "w", encoding="utf-8") as f:
        json.dump({"keyword": keyword, "images": rep_for_json}, f, ensure_ascii=False, indent=2)

    if debug_dump is not None:
        debug_dump["skipped_keyword_mismatch_count"] = len(skipped_keyword_mismatch)
        debug_dump["skipped_keyword_mismatch"] = skipped_keyword_mismatch[:200]
        debug_dump.update(image_axis_policy_debug)
        debug_dump["image_axis_evaluations"] = all_records
        debug_dump["selection_rejections"] = selection_rejections[:400]
        debug_dump["selection_summary"] = {
            "raw_candidates_after_prefix_cut": raw_candidate_count,
            "meta_gate_passed": meta_ok_count,
            "selected_total": len(rep),
            "selected_primary": base_selected,
            "selected_fallback": fallback_added,
            "fallback_target": fallback_target,
            "fallback_relaxed_restaurant": fallback_relaxed_restaurant,
            "per_restaurant_count": dict(per_restaurant_count),
        }
        debug_dump["image_filtering_thresholds"] = {
            "min_food_presence": min_food, "max_non_food_penalty": max_non,
            "min_category_presence": min_cat, "max_wrong_food_penalty": max_wrong,
            "category_bonus_weight": w_cat, "wrong_food_weight": w_wrong,
            "non_food_penalty_weight": w_non, "linked_text_weight": w_link,
            "duplicate_threshold": dup_thresh, "duplicate_penalty_weight": w_dup,
            "max_per_review": max_per_review,
            "max_per_review_loose": max_per_review_loose,
            "max_per_restaurant": max_per_restaurant,
            "max_per_restaurant_axis": max_per_rest_axis,
            "fallback_target_count": fallback_target,
            "fallback_min_combined": fallback_min_combined,
        }

    return rep


# ── DB 저장 ───────────────────────────────────────────────────

def save_to_db(keyword, fused_vecs, text_vecs, img_vecs, rep_images,
               weights_used, mode, update_rep_images: bool = True):
    """
    update_rep_images=False면 representative_images 테이블은 건드리지 않는다.
    (새 후보가 0장일 때 기존 대표 이미지를 유지하기 위함)
    """
    with get_conn() as conn:
        for rest_name, fv in fused_vecs.items():
            rest_id = upsert_restaurant(conn, {"name": rest_name, "source": "multimodal"})
            upsert_multimodal_profile(conn, rest_id, keyword, {
                "fused_vector":     fv,
                "text_only_vector": text_vecs.get(rest_name, {}),
                "image_sentiment":  img_vecs.get(rest_name, {}),
                "has_image_data":   rest_name in img_vecs and bool(img_vecs[rest_name]),
                "fusion_mode":      "late_dynamic",
                "fusion_weights":   weights_used.get(rest_name, {}),
                "image_mode":       mode,
            })
        if update_rep_images:
            upsert_representative_images(conn, keyword, rep_images)
        else:
            print("  ↪️  representative_images 갱신 skip (새 후보 0장 — 기존 유지)")
    print(f"  ✅ DB 저장: {len(fused_vecs)}개 식당 | 대표이미지 {len(rep_images)}장")


# ── 진입점 ────────────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser(description="Multimodal Profiler v7")
    parser.add_argument("keyword", nargs="?", default=None)
    parser.add_argument("--mode", default=IMAGE_MODE, choices=["clip", "simple"])
    parser.add_argument("--debug", action="store_true")
    parser.add_argument("--no-cache", action="store_true", help="CLIP 임베딩 캐시 사용 안 함")
    parser.add_argument("--source", choices=["db", "csv", "auto"], default="auto")
    parser.add_argument("--purge-rep", action="store_true",
                        help="해당 keyword의 representative_images를 DB에서 삭제 후 재선정")
    parser.add_argument("--refresh-image-scores", action="store_true",
                        help="review_image_scores 기존 캐시를 무시하고 CLIP 재평가 후 갱신")
    parser.add_argument("--max-score-candidates", type=int, default=0,
                        help="이미지 후보 상한 (0=무제한, 기존 동작 유지)")
    args = parser.parse_args()

    keyword = args.keyword or input("키워드: ").strip()
    if not keyword:
        print("키워드를 입력하세요!")
        return
    safe = keyword.replace(" ", "_")
    use_cache = not args.no_cache

    print(f"\n{'━' * 55}")
    print(f"  Multimodal Profiler v7  '{keyword}'  mode={args.mode}  cache={'on' if use_cache else 'off'}")
    print(f"{'━' * 55}")

    # text_vector 로드
    vec_file = f"{safe}_vectors.json"
    if not os.path.exists(vec_file):
        print(f"  ❌ {vec_file} 없음 → python Food_profiler.py {keyword} 먼저 실행")
        return
    with open(vec_file, "r", encoding="utf-8") as f:
        text_data = json.load(f)

    axes_config = text_data.get("axes_config", {})
    text_vecs = {}
    axes_list = text_data.get("axes", [])
    for rest, info in text_data.get("restaurants", {}).items():
        if isinstance(info, dict):
            if "normalized" in info:
                text_vecs[rest] = info["normalized"]
            elif "vector" in info and axes_list:
                text_vecs[rest] = dict(zip(axes_list, info["vector"]))
    print(f"\n📊 텍스트 벡터: {len(text_vecs)}개 식당")

    debug_dump = {"keyword": keyword} if args.debug else None

    # 리뷰 로드: DB 우선
    df = None
    if args.source in ("auto", "db"):
        df = load_reviews_from_db(keyword, debug_dump=debug_dump)
    if (df is None or len(df) == 0) and args.source in ("auto", "csv"):
        print("  → CSV 폴백")
        df = load_reviews_from_csv(keyword)

    rules = load_stand()["rules"] if load_stand() else {
        "scoring": {"positive_hit": 1.0, "negative_hit": -0.5}
    }

    if df is None or len(df) == 0:
        print("  ⚠️ 이미지 리뷰 없음 → 텍스트 벡터만 DB 저장")
        with get_conn() as conn:
            for rest_name, tv in text_vecs.items():
                rest_id = upsert_restaurant(conn, {"name": rest_name, "source": "profiler"})
                upsert_multimodal_profile(conn, rest_id, keyword, {
                    "fused_vector": tv, "text_only_vector": tv,
                    "image_sentiment": {}, "has_image_data": False,
                })
        print(f"  ✅ {len(text_vecs)}개 식당 텍스트 벡터 저장 완료")
        return

    total = len(df)
    img_cnt = int(df["HasPicture"].sum()) if "HasPicture" in df.columns else 0
    print(f"  리뷰 {total}건 | 이미지 {img_cnt}건 ({img_cnt/total*100:.1f}%)" if total else "")

    print(f"\n🔍 이미지 벡터 생성...")
    img_vecs, rest_linked = build_image_vectors(
        df, axes_config, keyword, args.mode, rules,
        use_cache=use_cache, debug_dump=debug_dump
    )

    print(f"\n🔀 Late Fusion (dynamic)...")
    fused_vecs, weights_used = late_fusion_dynamic(text_vecs, img_vecs, dynamic=True)
    print(f"  {len(fused_vecs)}개 식당 (이미지보정: {sum(1 for r in fused_vecs if r in img_vecs and img_vecs[r])}개)")

    print(f"\n🖼️  대표 이미지...")
    rep = extract_rep_images(df, axes_config, img_vecs, rest_linked, keyword,
                             rules=rules, top_n=6, use_cache=use_cache,
                             debug_dump=debug_dump,
                             refresh_image_scores=args.refresh_image_scores,
                             max_score_candidates=args.max_score_candidates)

    # --purge-rep는 새 후보가 1장 이상일 때만 적용 (0장이면 기존 유지)
    if args.purge_rep:
        if rep:
            try:
                with get_conn() as conn:
                    deleted = delete_representative_images(conn, keyword)
                print(f"  🧹 기존 대표 이미지 {deleted}개 삭제됨 (--purge-rep, 새 후보 {len(rep)}장)")
            except Exception as e:
                print(f"  ⚠️ purge 실패: {type(e).__name__}: {e}")
        else:
            print(f"  ⚠️ 새 대표 이미지 후보 0장 → 기존 representative_images 유지 (--purge-rep 무시)")

    print(f"\n💾 DB 저장...")
    # 새 rep이 0장이면 representative_images 갱신 자체를 skip (기존 유지)
    save_to_db(keyword, fused_vecs, text_vecs, img_vecs, rep, weights_used, args.mode,
               update_rep_images=bool(rep))

    # JSON 백업
    with open(f"{safe}_multimodal_vectors.json", "w", encoding="utf-8") as f:
        json.dump({
            "keyword": keyword, "fusion_mode": "late_dynamic",
            "image_mode": args.mode,
            "total_reviews": total, "image_reviews": img_cnt,
            "restaurants": {
                r: {
                    "fused_vector": fused_vecs[r],
                    "text_only_vector": text_vecs.get(r, {}),
                    "image_sentiment": img_vecs.get(r, {}),
                    "fusion_weights": weights_used.get(r, {}),
                    "has_image_data": bool(img_vecs.get(r)),
                }
                for r in fused_vecs
            },
        }, f, ensure_ascii=False, indent=2)
    print(f"  ✅ {safe}_multimodal_vectors.json")

    if args.debug and debug_dump is not None:
        debug_dump["text_vecs"] = text_vecs
        debug_dump["image_vecs"] = img_vecs
        debug_dump["fused_vecs"] = fused_vecs
        debug_dump["weights_used"] = weights_used
        debug_dump["linked_text_vecs"] = {
            r: {ax: round(float(np.mean(v)), 4) for ax, v in axes.items() if v}
            for r, axes in rest_linked.items()
        }
        with open(f"debug_multimodal_{safe}.json", "w", encoding="utf-8") as f:
            json.dump(debug_dump, f, ensure_ascii=False, indent=2)
        print(f"  🐛 debug_multimodal_{safe}.json")

    print(f"\n{'━' * 55}")
    print(f"  완료! 텍스트:{len(text_vecs)} 이미지:{len([r for r in img_vecs if img_vecs[r]])} 최종:{len(fused_vecs)}")
    print(f"{'━' * 55}")


if __name__ == "__main__":
    main()
