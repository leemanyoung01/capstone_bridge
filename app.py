"""
app.py — Flask API 서버 (PostgreSQL 전용)
==========================================
시작 시 DB에서 전체 데이터를 메모리에 캐시.
새 데이터 적재 후 POST /api/reload 호출하면 재시작 없이 갱신.

실행:
  python app.py                    # 로컬 개발
  gunicorn app:app -b 0.0.0.0:5000 --workers 3   # 프로덕션
"""

import json, math, os
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

USER_ALPHA = 0.7
USER_BETA  = 0.3
DEFAULT_KEYWORD_PREF = os.environ.get("DEFAULT_KEYWORD", "삼겹살")


# ── 시작 시 DB 캐시 로드 ─────────────────────────────────────

def _load_all_data() -> tuple[dict, dict, dict]:
    """
    반환: (ALL_DATA, REP_IMAGES, REST_DB)
    ALL_DATA[keyword] = {axes, axes_config, restaurants}
    """
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
                }

            bundles[kw] = {
                "keyword":     kw,
                "axes":        axes_list,
                "axes_config": cfg,
                "restaurants": restaurants,
            }

        # 대표 이미지
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

        # 식당 기본 정보
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
    print(f"     DATABASE_URL = {os.environ.get('DATABASE_URL','(미설정)')}")
    ALL_DATA, REP_IMAGES, REST_DB, DEFAULT_KW = {}, {}, {}, None


# ── 유틸 ─────────────────────────────────────────────────────

def _norm_kw(kw: str) -> str:
    kw = str(kw or "").strip()
    return kw[:-len("_multimodal")] if kw.endswith("_multimodal") else kw


def _get_axes(bundle: dict) -> list[str]:
    if bundle.get("axes"): return bundle["axes"]
    if bundle.get("axes_config"): return list(bundle["axes_config"].keys())
    for info in (bundle.get("restaurants") or {}).values():
        if isinstance(info, dict):
            for key in ("fused_vector","text_only_vector","normalized"):
                vec = info.get(key)
                if isinstance(vec, dict):
                    return [k for k in vec if not k.startswith("_")]
    return []


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


def _user_img_vec(selected_images, axes):
    scores = {ax:[] for ax in axes}
    for img in selected_images:
        cv, ma = img.get("clip_vector",{}) or {}, img.get("axis","")
        for ax in axes:
            v = cv.get(ax,0.0)
            if isinstance(v,(int,float)): scores[ax].append(float(v))
        if ma in scores: scores[ma].append(0.5)
    return {ax: round(float(np.mean(vs)),4) if vs else 0.0 for ax,vs in scores.items()}


def _fuse(tv, iv, axes):
    if not any(abs(v)>0.001 for v in iv.values()): return tv, "text_only"
    fused = {}
    for ax in axes:
        t, i = float(tv.get(ax,0.0)), float(iv.get(ax,0.0))
        if   abs(i)>0.001 and abs(t)>0.001: fused[ax] = round(USER_ALPHA*t+USER_BETA*i,4)
        elif abs(t)>0.001: fused[ax] = t
        elif abs(i)>0.001: fused[ax] = round(USER_BETA*i,4)
        else: fused[ax] = 0.0
    return fused, "multimodal"


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
    return jsonify({
        "ok":          True,
        "keywords":    sorted(ALL_DATA.keys()),
        "default_keyword":     DEFAULT_KW,
        "restaurants": len(REST_DB),
        "db_url":      os.environ.get("DATABASE_URL","(env not set)").split("@")[-1],  # 비밀번호 제외
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
    
    # groups가 없으면 axes_config의 group으로 직접 구성
    groups = bundle.get("groups", {})
    if not groups:
        from collections import defaultdict
        groups = defaultdict(list)
        for axis_name, info in bundle.get("axes_config", {}).items():
            groups[info.get("group", "기타")].append(axis_name)
        groups = dict(groups)

    return jsonify({
        "keyword":     kw,
        "axes":        _get_axes(bundle),
        "groups":      groups,           # ← 추가
        "axes_config": bundle.get("axes_config", {}),
    })


@app.route("/api/representative_images")
def api_rep_images():
    kw = _norm_kw(request.args.get("keyword", DEFAULT_KW))
    if kw in REP_IMAGES: return jsonify(REP_IMAGES[kw])
    for k, v in REP_IMAGES.items():
        if kw in k or k in kw: return jsonify(v)
    return jsonify({"keyword": kw, "images": []})


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

    axes = _get_axes(bundle)
    if not axes:
        return jsonify({"error": "축 정보 없음"}), 500

    user_tv = {ax: float(user_prefs.get(ax, 0.0)) for ax in axes}

    if use_fusion and selected_images:
        user_iv = _user_img_vec(selected_images, axes)
        user_vec_dict, fusion_mode = _fuse(user_tv, user_iv, axes)
    else:
        user_vec_dict, fusion_mode = user_tv, "text_only"

    user_vec = [user_vec_dict.get(ax,0.0) for ax in axes]
    results  = []

    for name, info in (bundle.get("restaurants") or {}).items():
        if not isinstance(info, dict): continue

        vec_dict = None
        for key in ("fused_vector","text_only_vector","normalized"):
            if isinstance(info.get(key), dict):
                vec_dict = info[key]; break
        if vec_dict is None: continue

        rest_vec = [float(vec_dict.get(ax,0.0)) for ax in axes]
        sim      = _cosine(user_vec, rest_vec)

        reasons = sorted(
            [(ax, float(user_vec_dict.get(ax,0.0)) * float(vec_dict.get(ax,0.0)))
             for ax in axes
             if abs(user_vec_dict.get(ax,0.0))>0.001 and abs(vec_dict.get(ax,0.0))>0.001],
            key=lambda x:x[1], reverse=True
        )
        evidence = []
        for _, evl in (info.get("evidence") or {}).items():
            if isinstance(evl, list): evidence.extend(evl)

        ri = _rest_info(name)
        results.append({
            "name":           name,
            "similarity":     round(float(sim),4),
            "address":        ri.get("road_address") or ri.get("address",""),
            "phone":          ri.get("phone",""),
            "naver_url":      ri.get("naver_url",""),
            "category":       ri.get("category",""),
            "evidence":       evidence[:3],
            "reasons":        [ax for ax,_ in reasons[:5]],
            "fusion_mode":    fusion_mode,
            "has_image_data": bool(info.get("has_image_data",False)),
        })

    results.sort(key=lambda x:x["similarity"], reverse=True)
    return jsonify(_clean({
        "keyword":     kw,
        "fusion_mode": fusion_mode,
        "count":       len(results),
        "results":     results[:10],
        "user_vector": user_vec_dict,
    }))


@app.route("/api/reload", methods=["POST"])
def api_reload():
    """새 데이터를 DB에 올린 후 이 엔드포인트를 호출하면 캐시 갱신."""
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
