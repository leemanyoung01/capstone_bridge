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
BERT_EVIDENCE_GATE = 0.40  # BERT 근거 문장 채택 최소 score(pos-neg diff). generic 노이즈 차단
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
                sv   = p.get("semantic_vector") or {}
                se   = p.get("semantic_evidence") or {}
                restaurants[name] = {
                    "fused_vector":     fv if fv else tv,
                    "text_only_vector": tv,
                    "image_sentiment":  iv,
                    "semantic_vector":  sv,
                    "semantic_evidence": se,
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
    kw = str(kw or "").strip().lower()
    kw = kw[:-len("_multimodal")] if kw.endswith("_multimodal") else kw

    # 이미 정규 키워드면 그대로
    if kw in ALL_DATA:
        return kw

    # stand_axes의 별명 사전으로 정규 키워드 해석 (오타·줄임말·하위메뉴 → 루트 카테고리)
    stand = _load_stand_axes()
    alias_to_category = stand.get("alias_to_category", {})
    if kw in alias_to_category:
        canonical = alias_to_category[kw]
        if canonical in ALL_DATA:
            return canonical

    aliases = stand.get("keyword_aliases", {})
    for canonical, alias_group in aliases.items():
        if kw == canonical or kw in alias_group:
            if canonical in ALL_DATA:
                return canonical
            for word in alias_group:
                if word in ALL_DATA:
                    return word

    return kw


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
    """axes_config의 label 필드에서 {axis_key: display_label} 맵을 만든다.
    DB label에 띄어쓰기가 빠졌으면 stand_axes의 표준 label로 보정."""
    out = {}
    stand = _load_stand_axes()
    stand_labels = {}
    if stand:
        for axes in (stand.get("food_specific_axes") or {}).values():
            if isinstance(axes, dict):
                for ax, i in axes.items():
                    if isinstance(i, dict) and i.get("label"):
                        stand_labels[ax] = i["label"]
        for ax, i in (stand.get("taste_axes") or {}).items():
            if isinstance(i, dict) and i.get("label"):
                stand_labels[ax] = i["label"]
        for ax, i in (stand.get("meta_axes") or {}).items():
            if isinstance(i, dict) and i.get("label"):
                stand_labels[ax] = i["label"]

    for name, info in (bundle.get("axes_config") or {}).items():
        if name.startswith("_"):
            continue
        db_label = info.get("label")
        # DB label이 비었거나 key와 동일(띄어쓰기 누락 가능)하면 stand 표준 label 우선
        if name in stand_labels and (not db_label or db_label == name):
            out[name] = stand_labels[name]
        else:
            out[name] = db_label or name
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


def _clamp01(v: float) -> float:
    return max(0.0, min(1.0, float(v)))


def _axis_preference_match(user_vec: dict, target_vec: dict, axes: list[str]) -> float:
    """User-selected axis direction vs a restaurant modality vector, normalized to 0~1."""
    if not user_vec or not target_vec or not axes:
        return 0.0

    weighted, total = 0.0, 0.0
    for ax in axes:
        u = float(user_vec.get(ax, 0.0) or 0.0)
        if abs(u) <= 0.001:
            continue
        r = max(-1.0, min(1.0, float(target_vec.get(ax, 0.0) or 0.0)))
        axis_score = (r + 1.0) / 2.0 if u >= 0 else (1.0 - r) / 2.0
        weighted += abs(u) * axis_score
        total += abs(u)

    if total > 1e-6:
        return _clamp01(weighted / total)

    u_arr = [float(user_vec.get(ax, 0.0) or 0.0) for ax in axes]
    r_arr = [float(target_vec.get(ax, 0.0) or 0.0) for ax in axes]
    return _clamp01((_cosine(u_arr, r_arr) + 1.0) / 2.0)


def _relative_support(value: float, max_value: float, floor: float = 0.55) -> float:
    """Weakly supported restaurants still count, but high-review restaurants get a small lift."""
    if max_value <= 0:
        return floor
    return _clamp01(floor + (1.0 - floor) * math.sqrt(max(0.0, float(value)) / max_value))


def _user_img_vec(selected_images, axes):
    scores = {ax:[] for ax in axes}
    selected_axes = set()
    for img in selected_images:
        cv, ma = img.get("clip_vector",{}) or {}, img.get("axis","")
        for ax in axes:
            v = cv.get(ax,0.0)
            if isinstance(v,(int,float)): scores[ax].append(float(v))
        if ma in scores:
            scores[ma].append(1.5)
            selected_axes.add(ma)
    result = {}
    for ax, vs in scores.items():
        if not vs:
            result[ax] = 0.0
            continue
        mean_val = float(np.mean(vs))
        if ax not in selected_axes:
            mean_val *= 0.5
        result[ax] = round(mean_val, 4)
    return result


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


# ── 축별 추출 정확도 (작업 A) ───────────────────────────────────
# 식당 × 축마다 실제 리뷰에서 lex 키워드가 얼마나 잡혔는지,
# BERT semantic 점수는 얼마인지를 한 번에 보여주는 endpoint.
# 프론트의 '계산 근거' 토글에서 lazy-load해서 표시.
@app.route("/api/axis_stats")
def api_axis_stats():
    restaurant = (request.args.get("restaurant") or "").strip()
    kw         = _norm_kw(request.args.get("keyword", DEFAULT_KW))
    if not restaurant:
        return jsonify({"error": "restaurant required"}), 400

    bundle = ALL_DATA.get(kw)
    if not bundle:
        return jsonify({"error": f"'{kw}' 키워드 없음"}), 404

    cfg = bundle.get("axes_config") or {}
    rest_info = (bundle.get("restaurants") or {}).get(restaurant)
    if not rest_info:
        return jsonify({"error": f"'{restaurant}' 데이터 없음"}), 404

    text_vec = rest_info.get("text_only_vector") or {}
    sem_vec  = rest_info.get("semantic_vector") or {}
    evidence = rest_info.get("evidence") or {}
    sem_evidence = rest_info.get("semantic_evidence") or {}

    # 실제 리뷰 본문을 DB에서 fetch — 카운트용.
    # 주의: crawl_keyword로 필터하면 안 됨. 일부 키워드(예: 김치찌개)는
    # 리뷰가 crawl_keyword로 태깅 안 되고 식당명/이미지 prefix로만 매칭됨
    # (Food_profiler의 source boundary 참고). 식당은 이미 이 키워드 bundle에
    # 속하므로, 그 식당의 모든 리뷰를 가져오면 됨.
    reviews_texts: list[str] = []
    try:
        with get_conn() as conn, conn.cursor() as cur:
            cur.execute("""
                SELECT rev.content FROM reviews rev
                JOIN restaurants r ON r.restaurant_id = rev.restaurant_id
                WHERE r.name = %s
                  AND rev.content IS NOT NULL AND rev.content != ''
            """, (restaurant,))
            reviews_texts = [r["content"] for r in cur.fetchall()]
    except Exception as e:
        return jsonify({"error": f"DB read failed: {e}"}), 500

    total = len(reviews_texts)

    axes_stats = []
    for axis_name, axis_info in cfg.items():
        if axis_info.get("is_meta"):
            continue
        pos_kws = axis_info.get("positive_keywords", []) or []
        neg_kws = axis_info.get("negative_keywords", []) or []

        pos_hits = sum(1 for r in reviews_texts if any(kw_str and kw_str in r for kw_str in pos_kws))
        neg_hits = sum(1 for r in reviews_texts if any(kw_str and kw_str in r for kw_str in neg_kws))
        total_hits = pos_hits + neg_hits

        tv_score = text_vec.get(axis_name)
        sv_score = sem_vec.get(axis_name)
        ev_list = evidence.get(axis_name, []) or []
        # BERT 의미 근거 (semantic_text_profiler가 저장한 문장) — 강한 매칭만(노이즈 차단)
        sem_ev = sem_evidence.get(axis_name, []) or []
        sem_ev_sentences = [e.get("sentence", "") for e in sem_ev
                            if isinstance(e, dict)
                            and float(e.get("score") or 0) >= BERT_EVIDENCE_GATE]

        axes_stats.append({
            "axis":  axis_name,
            "label": axis_info.get("label", axis_name) if isinstance(axis_info, dict) else axis_name,
            "group": axis_info.get("group", "기타"),
            "positive_hits": pos_hits,
            "negative_hits": neg_hits,
            "total_hits":    total_hits,
            "hit_rate":      round(total_hits / total, 4) if total else 0.0,
            "lex_keywords_count": len(pos_kws) + len(neg_kws),
            "semantic_evidence_count": len(sem_ev),
            "semantic_evidence_samples": sem_ev_sentences[:3],
            "text_vector_score":     (round(float(tv_score), 4) if tv_score is not None else None),
            "semantic_vector_score": (round(float(sv_score), 4) if sv_score is not None else None),
            "evidence_count":  len(ev_list),
            "evidence_samples": ev_list[:3],
            "positive_keywords": pos_kws,
            "negative_keywords": neg_kws,
        })

    # 축 정렬: total_hits 많은 순
    axes_stats.sort(key=lambda x: -x["total_hits"])

    return jsonify({
        "restaurant":    restaurant,
        "keyword":       kw,
        "total_reviews": total,
        "axes":          axes_stats,
    })


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
        seen_pair: set = set()
        seen_src: set = set()
        out: list = []
        skipped_mismatch = 0
        for img in raw_imgs:
            src = img.get("image_src", "")
            if not _image_src_matches_keyword(src, target_kw):
                skipped_mismatch += 1
                continue
            pair = (img.get("restaurant", ""), img.get("axis", ""))
            if pair in seen_pair or (src and src in seen_src):
                continue
            seen_pair.add(pair)
            if src:
                seen_src.add(src)
            ax = img.get("axis", "")
            if ax and "axis_label" not in img:
                img["axis_label"] = label_map.get(ax) or img.get("label") or ax
            out.append(img)

        returned_count = len(out)
        regeneration_needed = (returned_count == 0)

        # 진단 로그 (CLIP 등 무거운 연산은 안 함 — 카운트만)
        print(f"[api_rep_images] keyword={target_kw} "
              f"db_loaded={db_loaded_count} returned={returned_count} "
              f"prefix-skipped={skipped_mismatch}")
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
        use_semantic    = body.get("use_semantic", True)   # BERT semantic 가산 (default ON)
        semantic_weight = float(body.get("semantic_weight", 0.5))  # 0=baseline, 1=semantic only
        # 평가 ablation용: false면 식당 벡터에서 image_vector 영향 제거 (text_only_vector만 사용)
        use_image       = bool(body.get("use_image", True))
    else:
        kw              = _norm_kw(body.get("_keyword", DEFAULT_KW))
        user_prefs      = {k:v for k,v in body.items() if k != "_keyword"}
        selected_images = []
        use_fusion      = False
        use_semantic    = True
        semantic_weight = 0.5
        use_image       = True

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
    user_iv = {}
    if use_fusion and selected_images:
        user_iv = _user_img_vec(selected_images, all_axes)
        user_vec_dict, user_weights, fusion_mode = _fuse_dynamic(user_tv, user_iv, all_axes)
    else:
        user_vec_dict, user_weights, fusion_mode = user_tv, {"text": 1.0, "image": 0.0}, "text_only"

    user_taste = [user_vec_dict.get(ax, 0.0) for ax in taste_axes]
    user_meta  = [user_vec_dict.get(ax, 0.0) for ax in meta_axes]

    restaurants = bundle.get("restaurants") or {}
    max_keyword_reviews = max(
        [float(info.get("keyword_reviews", 0) or 0) for info in restaurants.values() if isinstance(info, dict)] or [1.0]
    )

    results = []
    for name, info in restaurants.items():
        if not isinstance(info, dict):
            continue

        # 식당 벡터: 4-way ablation을 위해 use_image 따라 후보 키 결정
        #   use_image=True  → fused → text → normalized (CLIP image 포함)
        #   use_image=False → text_only_vector → normalized (lex만)
        vec_dict = None
        candidate_keys = (("fused_vector", "text_only_vector", "normalized")
                          if use_image
                          else ("text_only_vector", "normalized"))
        for key in candidate_keys:
            if isinstance(info.get(key), dict):
                vec_dict = info[key]
                break
        if vec_dict is None:
            continue

        text_vec = info.get("text_only_vector") or vec_dict
        img_vec  = info.get("image_sentiment") or {}
        sem_vec  = info.get("semantic_vector") or {}

        # ── BERT semantic 가산 (옵트인) ───────────────────────────
        # use_semantic이 False면 baseline 동작.
        # True이고 semantic_vector가 있으면 (1-w)*baseline + w*semantic 으로 fuse.
        # 평가 파이프라인에서 toggle 가능 → before/after 비교용.
        if use_semantic and sem_vec:
            w = max(0.0, min(1.0, semantic_weight))
            fused_with_sem = {}
            for ax in set(vec_dict.keys()) | set(sem_vec.keys()):
                if str(ax).startswith("_"):
                    fused_with_sem[ax] = vec_dict.get(ax, 0.0)
                    continue
                v = (1.0 - w) * float(vec_dict.get(ax, 0.0)) + w * float(sem_vec.get(ax, 0.0))
                fused_with_sem[ax] = round(v, 4)
            vec_dict = fused_with_sem

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

        # Display-only evidence ratio.
        # Keep ranking/match_percent on the fused cosine above; only explain how much
        # each restaurant is supported by text vs image evidence for this user input.
        text_explain_vec = text_vec
        if use_semantic and sem_vec:
            w = max(0.0, min(1.0, semantic_weight))
            text_explain_vec = {}
            for ax in set(text_vec.keys()) | set(sem_vec.keys()):
                if str(ax).startswith("_"):
                    text_explain_vec[ax] = text_vec.get(ax, 0.0)
                    continue
                text_explain_vec[ax] = round(
                    (1.0 - w) * float(text_vec.get(ax, 0.0) or 0.0)
                    + w * float(sem_vec.get(ax, 0.0) or 0.0),
                    4,
                )

        text_conf = _confidence(text_explain_vec, taste_axes)
        img_conf  = _confidence(img_vec, taste_axes) if info.get("has_image_data") else 0.0
        total_conf = text_conf + img_conf
        if total_conf > 1e-6:
            profile_text_w = round(text_conf / total_conf, 3)
            profile_img_w  = round(1.0 - profile_text_w, 3)
        else:
            profile_text_w, profile_img_w = 1.0, 0.0

        input_text_w = round(float(user_weights.get("text", 1.0)), 3)
        input_img_w  = round(float(user_weights.get("image", 0.0)), 3)
        text_match_score = _axis_preference_match(user_tv, text_explain_vec, taste_axes)
        image_match_score = (
            _axis_preference_match(user_iv, img_vec, taste_axes)
            if input_img_w > 0 and info.get("has_image_data")
            else 0.0
        )
        text_support = _relative_support(info.get("keyword_reviews", 0) or 0, max_keyword_reviews)
        image_support = _clamp01(float(info.get("image_coverage", 0) or 0)) if info.get("has_image_data") else 0.0

        text_basis = input_text_w * text_match_score * text_support
        image_basis = input_img_w * image_match_score * image_support
        basis_total = text_basis + image_basis
        if basis_total > 1e-6:
            text_w = round(text_basis / basis_total, 3)
            img_w = round(1.0 - text_w, 3)
        else:
            text_w, img_w = 1.0, 0.0

        # axis별 evidence — 메인 근거는 '사용자가 고른 축'만 (표/BERT섹션과 동일 기준).
        # lex 근거 우선, lex가 못 잡은 고른 축은 BERT 강한 근거(0.40+)로 채움.
        evidence_map = info.get("evidence") or {}            # lex: {ax: [str]}
        sem_ev_map   = info.get("semantic_evidence") or {}   # BERT: {ax: [{sentence,score}]}
        evidence_sentences = {}
        # 고른 축만. 안 고르면(이미지만 선택 등) 기여 상위 축으로 폴백.
        pref_axes = [ax for ax in all_axes if float(user_prefs.get(ax, 0) or 0) > 0]
        ev_axes = pref_axes if pref_axes else list(top_axes_names)
        for ax in ev_axes:
            lex_sents = evidence_map.get(ax) or []
            if lex_sents:
                evidence_sentences[ax] = lex_sents[:2]
            else:
                sem_sents = [e.get("sentence", "") for e in (sem_ev_map.get(ax) or [])
                             if isinstance(e, dict) and e.get("sentence")
                             and float(e.get("score") or 0) >= BERT_EVIDENCE_GATE]
                if sem_sents:
                    evidence_sentences[ax] = sem_sents[:1]

        # flat evidence (fallback 호환)
        flat_evidence = []
        for ax in top_axes_names:
            for s in evidence_map.get(ax, []):
                if s and s not in flat_evidence:
                    flat_evidence.append(s)
                if len(flat_evidence) >= 3:
                    break
            if len(flat_evidence) >= 3:
                break

        # axis_scores (식당의 모든 축별 점수 - 맛 + 메타)
        axis_scores = {ax: round(float(vec_dict.get(ax, 0.0)), 4) for ax in all_axes}

        ri = _rest_info(name)
        naver_url = ri.get("naver_url", "") or ""
        fallback_url = _build_fallback_url(name, kw)

        # 대표 이미지 — Gallery 6장에 없으면 식당 단위 clean image fallback (db lookup)
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

        # ── '계산 근거' 묶음 (B 작업) ──
        # 정직한 분해: match_percent는 코사인 기반.
        # text_basis/image_basis는 별도 — '이 점수의 텍스트/이미지 영향 비중' 표시용.
        # (이전 버전에서 두 개를 혼동해서 표시한 것을 수정)
        match_breakdown = {
            "final_percent": max(0, min(100, round(float(sim) * 100))),

            # ▶ 메인 점수 (랭킹/match_percent 산출식)
            "main": {
                "formula": "match% = clamp(taste_cosine + META_BOOST × max(0, meta_cosine)) × 100",
                "taste_cosine":      round(float(taste_sim), 4),
                "meta_cosine":       round(float(meta_sim), 4),
                "meta_boost_weight": META_BOOST,
                "meta_boost_amount": round(float(META_BOOST * max(meta_sim, 0)), 4),
                "sim_raw":           round(float(taste_sim + META_BOOST * max(meta_sim, 0)), 4),
                "sim_clamped":       round(float(sim), 4),
            },

            # ▶ 텍스트 vs 이미지 영향 비중 (UI의 '텍스트 N% / 이미지 M%' 출처)
            "evidence_split": {
                "note": "위 일치도와 별개 — 이 식당에서 텍스트/이미지가 각각 얼마나 기여했는지의 ratio 표시용",
                "text_basis": {
                    "value":  round(text_basis, 4),
                    "formula": "input_text_w × text_match_score × text_support",
                    "input_text_w":     round(input_text_w, 3),
                    "text_match_score": round(text_match_score, 4),
                    "text_support":     round(text_support, 4),
                },
                "image_basis": {
                    "value":  round(image_basis, 4),
                    "formula": "input_img_w × image_match_score × image_support",
                    "input_img_w":       round(input_img_w, 3),
                    "image_match_score": round(image_match_score, 4),
                    "image_support":     round(image_support, 4),
                },
                "basis_total":  round(basis_total, 4),
                "text_ratio":   text_w,
                "image_ratio":  img_w,
            },

            "model_variant": ("semantic" if use_semantic else "baseline"),
            "semantic_weight": (semantic_weight if use_semantic else 0.0),
            "use_image": use_image,
            "ablation_tag": f"{'I' if use_image else '-'}{'S' if use_semantic else '-'}",
        }

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
            "match_breakdown":    match_breakdown,
            "top_axes":           top_axes_names[:3],
            "top_axes_labels":    [label_map.get(a, a) for a in top_axes_names[:3]],
            "axis_scores":        axis_scores,
            "axis_contributions": axis_contributions,
            "axis_contributions_labels": {label_map.get(a, a): v for a, v in axis_contributions.items()},
            "axis_label_map":     label_map,
            "text_confidence":    round(text_conf, 3),
            "image_confidence":   round(img_conf, 3),
            "fusion_weights":     {"text": text_w, "image": img_w},
            "text_evidence_ratio":  text_w,
            "image_evidence_ratio": img_w,
            "input_fusion_weights": {"text": input_text_w, "image": input_img_w},
            "profile_fusion_weights": {"text": profile_text_w, "image": profile_img_w},
            "profile_text_evidence_ratio":  profile_text_w,
            "profile_image_evidence_ratio": profile_img_w,
            "text_match_score": round(text_match_score, 4),
            "image_match_score": round(image_match_score, 4),
            "text_support": round(text_support, 4),
            "image_support": round(image_support, 4),
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
        "model_variant":   ("semantic" if use_semantic else "baseline"),
        "semantic_weight": semantic_weight if use_semantic else 0.0,
        "use_image":       use_image,
        "ablation_tag":    f"{'I' if use_image else '-'}{'S' if use_semantic else '-'}",
    }))


@app.route("/api/evaluation", methods=["GET"])
def api_evaluation():
    """
    evaluation.py가 만든 evaluation_results.json을 그대로 내려준다.
    파일이 없거나 읽기 실패해도 항상 200 OK + ok:false 형태로 응답
    (frontend는 ok:false면 카드를 숨김 또는 안내 메시지 표시).
    """
    # evaluate_ranking.py는 eval/ 에 저장. 옛 evaluation.py는 루트에 저장 → 둘 다 시도.
    candidates = [
        os.path.join(BASE_DIR, "eval", "evaluation_results.json"),
        os.path.join(BASE_DIR, "evaluation_results.json"),
    ]
    path = next((p for p in candidates if os.path.exists(p)), None)
    if path is None:
        return jsonify({
            "ok": False,
            "available": False,
            "message": "evaluation_results.json이 없습니다. evaluate_ranking.py를 먼저 실행하세요.",
            "hint": "python scripts/evaluate_ranking.py <키워드들>",
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
    port  = int(os.environ.get("PORT", 5050))  # 5000은 Neo4j Desktop이 점유 → 5050으로 변경
    debug = os.environ.get("FLASK_DEBUG","1") == "1"
    print(f"🚀 http://127.0.0.1:{port}  debug={debug}  keyword={DEFAULT_KW}")
    app.run(debug=debug, host="127.0.0.1", port=port, threaded=True)
