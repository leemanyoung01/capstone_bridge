"""
app.py — Flask API 서버 (PostgreSQL 전용) v2
==============================================
v2 개선점:
  - cosine은 taste_axes 만으로 계산 (메타축은 보조 부스트)
  - 응답에 axis_scores, axis_contributions, top_axes, fusion_weights,
    text/image confidence, representative_image, place_url, fallback_search_url 추가
  - 동적 fusion (텍스트 confidence 높으면 image 가중치 자동 감소)
  - frontend 호환 fallback (similarity, reasons, evidence 유지)

실행:
  python app.py
  gunicorn app:app -b 0.0.0.0:5000 --workers 3
"""
import csv
import json, math, os
from urllib.parse import quote
import numpy as np
from flask import Flask, jsonify, request, send_file, send_from_directory

# 추천 결과 기본 Top-N. /api/recommend?limit=... 로 override 가능 (max 50).
DEFAULT_RESULT_LIMIT = 5
MAX_RESULT_LIMIT = 50

from db import (
    get_conn,
    get_all_restaurants,
    get_profiles_by_keyword,
    get_all_keywords,
    get_axes_config,
    get_representative_images,
    get_all_rep_keywords,
    get_best_restaurant_image,
)
BASE_DIR   = os.path.dirname(os.path.abspath(__file__))
IMAGES_DIR = os.path.join(BASE_DIR, "images")
FRONTEND_DIST = os.environ.get(
    "FRONTEND_DIST",
    os.path.join(BASE_DIR, "frontend", "dist"),
)

app = Flask(__name__)

# 동적 fusion 기본 파라미터 (실제 가중은 confidence로 자동 조정)
USER_ALPHA_BASE = 0.7
USER_BETA_BASE  = 0.3
META_BOOST      = 0.05  # 메타축 일치 시 cosine에 더하는 부스트 비율
DEFAULT_KEYWORD_PREF = os.environ.get("DEFAULT_KEYWORD", "삼겹살")


# ── DB 캐시 로드 ─────────────────────────────────────────────

def _load_all_data() -> tuple[dict, dict, dict]:
    bundles: dict = {}
    with get_conn() as conn:
        keywords = get_all_keywords(conn)
        for kw in keywords:
            profiles = get_profiles_by_keyword(conn, kw)
            if not profiles:
                continue
            sample    = profiles[0]
            fused     = sample.get("fused_vector") or {}
            text      = sample.get("text_vector")  or {}
            base_vec  = fused if fused else text
            axes_list = [k for k in base_vec if not k.startswith("_")]
            cfg       = get_axes_config(conn, kw)

            restaurants: dict = {}
            for p in profiles:
                name = p["name"]
                fv   = p.get("fused_vector") or {}
                tv   = p.get("text_vector")  or {}
                iv   = p.get("image_vector") or {}
                restaurants[name] = {
                    "fused_vector":     fv if fv else tv,
                    "text_only_vector": tv,
                    "image_sentiment":  iv,
                    "normalized":       tv,
                    "has_image_data":   bool(p.get("has_image_data", False)),
                    "evidence":         p.get("evidence") or {},
                    "avg_total":        p.get("avg_rating", 0),
                    "keyword_reviews":  p.get("keyword_reviews", 0),
                    "image_coverage":   float(p.get("image_coverage", 0) or 0),
                }
            bundles[kw] = {
                "keyword":     kw,
                "axes":        axes_list,
                "axes_config": cfg,
                "restaurants": restaurants,
            }

        rep_images: dict = {}
        for kw in get_all_rep_keywords(conn):
            imgs = get_representative_images(conn, kw)
            rep_images[kw] = {
                "keyword": kw,
                "images": [
                    {
                        "image_src":      img.get("image_url",""),
                        "axis":           img.get("axis",""),
                        "label":          img.get("label",""),
                        "restaurant":     img.get("restaurant_name",""),
                        "score":          img.get("score", 0.0),
                        "review_snippet": img.get("review_snippet",""),
                        "clip_vector":    img.get("clip_vector", {}),
                    }
                    for img in imgs
                ],
            }

        rest_db = {r["name"]: r for r in get_all_restaurants(conn)}

    return bundles, rep_images, rest_db


print("\n📂 DB에서 데이터 로드 중...")
try:
    ALL_DATA, REP_IMAGES, REST_DB = _load_all_data()
    DEFAULT_KW = (DEFAULT_KEYWORD_PREF if DEFAULT_KEYWORD_PREF in ALL_DATA
                  else (sorted(ALL_DATA.keys())[0] if ALL_DATA else None))
    print(f"  키워드: {sorted(ALL_DATA.keys())}")
    print(f"  식당: {len(REST_DB)}개 | 기본 키워드: {DEFAULT_KW}\n")
except Exception as e:
    print(f"  ❌ DB 로드 실패: {e}")
    db_host = os.environ.get('DATABASE_URL','(env not set)').split('@')[-1]
    print(f"     DB host = {db_host}")
    ALL_DATA, REP_IMAGES, REST_DB, DEFAULT_KW = {}, {}, {}, None


# ── 유틸 ─────────────────────────────────────────────────────

def _norm_kw(kw: str) -> str:
    kw = str(kw or "").strip()
    return kw[:-len("_multimodal")] if kw.endswith("_multimodal") else kw


def _all_axes(bundle: dict) -> list[str]:
    if bundle.get("axes"): return bundle["axes"]
    if bundle.get("axes_config"): return list(bundle["axes_config"].keys())
    for info in (bundle.get("restaurants") or {}).values():
        if isinstance(info, dict):
            for key in ("fused_vector","text_only_vector","normalized"):
                vec = info.get(key)
                if isinstance(vec, dict):
                    return [k for k in vec if not k.startswith("_")]
    return []


# ── stand_axes.json axis_policy 응답 필터 ─────────────────────────────
# /api/config 응답 시 stand_axes.json의 axis_policy를 1차로 적용해
# 커피/라떼류 같은 음료 keyword에서 공용 taste 축이 UI로 새지 않게 한다.
# DB axes_config는 건드리지 않고, 응답 view 단계에서만 좁힌다.

_STAND_AXES_CACHE: dict = {"mtime": 0.0, "data": None}


def _load_stand_axes() -> dict:
    """stand/stand_axes.json을 mtime 기반으로 캐시 로드."""
    path = os.path.join(BASE_DIR, "stand", "stand_axes.json")
    if not os.path.exists(path):
        return {}
    try:
        mt = os.path.getmtime(path)
    except OSError:
        return _STAND_AXES_CACHE.get("data") or {}
    if mt == _STAND_AXES_CACHE.get("mtime") and _STAND_AXES_CACHE.get("data"):
        return _STAND_AXES_CACHE["data"]
    try:
        with open(path, "r", encoding="utf-8") as f:
            data = json.load(f)
    except Exception:
        return _STAND_AXES_CACHE.get("data") or {}
    _STAND_AXES_CACHE["mtime"] = mt
    _STAND_AXES_CACHE["data"] = data
    return data


def _resolve_keyword_category(kw: str) -> str | None:
    """alias_to_category 기반으로 keyword의 food_specific category 결정."""
    stand = _load_stand_axes()
    if not stand:
        return None
    food_specific = stand.get("food_specific_axes", {}) or {}
    alias_to_cat = stand.get("alias_to_category", {}) or {}
    if kw in food_specific:
        return kw
    if kw in alias_to_cat and alias_to_cat[kw] in food_specific:
        return alias_to_cat[kw]
    for food, aliases in (stand.get("keyword_aliases", {}) or {}).items():
        if kw in aliases or kw == food:
            if food in alias_to_cat and alias_to_cat[food] in food_specific:
                return alias_to_cat[food]
            for cat in food_specific:
                if cat in food or food in cat:
                    return cat
    return None


def _resolve_axis_policy(kw: str) -> dict:
    """
    keyword에 적용할 axis_policy. 우선순위:
      1) matched category가 axis_policy에 있으면 그것
      2) keyword 자체가 axis_policy 키이면 그것
      3) _default
    """
    stand = _load_stand_axes()
    policy_block = stand.get("axis_policy", {}) or {}
    default = dict(policy_block.get("_default") or {})
    default.setdefault("include_shared_taste_axes", True)
    default.setdefault("include_meta_axes",         True)
    default.setdefault("use_food_specific_axes",    True)
    default.setdefault("shared_taste_allowlist",    [])

    cat = _resolve_keyword_category(kw)
    chosen = None
    if cat and cat in policy_block:
        chosen = policy_block[cat]
    elif kw in policy_block:
        chosen = policy_block[kw]
    if not chosen:
        return default
    merged = dict(default)
    merged.update({k: v for k, v in chosen.items() if not str(k).startswith("_")})
    return merged


def _filter_axes_config_by_policy(axes_config_raw: dict, policy: dict) -> dict:
    """
    axes_config (DB 캐시) 를 axis_policy로 필터링한 view dict 반환.
    그룹명 기준 판단:
      - 메타 그룹("메타") 또는 is_meta=True → include_meta_axes 따름
      - 공용 taste 그룹("맛"/"식감"/"기타"/"공용") → allow_shared 또는 allowlist 통과 시 유지
      - 그 외(*특화 같은 food_specific) → 항상 유지
    """
    allow_shared = bool(policy.get("include_shared_taste_axes", True))
    allow_meta = bool(policy.get("include_meta_axes", True))
    allowlist = set(policy.get("shared_taste_allowlist") or [])
    SHARED_GROUPS = {"맛", "식감", "기타", "공용", "공통"}
    META_GROUPS = {"메타"}

    out: dict = {}
    for name, info in (axes_config_raw or {}).items():
        if not isinstance(info, dict) or name.startswith("_"):
            continue
        group = info.get("group", "기타")
        is_meta = bool(info.get("is_meta")) or group in META_GROUPS
        if is_meta:
            if allow_meta:
                out[name] = info
            continue
        if group in SHARED_GROUPS:
            if allow_shared or (name in allowlist):
                out[name] = info
            continue
        # 특화 그룹 — 항상 유지
        out[name] = info
    return out


# ── 표시(display) 축 필터 ──────────────────────────────────────────
# stand_axes.json의 display_axis_policy를 읽어 API 응답에서만 노출 축을 좁힌다.
# 추천 점수·cosine·match_percent·벡터 계산에는 전혀 영향이 없으며,
# top_axes/axis_scores/axis_contributions/evidence_sentences/representative_image
# 처럼 화면에 노출되는 부분만 whitelist로 거른다. 정책이 없는 keyword는 통과.

def _resolve_display_axis_policy(kw: str) -> dict:
    """
    keyword(또는 그 resolved category) 기준 display_axis_policy를 lookup.
    반환은 정책 dict (allowed 키 등) 또는 빈 dict — 빈 dict면 필터링 없음.
    """
    stand = _load_stand_axes()
    if not stand:
        return {}
    block = (stand.get("display_axis_policy") or {})
    if not block:
        return {}
    # 1) raw keyword 그대로
    if kw in block:
        return block[kw] or {}
    # 2) alias resolve된 category
    cat = _resolve_keyword_category(kw)
    if cat and cat in block:
        return block[cat] or {}
    return {}


def _display_allowed_set(kw: str) -> set[str] | None:
    """
    keyword의 표시 허용 축 set 반환. 정책 미정의면 None — 호출부에서 필터링 skip.
    /api/config·/api/recommend의 top_axes·radar·evidence에만 사용.
    """
    pol = _resolve_display_axis_policy(kw)
    allowed = pol.get("allowed") if isinstance(pol, dict) else None
    if not allowed:
        return None
    return set(allowed)


def _display_image_blocked_set(kw: str) -> set[str]:
    """
    keyword의 이미지 선택 화면 차단(blacklist) 축 set 반환.
    /api/representative_images에만 사용. allowed whitelist를 적용하면 6장이
    2장으로 줄어드는 문제를 막기 위해 분리. 정책 미정의 또는 image_blocked가
    없으면 빈 set — 모든 이미지 통과.
    """
    pol = _resolve_display_axis_policy(kw)
    blocked = pol.get("image_blocked") if isinstance(pol, dict) else None
    if not blocked:
        return set()
    return set(blocked)


def _filter_display_axes(kw: str, axes: list[str]) -> list[str]:
    """list[str]에서 display whitelist 통과하는 항목만 순서 보존하여 반환."""
    allowed = _display_allowed_set(kw)
    if allowed is None or not axes:
        return list(axes or [])
    return [a for a in axes if a in allowed]


def _filter_axis_dict(kw: str, axis_dict: dict) -> dict:
    """{axis: value} 형태 dict에서 disallowed key를 제거."""
    allowed = _display_allowed_set(kw)
    if allowed is None or not isinstance(axis_dict, dict):
        return dict(axis_dict or {})
    return {a: v for a, v in axis_dict.items() if a in allowed}


# evidence 표시용 비-맛(매장/실내/서비스/배달 등) 문장을 거르기 위한 term 목록.
# 추천 점수 계산에는 사용하지 않는다 — 화면의 '맛 근거' 문장이 매장/실내 화제로
# 채워지는 것만 방지.
EVIDENCE_NON_TASTE_TERMS = (
    "실내", "매장", "인테리어", "직원", "서비스", "친절", "분위기",
    "포장", "배달", "주차",
)


def _is_non_taste_evidence_sentence(sentence: str) -> bool:
    if not isinstance(sentence, str):
        return False
    return any(term in sentence for term in EVIDENCE_NON_TASTE_TERMS)


def _filter_evidence_by_display_axes(kw: str, evidence_map: dict) -> dict:
    """
    evidence_sentences 전용 필터.
    - display_axis_policy.allowed whitelist 는 더 이상 적용하지 않는다.
      (맛 그래프/레이더 다양성 유지가 우선)
    - 실제 문장이 비어 있거나 공백뿐인 축은 제외 — '근거 없는데 다른 리뷰 가져오기' 방지.
    - 매장/실내/서비스/배달 등 비-맛 화제 문장은 evidence에서만 제거.
      (추천 점수 계산에는 영향 없음)
    """
    if not isinstance(evidence_map, dict):
        return {}
    out: dict = {}
    for axis, sents in evidence_map.items():
        clean = [
            s for s in (sents or [])
            if isinstance(s, str) and s.strip()
            and not _is_non_taste_evidence_sentence(s)
        ]
        if not clean:
            continue
        out[axis] = clean
    return out


def _split_axes(bundle: dict) -> tuple[list[str], list[str]]:
    """axes_config의 is_meta로 taste/meta 분리. 없으면 모두 taste."""
    cfg = bundle.get("axes_config") or {}
    if not cfg:
        axes = _all_axes(bundle)
        return axes, []
    taste, meta = [], []
    for name, info in cfg.items():
        if name.startswith("_"):
            continue
        (meta if info.get("is_meta") else taste).append(name)
    if not taste:
        taste = list(cfg.keys())
    return taste, meta


def _axis_label_map(bundle: dict) -> dict:
    """axes_config의 label 필드에서 {axis_key: display_label} 맵을 만든다."""
    out = {}
    for name, info in (bundle.get("axes_config") or {}).items():
        if name.startswith("_"):
            continue
        out[name] = info.get("label") or name
    return out


def _label_of(axis_key: str, label_map: dict) -> str:
    return label_map.get(axis_key) or axis_key


def _rest_info(name: str) -> dict:
    if name in REST_DB: return REST_DB[name]
    compact = name.replace("-","").replace(" ","").lower()
    for k, v in REST_DB.items():
        if compact in k.replace("-","").replace(" ","").lower(): return v
    return {}


def _cosine(a, b) -> float:
    a, b = np.array(a, dtype=float), np.array(b, dtype=float)
    na, nb = np.linalg.norm(a), np.linalg.norm(b)
    return float(np.dot(a,b)/(na*nb)) if na and nb else 0.0


def _clean(obj):
    if isinstance(obj, dict):  return {k: _clean(v) for k,v in obj.items()}
    if isinstance(obj, list):  return [_clean(v) for v in obj]
    if isinstance(obj, np.generic): obj = obj.item()
    if isinstance(obj, float) and (math.isnan(obj) or math.isinf(obj)): return None
    return obj


def _confidence(vec: dict, axes: list[str]) -> float:
    """벡터의 평균 절댓값을 신뢰도로. 0~1 범위."""
    if not vec or not axes: return 0.0
    vals = [abs(float(vec.get(a, 0.0))) for a in axes]
    if not vals: return 0.0
    mean_abs = sum(vals) / len(vals)
    return float(min(mean_abs * 2.0, 1.0))  # 0.5 이상이면 confidence 1.0


def _user_img_vec(selected_images, axes):
    scores = {ax:[] for ax in axes}
    for img in selected_images:
        cv, ma = img.get("clip_vector",{}) or {}, img.get("axis","")
        for ax in axes:
            v = cv.get(ax,0.0)
            if isinstance(v,(int,float)): scores[ax].append(float(v))
        if ma in scores: scores[ma].append(0.5)
    return {ax: round(float(np.mean(vs)),4) if vs else 0.0 for ax,vs in scores.items()}


def _fuse_dynamic(tv: dict, iv: dict, axes: list[str]) -> tuple[dict, dict, str]:
    """
    동적 fusion: text_conf vs image_conf로 가중치 자동 조정.
    반환: (fused_vec, weights{text, image}, mode)
    """
    if not iv or not any(abs(float(v)) > 0.001 for v in iv.values()):
        return dict(tv), {"text": 1.0, "image": 0.0}, "text_only"

    t_conf = _confidence(tv, axes)
    i_conf = _confidence(iv, axes)
    total = t_conf + i_conf
    if total < 1e-6:
        alpha = USER_ALPHA_BASE
    else:
        # 텍스트 신뢰도 비율을 0.5~0.9 범위로 매핑
        ratio = t_conf / total
        alpha = max(0.5, min(0.9, 0.5 + 0.4 * ratio))

    beta = 1.0 - alpha
    fused = {}
    for ax in axes:
        t = float(tv.get(ax, 0.0))
        i = float(iv.get(ax, 0.0))
        if abs(t) > 0.001 and abs(i) > 0.001:
            fused[ax] = round(alpha * t + beta * i, 4)
        elif abs(t) > 0.001:
            fused[ax] = round(t, 4)
        elif abs(i) > 0.001:
            fused[ax] = round(beta * i, 4)
        else:
            fused[ax] = 0.0
    return fused, {"text": round(alpha, 3), "image": round(beta, 3)}, "multimodal"


# ── 라우트 ───────────────────────────────────────────────────

@app.route("/")
def index():
    dist_index = os.path.join(FRONTEND_DIST, "index.html")
    if os.path.exists(dist_index):
        return send_file(dist_index)

    for fn in ("survey.html", "index.html"):
        p = os.path.join(BASE_DIR, fn)
        if os.path.exists(p):
            return send_file(p)

    return "<h1>frontend/dist 빌드가 없습니다 (npm run build)</h1>", 404


@app.route("/assets/<path:fn>")
def serve_assets(fn):
    return send_from_directory(os.path.join(FRONTEND_DIST, "assets"), fn)


@app.route("/<path:fn>")
def serve_static(fn):
    if fn.startswith(("api/", "images/", "assets/")):
        return "Not Found", 404

    full = os.path.join(FRONTEND_DIST, fn)
    if os.path.isfile(full):
        return send_from_directory(FRONTEND_DIST, fn)

    dist_index = os.path.join(FRONTEND_DIST, "index.html")
    if os.path.exists(dist_index):
        return send_file(dist_index)

    return "Not Found", 404


@app.route("/images/<path:fn>")
def serve_img(fn):
    return send_from_directory(IMAGES_DIR, fn)


@app.route("/api/health")
def api_health():
    db_url = os.environ.get("DATABASE_URL","(env not set)")
    return jsonify({
        "ok":              True,
        "keywords":        sorted(ALL_DATA.keys()),
        "default_keyword": DEFAULT_KW,
        "restaurants":     len(REST_DB),
        "db_host":         db_url.split("@")[-1] if "@" in db_url else "(env not set)",
    })


@app.route("/api/keywords")
def api_keywords():
    return jsonify(sorted(ALL_DATA.keys()))


@app.route("/api/config")
def api_config():
    kw = _norm_kw(request.args.get("keyword", DEFAULT_KW))
    bundle = ALL_DATA.get(kw)
    if not bundle:
        return jsonify({"error": f"'{kw}' 키워드 없음"}), 404

    # ── stand_axes.json의 axis_policy를 응답 단계에서 1차 적용 ──
    # 커피/라떼류처럼 음료 keyword는 공용 taste 축을 UI에서 빼고
    # allowlist + 메타 + 특화 축만 노출. DB axes_config는 손대지 않음.
    policy = _resolve_axis_policy(kw)
    raw_axes_config = bundle.get("axes_config") or {}
    filtered_axes_config = _filter_axes_config_by_policy(raw_axes_config, policy)

    # 필터링 결과가 비면 (특화 축이 DB에 아직 없는 경우 등) 원본 사용 — 화면이 비어버리는 것 방지.
    if not filtered_axes_config:
        filtered_axes_config = raw_axes_config

    # ── display_axis_policy.allowed 화면용 whitelist 적용 안 함 ──
    # 1단계 맛 선택/레이더 차트가 2~3축으로 줄어드는 문제를 막기 위해
    # 화면 노출 축은 axis_policy(_filter_axes_config_by_policy)까지만 좁힌다.
    # display_axis_policy.image_blocked 는 /api/representative_images 에서만 사용.

    from collections import defaultdict
    groups = defaultdict(list)
    for name, info in filtered_axes_config.items():
        groups[info.get("group", "기타")].append(name)
    groups = dict(groups)

    # taste / meta 분리 (필터링된 axes_config 기준)
    taste = [n for n, info in filtered_axes_config.items() if not info.get("is_meta")]
    meta  = [n for n, info in filtered_axes_config.items() if info.get("is_meta")]
    if not taste and filtered_axes_config:
        taste = list(filtered_axes_config.keys())

    label_map = _axis_label_map({"axes_config": filtered_axes_config})
    axes_list = list(filtered_axes_config.keys()) or _all_axes(bundle)

    return jsonify({
        "keyword":         kw,
        "axes":            axes_list,
        "taste_axes":      taste,
        "meta_axes":       meta,
        "groups":          groups,
        "axes_config":     filtered_axes_config,
        "axis_label_map":  label_map,
        "taste_axes_labels": [label_map.get(a, a) for a in taste],
        "meta_axes_labels":  [label_map.get(a, a) for a in meta],
        "_axis_policy": {
            "include_shared_taste_axes": bool(policy.get("include_shared_taste_axes", True)),
            "include_meta_axes":         bool(policy.get("include_meta_axes", True)),
            "shared_taste_allowlist":    sorted(policy.get("shared_taste_allowlist") or []),
            "matched_category":          _resolve_keyword_category(kw),
        },
    })


def _image_src_matches_keyword(src: str, keyword: str) -> bool:
    """
    image_src의 S3 URL이 `/reviews/<keyword>/` (raw 또는 URL-encoded) 을 포함하는지.
    `/reviews/` 자체가 없으면 로컬·dev 데이터로 간주해 통과 (앱 깨짐 방지).
    """
    if not src or not keyword:
        return True
    s = str(src)
    if "/reviews/" not in s:
        return True
    raw = f"/reviews/{keyword}/"
    enc = f"/reviews/{quote(keyword, safe='')}/"
    return (raw in s) or (enc in s)


@app.route("/api/representative_images")
def api_rep_images():
    kw = _norm_kw(request.args.get("keyword", DEFAULT_KW))
    bundle = ALL_DATA.get(kw) or {}
    label_map = _axis_label_map(bundle)

    def _augment(payload, target_kw):
        raw_imgs = payload.get("images") or []
        db_loaded_count = len(raw_imgs)
        # (restaurant, axis) 단위 dedup 안전망 — DB 단에서도 보장하지만 API에서 한 번 더.
        # 동일 image_src 반복도 제거해 같은 사진이 여러 축을 차지하지 않게 함.
        # 추가로: image_src의 S3 prefix가 현재 keyword와 어긋나면 차단 (DB 오염 방어).
        #
        # 이미지 선택 화면 정책 (1차 보정):
        #   이전엔 display_axis_policy.allowed whitelist를 적용해 짬뽕에서 2장만 남는
        #   문제 발생. 이제는 image_blocked blacklist만 사용 — blocked에 들어간 축만
        #   제외하고 안전한 공통축/메타축 이미지는 가능한 모두 통과시켜 6장에 가깝게 유지.
        seen_pair: set = set()
        seen_src: set = set()
        out: list = []
        skipped_mismatch = 0
        skipped_blocked_axis = 0
        image_blocked = _display_image_blocked_set(target_kw)
        for img in raw_imgs:
            src = img.get("image_src", "")
            if not _image_src_matches_keyword(src, target_kw):
                skipped_mismatch += 1
                continue
            ax = img.get("axis", "")
            # 블랙리스트만 적용 (allowed whitelist는 이미지 선택 화면에선 사용 안 함)
            if image_blocked and ax and ax in image_blocked:
                skipped_blocked_axis += 1
                continue
            pair = (img.get("restaurant", ""), ax)
            if pair in seen_pair or (src and src in seen_src):
                continue
            seen_pair.add(pair)
            if src:
                seen_src.add(src)
            if ax and "axis_label" not in img:
                img["axis_label"] = label_map.get(ax) or img.get("label") or ax
            out.append(img)

        returned_count = len(out)
        # 필터링 후 0장이어도 빈 응답이 정답 (blocked 축을 억지로 되살리지 않음).
        # regeneration_needed는 _augment 입력이 0일 때만 True로 유지.
        regeneration_needed = (db_loaded_count == 0)

        # 진단 로그 (CLIP 등 무거운 연산은 안 함 — 카운트만)
        print(f"[api_rep_images] keyword={target_kw} "
              f"db_loaded={db_loaded_count} returned={returned_count} "
              f"prefix-skipped={skipped_mismatch} blocked-axis-skipped={skipped_blocked_axis}")
        if regeneration_needed:
            print(f"[api_rep_images] representative_images regeneration needed "
                  f"for keyword={target_kw}")

        return {
            **payload,
            "images": out,
            "axis_label_map": label_map,
            "db_loaded_count": db_loaded_count,
            "returned_count": returned_count,
            "skipped_keyword_mismatch_count": skipped_mismatch,
            "skipped_blocked_axis_count":     skipped_blocked_axis,
            "regeneration_needed": regeneration_needed,
        }

    if kw in REP_IMAGES:
        return jsonify(_augment(dict(REP_IMAGES[kw]), kw))
    for k, v in REP_IMAGES.items():
        if kw in k or k in kw:
            return jsonify(_augment(dict(v), kw))
    # 캐시에 keyword 자체가 없는 경우 (예: 칼국수) — regeneration_needed=True 명시
    print(f"[api_rep_images] keyword={kw} not in REP_IMAGES cache "
          f"(representative_images empty or 모듈 시작 후 신규 생성) "
          f"→ regeneration needed")
    return jsonify({
        "keyword": kw, "images": [], "axis_label_map": label_map,
        "db_loaded_count": 0, "returned_count": 0,
        "skipped_keyword_mismatch_count": 0,
        "regeneration_needed": True,
    })


def _build_fallback_url(name: str, keyword: str) -> str:
    """naver_url 없을 때 검색 URL 생성."""
    q = f"{name} {keyword}".strip()
    return f"https://map.naver.com/v5/search/{quote(q)}"


# ── pseudo_relevance lookup (eval_labels.csv 기반, 캐시) ─────
_RELEVANCE_CACHE: dict = {"mtime": 0.0, "data": {}, "label_type": "pseudo-label"}


def _load_relevance_table():
    """
    eval_labels.csv (또는 _human 우선) 을 읽어
    {(keyword, frozenset(prefs.items()), restaurant): 0|1} 사전을 만든다.
    파일 mtime이 바뀌면 자동 재로드.
    """
    candidates = ["eval_labels_human.csv", "eval_labels.csv"]
    path = next((os.path.join(BASE_DIR, p) for p in candidates
                 if os.path.exists(os.path.join(BASE_DIR, p))), None)
    if not path:
        _RELEVANCE_CACHE["data"] = {}
        return
    try:
        mt = os.path.getmtime(path)
    except OSError:
        return
    if mt == _RELEVANCE_CACHE["mtime"]:
        return

    table: dict = {}
    label_type = "human-label" if "human" in os.path.basename(path).lower() else "pseudo-label"
    try:
        with open(path, "r", encoding="utf-8-sig", newline="") as f:
            reader = csv.DictReader(f)
            for row in reader:
                kw = (row.get("keyword") or "").strip()
                pref_raw = row.get("user_pref_json") or "{}"
                rest = (row.get("restaurant") or "").strip()
                rel = int(row.get("relevance") or 0)
                try:
                    prefs = json.loads(pref_raw)
                except Exception:
                    continue
                pref_key = frozenset((k, float(v)) for k, v in prefs.items())
                table[(kw, pref_key, rest)] = rel
    except Exception:
        pass

    _RELEVANCE_CACHE.update({"mtime": mt, "data": table, "label_type": label_type})


def _pseudo_relevance(keyword: str, user_prefs: dict, rest_name: str):
    _load_relevance_table()
    table = _RELEVANCE_CACHE.get("data") or {}
    if not table:
        return None
    pref_key = frozenset((k, float(v)) for k, v in user_prefs.items() if v)
    flag = table.get((keyword, pref_key, rest_name))
    if flag is None:
        return None
    return "relevant" if flag == 1 else "not_relevant"


def _rep_image_for(kw: str, rest_name: str, top_axes: list[str] | None = None) -> dict:
    """
    Inference 카드용 식당 대표 이미지 1장.

    레이어 분리:
      - Layer 1: Gallery 대표 이미지 6장(REP_IMAGES) 중 같은 식당이 있으면 그걸 사용.
        (이미 axis-aware로 선정된 best image)
      - Layer 2: 없으면 DB에서 `/reviews/<kw>/` prefix clean image를 식당 단위로 검색.
        Gallery 6장에 포함되지 않은 식당도 자기 이미지를 표시할 수 있게 함.
      - 없으면 빈 dict.

    image_src의 keyword prefix는 항상 검증 — DB가 오염돼 있어도 prefix가 어긋난
    이미지는 절대 노출하지 않는다.
    """
    # ── Layer 1: Gallery REP_IMAGES (in-process cache) ──
    bundle = REP_IMAGES.get(kw) or {}
    for img in bundle.get("images", []):
        if img.get("restaurant") == rest_name:
            src = img.get("image_src", "")
            if _image_src_matches_keyword(src, kw):
                return {
                    "image_src": src,
                    "axis": img.get("axis", ""),
                    "label": img.get("label", ""),
                    "review_snippet": img.get("review_snippet", ""),
                    "source": "gallery_rep_image",
                }

    # ── Layer 2: DB에서 식당 단위 clean image fallback ──
    ri = _rest_info(rest_name) or {}
    rest_id = ri.get("restaurant_id")
    if not rest_id:
        return {}
    try:
        with get_conn() as conn:
            cand = get_best_restaurant_image(conn, kw, int(rest_id), top_axes=top_axes)
    except Exception as e:
        print(f"[_rep_image_for] DB fallback 실패 ({rest_name}): {type(e).__name__}")
        return {}

    if not cand:
        return {}
    src = cand.get("image_src", "")
    if not _image_src_matches_keyword(src, kw):
        return {}
    return {
        "image_src": src,
        "axis": "",
        "label": "",
        "review_snippet": cand.get("review_snippet", ""),
        "source": cand.get("source", "restaurant_clean_image_fallback"),
    }


@app.route("/api/recommend", methods=["POST"])
def api_recommend():
    body = request.get_json(silent=True)
    if not body:
        return jsonify({"error": "request body 없음"}), 400

    if "text_preferences" in body:
        kw              = _norm_kw(body.get("_keyword", DEFAULT_KW))
        user_prefs      = body.get("text_preferences", {})
        selected_images = body.get("selected_images", [])
        use_fusion      = body.get("use_image_fusion", False)
    else:
        kw              = _norm_kw(body.get("_keyword", DEFAULT_KW))
        user_prefs      = {k:v for k,v in body.items() if k != "_keyword"}
        selected_images = []
        use_fusion      = False

    bundle = ALL_DATA.get(kw)
    if not bundle:
        return jsonify({"error": f"'{kw}' 키워드 없음"}), 404

    all_axes = _all_axes(bundle)
    if not all_axes:
        return jsonify({"error": "축 정보 없음"}), 500

    taste_axes, meta_axes = _split_axes(bundle)
    label_map = _axis_label_map(bundle)

    # 사용자 벡터
    user_tv = {ax: float(user_prefs.get(ax, 0.0)) for ax in all_axes}
    if use_fusion and selected_images:
        user_iv = _user_img_vec(selected_images, all_axes)
        user_vec_dict, user_weights, fusion_mode = _fuse_dynamic(user_tv, user_iv, all_axes)
    else:
        user_vec_dict, user_weights, fusion_mode = user_tv, {"text": 1.0, "image": 0.0}, "text_only"

    user_taste = [user_vec_dict.get(ax, 0.0) for ax in taste_axes]
    user_meta  = [user_vec_dict.get(ax, 0.0) for ax in meta_axes]

    results = []
    for name, info in (bundle.get("restaurants") or {}).items():
        if not isinstance(info, dict):
            continue

        # 식당 벡터: fused → text → normalized 우선순위
        vec_dict = None
        for key in ("fused_vector", "text_only_vector", "normalized"):
            if isinstance(info.get(key), dict):
                vec_dict = info[key]
                break
        if vec_dict is None:
            continue

        text_vec = info.get("text_only_vector") or vec_dict
        img_vec  = info.get("image_sentiment") or {}

        rest_taste = [float(vec_dict.get(ax, 0.0)) for ax in taste_axes]
        rest_meta  = [float(vec_dict.get(ax, 0.0)) for ax in meta_axes]

        # taste cosine 본체 + meta 부스트
        taste_sim = _cosine(user_taste, rest_taste)
        meta_sim  = _cosine(user_meta, rest_meta) if meta_axes else 0.0
        sim = taste_sim + META_BOOST * max(meta_sim, 0)
        sim = max(-1.0, min(1.0, sim))

        # axis 기여도 (taste 축만)
        contrib = {}
        for ax in taste_axes:
            u = float(user_vec_dict.get(ax, 0.0))
            r = float(vec_dict.get(ax, 0.0))
            contrib[ax] = u * r
        contrib_sum = sum(abs(v) for v in contrib.values())
        if contrib_sum > 1e-6:
            axis_contributions = {ax: round(abs(v) / contrib_sum, 3)
                                  for ax, v in contrib.items() if abs(v) > 0.001}
        else:
            axis_contributions = {}

        top_axes = sorted(axis_contributions.items(), key=lambda x: -x[1])[:5]
        top_axes_names = [ax for ax, _ in top_axes]

        # ── display_axis_policy.allowed 는 top_axes·axis_contributions 에 적용 안 함 ──
        # 레이더 차트/추천 카드 top tag 의 맛축 다양성을 유지하기 위해 whitelist 컷오프 제거.
        # 점수 계산(cosine/similarity/match_percent)은 위에서 이미 끝났고 영향 없음.
        # 화면의 '근거' 영역만 evidence_sentences 필터로 따로 다룬다.

        # confidence
        text_conf = _confidence(text_vec, taste_axes)
        img_conf  = _confidence(img_vec, taste_axes) if info.get("has_image_data") else 0.0
        total_conf = text_conf + img_conf

        # raw confidence 비율은 그대로 계산해 raw_fusion_weights 로 노출.
        # 화면 표시용 fusion_weights / text_evidence_ratio / image_evidence_ratio 는
        # TasteBridge가 리뷰 기반 추천 시스템이라는 점을 반영해 raw image 비율을
        # [0, 0.4] 범위로 선형 매핑한다 — 텍스트가 항상 이미지보다 높게 보이지만
        # 식당별 raw confidence 차이(7:93 vs 30:70 등)는 그대로 보존된다.
        # 추천 점수 계산(cosine/similarity/match_percent)에는 영향이 없다.
        if total_conf > 1e-6 and img_conf > 0:
            raw_text_w = text_conf / total_conf
            raw_img_w  = 1.0 - raw_text_w
            img_w  = round(raw_img_w * 0.4, 3)
            text_w = round(1.0 - img_w, 3)
        else:
            raw_text_w, raw_img_w = 1.0, 0.0
            text_w, img_w = 1.0, 0.0

        # ── axis별 evidence ──
        # display_axis_policy 허용 + 실제 문장이 있는 축만 통과.
        # 비어있는 축에 다른 리뷰 문장을 억지로 끌어오지 않는다.
        evidence_map = info.get("evidence") or {}
        evidence_sentences = _filter_evidence_by_display_axes(
            kw,
            {ax: evidence_map.get(ax, []) for ax in top_axes_names},
        )
        # top_axes_names 중 실제 evidence_sentences에 살아남은 축만 최대 2문장.
        evidence_sentences = {ax: (sents or [])[:2]
                              for ax, sents in evidence_sentences.items()}

        # flat evidence (fallback 호환) — 살아남은 evidence_sentences만 평탄화.
        flat_evidence: list = []
        for ax in top_axes_names:
            for s in evidence_sentences.get(ax, []):
                if s and s not in flat_evidence:
                    flat_evidence.append(s)
                if len(flat_evidence) >= 3:
                    break
            if len(flat_evidence) >= 3:
                break

        # axis_scores (식당의 모든 축별 점수 - 맛 + 메타)
        # 추천 점수/기여도 계산은 taste_axes 중심으로 유지하되,
        # 결과 카드 레이더는 사용자가 선택한 축을 그대로 그릴 수 있게 all_axes를 내려준다.
        axis_scores = {ax: round(float(vec_dict.get(ax, 0.0)), 4) for ax in all_axes}

        ri = _rest_info(name)
        naver_url = ri.get("naver_url", "") or ""
        fallback_url = _build_fallback_url(name, kw)

        # 대표 이미지 — Gallery 6장에 없으면 식당 단위 clean image fallback (db lookup)
        # 추천 카드의 대표 썸네일은 "축 선택 카드"가 아닌 "식당 대표 사진"이므로
        # display_axis_policy로 필터링하지 않는다. _rep_image_for() 결과를 그대로 사용.
        rep_img = _rep_image_for(kw, name, top_axes=top_axes_names[:3])

        # debug_reason
        if top_axes:
            top_names = ", ".join([ax for ax, _ in top_axes[:2]])
            top_pct = sum(v for _, v in top_axes[:2]) * 100
            debug_reason = f"{top_names} 축에서 일치 (상위 2축 기여도 {top_pct:.0f}%)"
        else:
            debug_reason = "유의미한 축 일치 없음"

        # pseudo-label lookup (없으면 None)
        pseudo_rel = _pseudo_relevance(kw, user_prefs, name)

        results.append({
            # 기존 필드 (frontend fallback 호환)
            "name":           name,
            "similarity":     round(float(sim), 4),
            "address":        ri.get("road_address") or ri.get("address",""),
            "phone":          ri.get("phone",""),
            "naver_url":      naver_url,
            "category":       ri.get("category",""),
            "evidence":       flat_evidence[:3],
            "reasons":        top_axes_names[:5],
            "fusion_mode":    fusion_mode,
            "has_image_data": bool(info.get("has_image_data", False)),
            # 신규 필드
            "similarity_score":   round(float(sim), 4),
            "rank_score":         round(float(sim), 4),
            "match_percent":      max(0, min(100, round(float(sim) * 100))),
            "top_axes":           top_axes_names[:3],
            "top_axes_labels":    [label_map.get(a, a) for a in top_axes_names[:3]],
            "axis_scores":        axis_scores,
            "axis_contributions": axis_contributions,
            "axis_contributions_labels": {label_map.get(a, a): v for a, v in axis_contributions.items()},
            "axis_label_map":     label_map,
            "text_confidence":    round(text_conf, 3),
            "image_confidence":   round(img_conf, 3),
            "fusion_weights":     {"text": text_w, "image": img_w},
            "raw_fusion_weights": {"text": round(float(raw_text_w), 3),
                                    "image": round(float(raw_img_w), 3)},
            "text_evidence_ratio":  text_w,
            "image_evidence_ratio": img_w,
            "evidence_sentences": evidence_sentences,
            "evidence_sentences_labels": {label_map.get(a, a): v for a, v in evidence_sentences.items()},
            "representative_image": rep_img,
            "place_url":          naver_url,
            "naver_place_url":    naver_url,
            "fallback_search_url": fallback_url,
            "pseudo_relevance":   pseudo_rel,
            "debug_reason":       debug_reason,
        })

    results.sort(key=lambda x: x["similarity"], reverse=True)
    for i, item in enumerate(results, start=1):
        item["rank"] = i

    # Top-N 결정: ?limit=... query param (기본 5, 1~50 클램프).
    # 추천 결과를 화면에 너무 많이 노출하지 않도록 백엔드 단에서 1차 제한.
    try:
        limit_req = int(request.args.get("limit", DEFAULT_RESULT_LIMIT))
    except (TypeError, ValueError):
        limit_req = DEFAULT_RESULT_LIMIT
    limit = max(1, min(MAX_RESULT_LIMIT, limit_req))

    return jsonify(_clean({
        "keyword":         kw,
        "fusion_mode":     fusion_mode,
        "user_weights":    user_weights,
        "taste_axes":      taste_axes,
        "meta_axes":       meta_axes,
        "axis_label_map":  label_map,
        "count":           len(results),
        "limit":           limit,
        "results":         results[:limit],
        "user_vector":     user_vec_dict,
        "label_type":      _RELEVANCE_CACHE.get("label_type", "pseudo-label"),
    }))


@app.route("/api/evaluation", methods=["GET"])
def api_evaluation():
    """
    evaluation.py가 만든 evaluation_results.json을 그대로 내려준다.
    파일이 없거나 읽기 실패해도 항상 200 OK + ok:false 형태로 응답
    (frontend는 ok:false면 카드를 숨김 또는 안내 메시지 표시).
    """
    path = os.path.join(BASE_DIR, "evaluation_results.json")
    if not os.path.exists(path):
        return jsonify({
            "ok": False,
            "available": False,
            "message": "evaluation_results.json이 없습니다. evaluation.py를 먼저 실행하세요.",
            "hint": "python evaluation.py --labels eval_labels.csv --k 5 --output evaluation_results.json",
        })
    try:
        with open(path, "r", encoding="utf-8") as f:
            data = json.load(f)
        data.setdefault("ok", True)
        data.setdefault("available", True)
        return jsonify(data)
    except Exception as e:
        return jsonify({
            "ok": False,
            "available": False,
            "message": f"읽기 실패: {type(e).__name__}",
        })


@app.route("/api/reload", methods=["POST"])
def api_reload():
    global ALL_DATA, REP_IMAGES, REST_DB, DEFAULT_KW
    try:
        ALL_DATA, REP_IMAGES, REST_DB = _load_all_data()
        DEFAULT_KW = (DEFAULT_KEYWORD_PREF if DEFAULT_KEYWORD_PREF in ALL_DATA
                      else (sorted(ALL_DATA.keys())[0] if ALL_DATA else None))
        return jsonify({"ok":True,"keywords":sorted(ALL_DATA.keys()),"default":DEFAULT_KW})
    except Exception as e:
        return jsonify({"ok":False,"error":str(e)}), 500


if __name__ == "__main__":
    port  = int(os.environ.get("PORT", 5000))
    debug = os.environ.get("FLASK_DEBUG","1") == "1"
    print(f"🚀 http://127.0.0.1:{port}  debug={debug}  keyword={DEFAULT_KW}")
    app.run(debug=debug, host="127.0.0.1", port=port)
