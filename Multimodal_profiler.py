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
)

IMAGE_MODE = "clip"     # "clip" | "simple"
ALPHA_BASE = 0.7
BETA_BASE  = 0.3
STAND_DIR  = os.path.join(os.path.dirname(os.path.abspath(__file__)), "stand")
CLIP_CACHE_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "clip_cache")

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

def load_reviews_from_db(keyword: str) -> Optional[pd.DataFrame]:
    terms = get_search_terms(keyword)
    try:
        with get_conn() as conn:
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
    frames = []
    for f in sorted(glob.glob("*.csv")):
        if any(x in f for x in ["_dictionary", "_vectors", "_axes", "candidate"]):
            continue
        try:
            tmp = pd.read_csv(f, encoding="utf-8-sig")
            if "Review" in tmp.columns and "Restaurant" in tmp.columns:
                frames.append(tmp)
        except Exception:
            pass
    if not frames:
        return None
    df = pd.concat(frames, ignore_index=True)
    df = df.drop_duplicates(subset=["Restaurant", "Date", "Review"], keep="first")

    terms = get_search_terms(keyword)
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

def extract_rep_images(df, axes_config, img_vecs, rest_linked, keyword, top_n=6):
    img_reviews = df[df["HasPicture"] == 1].copy()
    if len(img_reviews) == 0:
        return []

    def get_src(row):
        for col in ["ImageURLs", "ImagePaths", "Image_Links", "review_images", "image_url"]:
            val = str(row.get(col, "") or "")
            if val and val not in ("", "nan", "[]"):
                first = val.split(" | ")[0].split("|")[0].strip().strip("[]'\"")
                return first
        return ""

    candidates = []
    seen_pairs = set()  # (restaurant, axis) 중복 방지

    for ax_name, ax_info in axes_config.items():
        if ax_info.get("is_meta"):
            continue
        pos_kws = ax_info.get("positive_keywords", ax_info.get("positive", []))
        if not pos_kws:
            continue

        per_axis = []
        for _, row in img_reviews.iterrows():
            rt = str(row.get("Review", "") or "")
            rest = str(row.get("Restaurant", "") or "")
            src = get_src(row)
            if not src or not rest:
                continue
            mc = sum(1 for kw in pos_kws if kw in rt)
            if mc == 0:
                continue
            isc = float(img_vecs.get(rest, {}).get(ax_name, 0.0))
            linked_axis = rest_linked.get(rest, {}).get(ax_name, [])
            linked_avg = float(np.mean(linked_axis)) if linked_axis else 0.0
            score = 0.5 * abs(isc) + 0.3 * abs(linked_avg) + 0.2 * (mc / 3.0)
            per_axis.append({
                "image_src": src,
                "axis": ax_name,
                "label": ax_name,
                "restaurant": rest,
                "score": round(score, 3),
                "review_snippet": rt[:120],
                "_clip_axis_score": round(isc, 3),
                "_linked_text_score": round(linked_avg, 3),
            })
        per_axis.sort(key=lambda x: -x["score"])
        for cand in per_axis:
            key = (cand["restaurant"], cand["axis"])
            if key in seen_pairs:
                continue
            seen_pairs.add(key)
            candidates.append(cand)
            break

    candidates.sort(key=lambda x: -x["score"])
    rep = candidates[:top_n]
    for item in rep:
        item["clip_vector"] = img_vecs.get(item["restaurant"], {})

    safe = keyword.replace(" ", "_")
    with open(f"{safe}_representative_images.json", "w", encoding="utf-8") as f:
        json.dump({"keyword": keyword, "images": rep}, f, ensure_ascii=False, indent=2)
    print(f"  🖼️  대표 이미지 {len(rep)}장 → JSON 저장")
    return rep


# ── DB 저장 ───────────────────────────────────────────────────

def save_to_db(keyword, fused_vecs, text_vecs, img_vecs, rep_images,
               weights_used, mode):
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
        upsert_representative_images(conn, keyword, rep_images)
    print(f"  ✅ DB 저장: {len(fused_vecs)}개 식당 | 대표이미지 {len(rep_images)}장")


# ── 진입점 ────────────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser(description="Multimodal Profiler v7")
    parser.add_argument("keyword", nargs="?", default=None)
    parser.add_argument("--mode", default=IMAGE_MODE, choices=["clip", "simple"])
    parser.add_argument("--debug", action="store_true")
    parser.add_argument("--no-cache", action="store_true", help="CLIP 임베딩 캐시 사용 안 함")
    parser.add_argument("--source", choices=["db", "csv", "auto"], default="auto")
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

    # 리뷰 로드: DB 우선
    df = None
    if args.source in ("auto", "db"):
        df = load_reviews_from_db(keyword)
    if (df is None or len(df) == 0) and args.source in ("auto", "csv"):
        print("  → CSV 폴백")
        df = load_reviews_from_csv(keyword)

    rules = load_stand()["rules"] if load_stand() else {
        "scoring": {"positive_hit": 1.0, "negative_hit": -0.5}
    }

    debug_dump = {"keyword": keyword} if args.debug else None

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
    rep = extract_rep_images(df, axes_config, img_vecs, rest_linked, keyword, 6)

    print(f"\n💾 DB 저장...")
    save_to_db(keyword, fused_vecs, text_vecs, img_vecs, rep, weights_used, args.mode)

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
