import glob
import json
import os
import math
from copy import deepcopy

import numpy as np
from flask import Flask, jsonify, request, send_file, send_from_directory

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
IMAGES_DIR = os.path.join(BASE_DIR, "images")

app = Flask(__name__)

USER_ALPHA = 0.7
USER_BETA = 0.3
PREFERRED_DEFAULT_KEYWORD = "삼겹살"


def read_json(path):
    with open(path, "r", encoding="utf-8") as f:
        return json.load(f)


def path_glob(pattern):
    return sorted(glob.glob(os.path.join(BASE_DIR, pattern)))


def normalize_keyword_name(keyword: str) -> str:
    kw = str(keyword or "").strip()
    if kw.endswith("_multimodal"):
        kw = kw[: -len("_multimodal")]
    return kw


def keyword_from_filename(path, suffix):
    name = os.path.basename(path).replace(suffix, "")
    return normalize_keyword_name(name)


def merge_restaurants(text_restaurants, multimodal_restaurants):
    merged = deepcopy(text_restaurants or {})
    for name, info in (multimodal_restaurants or {}).items():
        if name not in merged:
            merged[name] = {}
        if isinstance(info, dict):
            if not isinstance(merged[name], dict):
                merged[name] = {}
            merged[name].update(info)
        else:
            merged[name] = info
    return merged


def load_keyword_bundles():
    bundles = {}

    # 1) text vectors
    for path in path_glob("*_vectors.json"):
        # _multimodal_vectors.json는 아래에서 따로 처리
        if path.endswith("_multimodal_vectors.json"):
            continue

        kw = keyword_from_filename(path, "_vectors.json")
        try:
            data = read_json(path)
        except Exception as e:
            print(f"  ❌ {os.path.basename(path)} 로드 실패: {e}")
            continue

        bundle = bundles.setdefault(kw, {"keyword": kw})
        bundle["text_path"] = path
        bundle["text"] = data
        bundle["axes"] = data.get("axes", bundle.get("axes", []))
        bundle["groups"] = data.get("groups", bundle.get("groups", {}))
        bundle["axes_config"] = data.get("axes_config", bundle.get("axes_config", {}))
        bundle["restaurants"] = merge_restaurants(
            bundle.get("restaurants", {}),
            data.get("restaurants", {}),
        )

    # 2) multimodal vectors
    for path in path_glob("*_multimodal_vectors.json"):
        kw = keyword_from_filename(path, "_multimodal_vectors.json")
        try:
            data = read_json(path)
        except Exception as e:
            print(f"  ❌ {os.path.basename(path)} 로드 실패: {e}")
            continue

        bundle = bundles.setdefault(kw, {"keyword": kw})
        bundle["multimodal_path"] = path
        bundle["multimodal"] = data
        bundle["fusion_mode"] = data.get("fusion_mode")
        bundle["alpha"] = data.get("alpha")
        bundle["beta"] = data.get("beta")
        bundle["image_mode"] = data.get("image_mode")

        if not bundle.get("axes"):
            sample_restaurants = data.get("restaurants", {})
            if sample_restaurants:
                first_info = next(iter(sample_restaurants.values()))
                if isinstance(first_info, dict):
                    fused = first_info.get("fused_vector", {})
                    if isinstance(fused, dict):
                        bundle["axes"] = list(fused.keys())

        bundle["restaurants"] = merge_restaurants(
            bundle.get("restaurants", {}),
            data.get("restaurants", {}),
        )

    # 3) axes_config only files (optional backup)
    for path in path_glob("*_axes_config.json"):
        kw = keyword_from_filename(path, "_axes_config.json")
        try:
            data = read_json(path)
        except Exception:
            continue

        bundle = bundles.setdefault(kw, {"keyword": kw})
        if not bundle.get("axes_config"):
            bundle["axes_config"] = data
        if not bundle.get("axes"):
            bundle["axes"] = list(data.keys())

    # 빈 번들 제거
    cleaned = {}
    for kw, bundle in bundles.items():
        if bundle.get("restaurants") or bundle.get("axes_config") or bundle.get("axes"):
            cleaned[kw] = bundle

    return cleaned


def load_restaurant_db():
    path = os.path.join(BASE_DIR, "restaurant_db.json")
    if not os.path.exists(path):
        print("  ⚠️ restaurant_db.json 없음")
        return {}
    try:
        data = read_json(path)
        print(f"  ✅ restaurant_db.json ({len(data)}개)")
        return data
    except Exception as e:
        print(f"  ❌ restaurant_db.json 로드 실패: {e}")
        return {}


def load_representative_images():
    all_images = {}
    for path in path_glob("*_representative_images.json"):
        try:
            data = read_json(path)
            kw = normalize_keyword_name(
                data.get("keyword") or keyword_from_filename(path, "_representative_images.json")
            )
            data["keyword"] = kw
            all_images[kw] = data
            print(f"  ✅ {os.path.basename(path)} → '{kw}' ({len(data.get('images', []))}장)")
        except Exception as e:
            print(f"  ❌ {os.path.basename(path)}: {e}")
    return all_images


def choose_default_keyword(all_data):
    if not all_data:
        return None
    if PREFERRED_DEFAULT_KEYWORD in all_data:
        return PREFERRED_DEFAULT_KEYWORD
    return sorted(all_data.keys())[0]


def get_axes(bundle):
    axes = bundle.get("axes") or []
    if axes:
        return axes

    axes_config = bundle.get("axes_config") or {}
    if axes_config:
        return list(axes_config.keys())

    restaurants = bundle.get("restaurants") or {}
    for _, info in restaurants.items():
        if not isinstance(info, dict):
            continue
        for key in ["fused_vector", "text_only_vector", "normalized"]:
            vec = info.get(key)
            if isinstance(vec, dict) and vec:
                return list(vec.keys())

    return []


def get_restaurant_info(name, db):
    if name in db:
        return db[name]

    compact = name.replace("-", "").replace(" ", "").lower()
    for key, value in db.items():
        normalized = key.replace("-", "").replace(" ", "").lower()
        if compact in normalized or normalized in compact:
            return value
    return {}


def cosine_sim(a, b):
    a = np.array(a, dtype=float)
    b = np.array(b, dtype=float)
    na = np.linalg.norm(a)
    nb = np.linalg.norm(b)
    return float(np.dot(a, b) / (na * nb)) if na and nb else 0.0


def clean_jsonable(obj):
    if isinstance(obj, dict):
        return {k: clean_jsonable(v) for k, v in obj.items()}
    if isinstance(obj, list):
        return [clean_jsonable(v) for v in obj]
    if isinstance(obj, tuple):
        return [clean_jsonable(v) for v in obj]
    if isinstance(obj, np.generic):
        obj = obj.item()
    if isinstance(obj, float):
        if math.isnan(obj) or math.isinf(obj):
            return None
        return obj
    return obj


def build_user_image_vector(selected_images, axes):
    if not selected_images:
        return {ax: 0.0 for ax in axes}

    axis_scores = {ax: [] for ax in axes}

    for img_info in selected_images:
        clip_vec = img_info.get("clip_vector", {}) or {}
        main_axis = img_info.get("axis", "")

        for ax in axes:
            value = clip_vec.get(ax, 0.0)
            if isinstance(value, (int, float)):
                axis_scores[ax].append(float(value))

        if main_axis in axis_scores:
            axis_scores[main_axis].append(0.5)

    user_img_vec = {}
    for ax in axes:
        vals = axis_scores[ax]
        user_img_vec[ax] = round(float(np.mean(vals)), 4) if vals else 0.0
    return user_img_vec


def fuse_user_vectors(text_vec, image_vec, axes, alpha=USER_ALPHA, beta=USER_BETA):
    img_nonzero = sum(1 for value in image_vec.values() if abs(value) > 0.001)
    if img_nonzero == 0:
        return text_vec, "text_only"

    fused = {}
    for ax in axes:
        t = float(text_vec.get(ax, 0.0))
        i = float(image_vec.get(ax, 0.0))

        if abs(i) > 0.001 and abs(t) > 0.001:
            fused[ax] = round(alpha * t + beta * i, 4)
        elif abs(t) > 0.001:
            fused[ax] = t
        elif abs(i) > 0.001:
            fused[ax] = round(beta * i, 4)
        else:
            fused[ax] = 0.0

    return fused, "multimodal"


print("\n📂 데이터 로드...")
ALL_DATA = load_keyword_bundles()
REST_DB = load_restaurant_db()
REP_IMAGES = load_representative_images()
DEFAULT_KW = choose_default_keyword(ALL_DATA)
print(f"  키워드: {sorted(ALL_DATA.keys())} | 기본: {DEFAULT_KW}")
print(f"  대표 이미지: {list(REP_IMAGES.keys())}\n")


@app.route("/")
def index():
    for filename in ["survey.html", "index.html"]:
        path = os.path.join(BASE_DIR, filename)
        if os.path.exists(path):
            return send_file(path)
    return "<h1>survey.html 파일을 같은 폴더에 넣어주세요</h1>", 404


@app.route("/images/<path:fn>")
def serve_img(fn):
    return send_from_directory(IMAGES_DIR, fn)


@app.route("/api/health")
def api_health():
    return jsonify({
        "ok": True,
        "keywords": sorted(ALL_DATA.keys()),
        "default_keyword": DEFAULT_KW,
        "representative_keywords": sorted(REP_IMAGES.keys()),
    })


@app.route("/api/keywords")
def api_keywords():
    return jsonify(sorted(ALL_DATA.keys()))


@app.route("/api/config")
def api_config():
    kw = normalize_keyword_name(request.args.get("keyword", DEFAULT_KW))
    bundle = ALL_DATA.get(kw)
    if not bundle:
        return jsonify({"error": f"'{kw}' 없음"}), 404

    return jsonify({
        "keyword": kw,
        "axes": get_axes(bundle),
        "groups": bundle.get("groups", {}),
        "axes_config": bundle.get("axes_config", {}),
    })


@app.route("/api/representative_images")
def api_representative_images():
    kw = normalize_keyword_name(request.args.get("keyword", DEFAULT_KW))

    if kw in REP_IMAGES:
        return jsonify(REP_IMAGES[kw])

    # 유사 키 매칭
    for key, value in REP_IMAGES.items():
        if kw in key or key in kw:
            return jsonify(value)

    return jsonify({"keyword": kw, "images": []})


@app.route("/api/recommend", methods=["POST"])
def api_recommend():
    body = request.get_json(silent=True)
    if not body:
        return jsonify({"error": "no data"}), 400

    if "text_preferences" in body:
        kw = normalize_keyword_name(body.get("_keyword", DEFAULT_KW))
        user_prefs = body.get("text_preferences", {})
        selected_images = body.get("selected_images", [])
        use_fusion = body.get("use_image_fusion", False)
    else:
        kw = normalize_keyword_name(body.get("_keyword", DEFAULT_KW))
        user_prefs = {k: v for k, v in body.items() if k != "_keyword"}
        selected_images = []
        use_fusion = False

    bundle = ALL_DATA.get(kw)
    if not bundle:
        return jsonify({"error": f"'{kw}' 없음"}), 404

    axes = get_axes(bundle)
    if not axes:
        return jsonify({"error": "축 정보 없음"}), 500

    user_text_vec = {ax: float(user_prefs.get(ax, 0.0)) for ax in axes}
    fusion_mode = "text_only"

    if use_fusion and selected_images:
        user_img_vec = build_user_image_vector(selected_images, axes)
        user_vec_dict, fusion_mode = fuse_user_vectors(user_text_vec, user_img_vec, axes)
    else:
        user_vec_dict = user_text_vec

    user_vec = [user_vec_dict.get(ax, 0.0) for ax in axes]
    results = []

    for name, info in (bundle.get("restaurants") or {}).items():
        if not isinstance(info, dict):
            continue

        vector_dict = None
        for key in ["fused_vector", "text_only_vector", "normalized"]:
            if isinstance(info.get(key), dict):
                vector_dict = info[key]
                break

        if vector_dict is not None:
            restaurant_vec = [float(vector_dict.get(ax, 0.0)) for ax in axes]
        else:
            raw_vector = info.get("vector", [0] * len(axes))
            restaurant_vec = [float(v) for v in raw_vector[: len(axes)]]
            if len(restaurant_vec) < len(axes):
                restaurant_vec.extend([0.0] * (len(axes) - len(restaurant_vec)))

        sim = cosine_sim(user_vec, restaurant_vec)

        evidence = []
        for _ax, ev_list in (info.get("evidence") or {}).items():
            if isinstance(ev_list, list):
                evidence.extend(ev_list)

        reasons = []
        for ax in axes:
            uv = float(user_vec_dict.get(ax, 0.0))
            rv = float(restaurant_vec[axes.index(ax)])
            if abs(uv) > 0.001 and abs(rv) > 0.001:
                reasons.append((ax, uv * rv))

        reasons.sort(key=lambda x: x[1], reverse=True)
        top_reasons = [ax for ax, _ in reasons[:5]]

        rest_info = get_restaurant_info(name, REST_DB)
        address = (
            rest_info.get("road_address")
            or rest_info.get("address")
            or ""
        )

        results.append({
            "name": name,
            "similarity": round(float(sim), 4),
            "address": address,
            "phone": rest_info.get("phone", ""),
            "naver_url": rest_info.get("naver_url", ""),
            "category": rest_info.get("category", ""),
            "evidence": evidence[:3],
            "reasons": top_reasons,
            "fusion_mode": fusion_mode,
            "has_image_data": bool(info.get("has_image_data", False)),
        })

    results.sort(key=lambda x: x["similarity"], reverse=True)

    return jsonify(clean_jsonable({
        "keyword": kw,
        "fusion_mode": fusion_mode,
        "count": len(results),
        "results": results[:10],
        "user_vector": user_vec_dict,
    }))


if __name__ == "__main__":
    print(f"🚀 http://127.0.0.1:5000 | 기본 키워드: {DEFAULT_KW}")
    app.run(debug=True, host="127.0.0.1", port=5000)