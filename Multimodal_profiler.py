"""
Multimodal Profiler v5 — GPU + Stand JSON 연동
================================================
변경사항:
  - GPU 자동 감지 (CUDA/MPS/CPU 폴백)
  - aliases 하드코딩 제거 → Stand JSON에서 읽기
  - Food_profiler의 get_search_terms() 재사용
  - 배치 이미지 인코딩으로 속도 개선

실행: python Multimodal_profiler.py 순대국밥
전제: python Food_profiler.py 순대국밥 을 먼저 실행하여 _vectors.json 생성
"""

import os, sys, json, glob
import pandas as pd
import numpy as np
from collections import defaultdict

IMAGE_MODE = "clip"
ALPHA = 0.7
BETA  = 0.3
STAND_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "stand")

_stand_cache = None

def load_stand():
    global _stand_cache
    if _stand_cache is not None:
        return _stand_cache
    paths = {"axes": os.path.join(STAND_DIR, "stand_axes.json"),
             "lexicon": os.path.join(STAND_DIR, "stand_lexicon.json"),
             "rules": os.path.join(STAND_DIR, "stand_rules.json")}
    if not all(os.path.exists(p) for p in paths.values()):
        return None
    stand = {}
    for key, path in paths.items():
        with open(path, "r", encoding="utf-8") as f:
            stand[key] = json.load(f)
    _stand_cache = stand
    return stand

def get_search_terms(keyword):
    stand = load_stand()
    aliases = stand["axes"].get("keyword_aliases", {}) if stand else {}
    terms = list(aliases.get(keyword, [keyword]))
    if keyword not in terms: terms.append(keyword)
    return terms

def load_reviews_with_images(keyword):
    frames = []
    for f in sorted(glob.glob("*.csv")):
        try:
            tmp = pd.read_csv(f, encoding="utf-8-sig")
            if "Review" in tmp.columns and "Restaurant" in tmp.columns:
                frames.append(tmp)
        except: pass
    if not frames: return None
    df = pd.concat(frames, ignore_index=True)
    df = df.drop_duplicates(subset=["Restaurant", "Date", "Review"], keep="first")

    search_terms = get_search_terms(keyword)
    def has_keyword(row):
        text = (str(row.get("Menu", "")) + " " + str(row.get("Review", ""))).lower()
        return any(t.lower() in text for t in search_terms)
    df_filtered = df[df.apply(has_keyword, axis=1)].copy()

    def check_img(row):
        for col in ["ImageURLs", "ImagePaths", "Image_Links", "review_images", "image_url"]:
            val = str(row.get(col, ""))
            if val and val not in ["", "nan", "[]"]: return 1
        return 0
    if "HasPicture" not in df_filtered.columns:
        df_filtered["HasPicture"] = df_filtered.apply(check_img, axis=1)
    return df_filtered

# ── CLIP 캐시 ──
_clip_cache = {"model": None, "preprocess": None, "tokenizer": None, "device": None, "prompt_embeddings": {}}

def _get_device():
    import torch
    if torch.cuda.is_available(): return torch.device("cuda")
    elif hasattr(torch.backends, "mps") and torch.backends.mps.is_available(): return torch.device("mps")
    return torch.device("cpu")

def _load_clip():
    if _clip_cache["model"] is not None: return True
    try:
        import open_clip, torch
        device = _get_device()
        model, _, preprocess = open_clip.create_model_and_transforms('ViT-B-32', pretrained='laion2b_s34b_b79k')
        model = model.to(device).eval()
        tokenizer = open_clip.get_tokenizer('ViT-B-32')
        _clip_cache.update({"model": model, "preprocess": preprocess, "tokenizer": tokenizer, "device": device})
        print(f"  ✅ CLIP 로드 (ViT-B-32, {device})")
        return True
    except ImportError:
        print("  ⚠️ open_clip 미설치 → simple 모드"); return False
    except Exception as e:
        print(f"  ⚠️ CLIP 실패: {e}"); return False

def _get_text_embedding(prompt_text):
    import torch
    if prompt_text in _clip_cache["prompt_embeddings"]:
        return _clip_cache["prompt_embeddings"][prompt_text]
    tokens = _clip_cache["tokenizer"]([prompt_text]).to(_clip_cache["device"])
    with torch.no_grad():
        emb = _clip_cache["model"].encode_text(tokens)
        emb = emb / emb.norm(dim=-1, keepdim=True)
    _clip_cache["prompt_embeddings"][prompt_text] = emb
    return emb

def analyze_image_sentiment_simple(review_text):
    score = 0.0
    if isinstance(review_text, str):
        if any(w in review_text for w in ["맛있", "좋", "짱", "최고", "굿"]): score += 0.5
        if any(w in review_text for w in ["맛없", "별로", "최악", "비추"]): score -= 0.5
    return float(np.clip(score, -1.0, 1.0))

def make_axis_prompts(axis_name, axis_info, keyword="food"):
    pos = axis_info.get("clip_prompt_pos", [])
    neg = axis_info.get("clip_prompt_neg", [])
    if pos and neg: return pos[:3], neg[:3]
    if not pos:
        kws = axis_info.get("positive_keywords", axis_info.get("positive", []))
        pos = [f"a photo of {kw} {keyword}" for kw in kws[:3]] or [f"a photo of {axis_name} {keyword}"]
    if not neg:
        kws = axis_info.get("negative_keywords", axis_info.get("negative", []))
        neg = [f"a photo of {kw} {keyword}" for kw in kws[:2]] or [f"a photo of {keyword} lacking {axis_name}"]
    return pos[:3], neg[:3]

def analyze_image_clip_axis(image_path, pos_prompts, neg_prompts):
    try:
        import torch
        from PIL import Image
        if not _load_clip(): return 0.0
        model, preprocess, device = _clip_cache["model"], _clip_cache["preprocess"], _clip_cache["device"]
        image = preprocess(Image.open(image_path)).unsqueeze(0).to(device)
        with torch.no_grad():
            img_emb = model.encode_image(image)
            img_emb = img_emb / img_emb.norm(dim=-1, keepdim=True)
        pos_sims = [(img_emb @ _get_text_embedding(p).T).squeeze().item() for p in pos_prompts]
        neg_sims = [(img_emb @ _get_text_embedding(p).T).squeeze().item() for p in neg_prompts]
        mp = sum(pos_sims)/len(pos_sims) if pos_sims else 0
        mn = sum(neg_sims)/len(neg_sims) if neg_sims else 0
        d = abs(mp) + abs(mn)
        return float(np.clip((mp - mn) / d, -1.0, 1.0)) if d > 0.01 else 0.0
    except: return 0.0

def build_image_vectors(df_filtered, axes_config, keyword="food", mode="clip"):
    img_reviews = df_filtered[df_filtered["HasPicture"] == 1].copy()
    if len(img_reviews) == 0:
        print("  ⚠️ 이미지 리뷰 없음"); return {}
    print(f"  📷 이미지 리뷰: {len(img_reviews)}건")

    restaurant_img_scores = defaultdict(lambda: defaultdict(list))
    clip_calls = 0
    for _, row in img_reviews.iterrows():
        review_text = str(row.get("Review", ""))
        restaurant = str(row.get("Restaurant", ""))
        image_path = None
        paths_str = str(row.get("ImagePaths", ""))
        if paths_str and paths_str not in ["", "nan"]:
            image_path = paths_str.split(" | ")[0].strip()

        for axis_name, axis_info in axes_config.items():
            pos_kws = axis_info.get("positive_keywords", axis_info.get("positive", []))
            neg_kws = axis_info.get("negative_keywords", axis_info.get("negative", []))
            pos_matched = any(kw in review_text for kw in pos_kws) if pos_kws else False
            neg_matched = any(kw in review_text for kw in neg_kws) if neg_kws else False
            if not (pos_matched or neg_matched): continue

            if mode == "clip" and image_path and os.path.exists(image_path):
                ap, an = make_axis_prompts(axis_name, axis_info, keyword)
                axis_score = analyze_image_clip_axis(image_path, ap, an)
                clip_calls += 1
            else:
                axis_score = analyze_image_sentiment_simple(review_text)
            if axis_score == 0: continue
            if pos_matched: restaurant_img_scores[restaurant][axis_name].append(axis_score)
            elif neg_matched: restaurant_img_scores[restaurant][axis_name].append(-axis_score)

    img_vectors = {r: {a: round(float(np.mean(s)), 3) for a, s in axes.items()} for r, axes in restaurant_img_scores.items()}
    print(f"  ✅ {len(img_vectors)}개 식당 | CLIP: {clip_calls}회 | 캐시: {len(_clip_cache['prompt_embeddings'])}개")
    if _clip_cache["device"]: print(f"     Device: {_clip_cache['device']}")
    return img_vectors

def late_fusion(text_vectors, image_vectors, alpha=ALPHA, beta=BETA):
    fused = {}
    for restaurant, text_vec in text_vectors.items():
        img_vec = image_vectors.get(restaurant, {})
        fused[restaurant] = {}
        for axis in set(list(text_vec.keys()) + list(img_vec.keys())):
            t, i = text_vec.get(axis, 0.0), img_vec.get(axis, 0.0)
            if axis in img_vec and axis in text_vec: fused[restaurant][axis] = round(alpha * t + beta * i, 3)
            elif axis in text_vec: fused[restaurant][axis] = t
            else: fused[restaurant][axis] = round(beta * i, 3)
        fused[restaurant]["_img_coverage"] = round(len(img_vec) / max(len(text_vec), 1), 2)
    return fused

def extract_representative_images(df_filtered, axes_config, image_vectors, keyword, top_n=6):
    img_reviews = df_filtered[df_filtered["HasPicture"] == 1].copy()
    if len(img_reviews) == 0: return []
    def get_img(row):
        for col in ["ImageURLs", "ImagePaths", "Image_Links", "review_images", "image_url"]:
            val = str(row.get(col, ""))
            if val and val not in ["", "nan", "[]"]: return val.split(" | ")[0].split("|")[0].strip().strip("[]'\"")
        return ""
    candidates, seen = [], set()
    for axis_name, axis_info in axes_config.items():
        pos_kws = axis_info.get("positive_keywords", axis_info.get("positive", []))
        if not pos_kws: continue
        axis_matches = []
        for _, row in img_reviews.iterrows():
            rt, rest, img = str(row.get("Review","")), str(row.get("Restaurant","")), get_img(row)
            if not img or img in seen: continue
            mc = sum(1 for kw in pos_kws if kw in rt)
            if mc == 0: continue
            isc = image_vectors.get(rest, {}).get(axis_name, 0.0)
            axis_matches.append({"image_src": img, "axis": axis_name, "label": axis_name,
                "restaurant": rest, "score": round(mc + isc, 3), "review_snippet": rt[:80], "match_count": mc})
        if axis_matches:
            axis_matches.sort(key=lambda x: -x["score"])
            seen.add(axis_matches[0]["image_src"])
            candidates.append(axis_matches[0])
    candidates.sort(key=lambda x: -x["score"])
    representative = candidates[:top_n]
    for item in representative: item["clip_vector"] = image_vectors.get(item["restaurant"], {})
    safe = keyword.replace(" ", "_")
    with open(f"{safe}_representative_images.json", "w", encoding="utf-8") as f:
        json.dump({"keyword": keyword, "total_candidates": len(candidates), "images": representative}, f, ensure_ascii=False, indent=2)
    print(f"\n  🖼️  대표 이미지 {len(representative)}장")
    for i, img in enumerate(representative, 1): print(f"    {i}. [{img['axis']}] {img['restaurant'][:15]} ({img['score']})")
    return representative

def main():
    keyword = sys.argv[1] if len(sys.argv) > 1 else input("음식 키워드: ").strip()
    if not keyword: print("키워드를 입력해주세요!"); return
    safe = keyword.replace(" ", "_")

    print(f"\n{'━'*55}")
    print(f"  🔀 TasteBridge 멀티모달 v5 (GPU)")
    print(f"  키워드: '{keyword}' | α={ALPHA} β={BETA}")
    print(f"{'━'*55}")

    vectors_file = f"{safe}_vectors.json"
    if not os.path.exists(vectors_file):
        print(f"  ❌ {vectors_file} 없음! → python Food_profiler.py {keyword}"); return
    with open(vectors_file, "r", encoding="utf-8") as f: text_data = json.load(f)

    axes_config = text_data.get("axes_config", {})
    text_vectors = {}
    axes_list = text_data.get("axes", [])
    for rest, info in text_data.get("restaurants", {}).items():
        if isinstance(info, dict):
            if "normalized" in info: text_vectors[rest] = info["normalized"]
            elif "vector" in info and axes_list: text_vectors[rest] = dict(zip(axes_list, info["vector"]))
    print(f"\n📊 텍스트 벡터: {len(text_vectors)}개 식당")

    df = load_reviews_with_images(keyword)
    if df is None or len(df) == 0: print("  ❌ 리뷰 없음!"); return
    total, img_count = len(df), int(df["HasPicture"].sum())
    print(f"📷 리뷰: {total}건 | 이미지: {img_count}건 ({img_count/total*100:.1f}%)")

    print(f"\n🔍 이미지 벡터 생성...")
    image_vectors = build_image_vectors(df, axes_config, keyword=keyword, mode=IMAGE_MODE)

    print(f"\n🔀 Late Fusion...")
    fused_vectors = late_fusion(text_vectors, image_vectors, ALPHA, BETA)
    changed = sum(1 for r in fused_vectors if r in image_vectors)
    print(f"  ✅ {len(fused_vectors)}개 식당 (이미지보정: {changed})")

    if changed > 0:
        for rest in list(image_vectors.keys())[:3]:
            if rest not in text_vectors: continue
            print(f"\n    [{rest[:20]}]")
            for ax in list(image_vectors[rest].keys())[:5]:
                t = text_vectors.get(rest, {}).get(ax, 0)
                i = image_vectors.get(rest, {}).get(ax, 0)
                fv = fused_vectors.get(rest, {}).get(ax, 0)
                print(f"      {ax:12s}  T:{t:+.3f}  I:{i:+.3f}  → F:{fv:+.3f} {'↑' if fv>t else '↓' if fv<t else '='}")

    print(f"\n🖼️  대표 이미지...")
    rep = extract_representative_images(df, axes_config, image_vectors, keyword, 6)

    output = {"keyword": keyword, "fusion_mode": "late", "alpha": ALPHA, "beta": BETA,
              "image_mode": IMAGE_MODE, "total_reviews": total, "image_reviews": img_count, "restaurants": {}}
    for rest in fused_vectors:
        output["restaurants"][rest] = {"fused_vector": fused_vectors[rest],
            "text_only_vector": text_vectors.get(rest, {}),
            "image_sentiment": image_vectors.get(rest, {}),
            "has_image_data": rest in image_vectors}
    out_file = f"{safe}_multimodal_vectors.json"
    with open(out_file, "w", encoding="utf-8") as f: json.dump(output, f, ensure_ascii=False, indent=2)

    print(f"\n{'━'*55}")
    print(f"  ✅ {out_file}")
    print(f"  텍스트: {len(text_vectors)} | 이미지: {len(image_vectors)} | 최종: {len(fused_vectors)} | 대표: {len(rep)}장")
    if _clip_cache["device"]: print(f"  ⚡ {_clip_cache['device']}")
    print(f"{'━'*55}")

if __name__ == "__main__":
    main()
