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
import sys
sys.stdout.reconfigure(encoding='utf-8')
import csv
import json, math, os
from urllib.parse import quote
import numpy as np
from flask import Flask, jsonify, request, send_file, send_from_directory

from db import (
    get_conn,
    get_all_restaurants,
    get_profiles_by_keyword,
    get_all_keywords,
    get_axes_config,
    get_representative_images,
    get_all_rep_keywords,
)

BASE_DIR   = os.path.dirname(os.path.abspath(__file__))
IMAGES_DIR = os.path.join(BASE_DIR, "images")

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
    for fn in ("survey.html","index.html"):
        p = os.path.join(BASE_DIR, fn)
        if os.path.exists(p): return send_file(p)
    return "<h1>survey.html을 같은 폴더에 놓아주세요</h1>", 404


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

    groups = bundle.get("groups", {})
    if not groups:
        from collections import defaultdict
        groups = defaultdict(list)
        for axis_name, info in bundle.get("axes_config", {}).items():
            groups[info.get("group", "기타")].append(axis_name)
        groups = dict(groups)

    taste, meta = _split_axes(bundle)
    return jsonify({
        "keyword":     kw,
        "axes":        _all_axes(bundle),
        "taste_axes":  taste,
        "meta_axes":   meta,
        "groups":      groups,
        "axes_config": bundle.get("axes_config", {}),
    })


@app.route("/api/representative_images")
def api_rep_images():
    kw = _norm_kw(request.args.get("keyword", DEFAULT_KW))
    if kw in REP_IMAGES: return jsonify(REP_IMAGES[kw])
    for k, v in REP_IMAGES.items():
        if kw in k or k in kw: return jsonify(v)
    return jsonify({"keyword": kw, "images": []})


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


def _rep_image_for(kw: str, rest_name: str) -> dict:
    """해당 식당의 대표 이미지 1장 (없으면 빈 dict)."""
    bundle = REP_IMAGES.get(kw)
    if not bundle:
        return {}
    for img in bundle.get("images", []):
        if img.get("restaurant") == rest_name:
            return {
                "image_src": img.get("image_src", ""),
                "axis": img.get("axis", ""),
                "label": img.get("label", ""),
                "review_snippet": img.get("review_snippet", ""),
            }
    return {}


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

        # confidence
        text_conf = _confidence(text_vec, taste_axes)
        img_conf  = _confidence(img_vec, taste_axes) if info.get("has_image_data") else 0.0
        total_conf = text_conf + img_conf
        if total_conf > 1e-6:
            text_w = round(text_conf / total_conf, 3)
            img_w  = round(1.0 - text_w, 3)
        else:
            text_w, img_w = 1.0, 0.0

        # axis별 evidence
        evidence_map = info.get("evidence") or {}
        evidence_sentences = {ax: evidence_map.get(ax, [])[:2]
                              for ax in top_axes_names if evidence_map.get(ax)}

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

        # axis_scores (식당의 taste 축별 점수)
        axis_scores = {ax: round(float(vec_dict.get(ax, 0.0)), 4) for ax in taste_axes}

        ri = _rest_info(name)
        naver_url = ri.get("naver_url", "") or ""
        fallback_url = _build_fallback_url(name, kw)

        # 대표 이미지
        rep_img = _rep_image_for(kw, name)

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
            "axis_scores":        axis_scores,
            "axis_contributions": axis_contributions,
            "text_confidence":    round(text_conf, 3),
            "image_confidence":   round(img_conf, 3),
            "fusion_weights":     {"text": text_w, "image": img_w},
            "text_evidence_ratio":  text_w,
            "image_evidence_ratio": img_w,
            "evidence_sentences": evidence_sentences,
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

    return jsonify(_clean({
        "keyword":         kw,
        "fusion_mode":     fusion_mode,
        "user_weights":    user_weights,
        "taste_axes":      taste_axes,
        "meta_axes":       meta_axes,
        "count":           len(results),
        "results":         results[:10],
        "user_vector":     user_vec_dict,
        "label_type":      _RELEVANCE_CACHE.get("label_type", "pseudo-label"),
    }))


@app.route("/api/evaluation", methods=["GET"])
def api_evaluation():
    """
    evaluation.py가 만든 evaluation_results.json을 그대로 내려준다.
    파일이 없으면 ok:false + 안내 메시지 (frontend는 카드 자체를 숨김).
    """
    path = os.path.join(BASE_DIR, "evaluation_results.json")
    if not os.path.exists(path):
        return jsonify({
            "ok": False,
            "message": "evaluation_results.json이 없습니다. evaluation.py를 먼저 실행하세요.",
        }), 404
    try:
        with open(path, "r", encoding="utf-8") as f:
            data = json.load(f)
        return jsonify(data)
    except Exception as e:
        return jsonify({"ok": False, "message": f"읽기 실패: {e}"}), 500


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
