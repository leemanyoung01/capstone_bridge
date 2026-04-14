"""
Multimodal Profiler v6 — PostgreSQL 연동
==========================================
CLIP 추론은 로컬에서만 실행, 결과 벡터를 DB에 저장.

실행:
  python Multimodal_profiler.py 삼겹살
  python Multimodal_profiler.py 삼겹살 --mode simple  # CLIP 없이 텍스트 감성만
"""

import os, sys, json, glob
import pandas as pd
import numpy as np
from collections import defaultdict
from db import (get_conn, upsert_restaurant, upsert_multimodal_profile,
                upsert_representative_images)

IMAGE_MODE = "clip"    # "clip" | "simple"
ALPHA      = 0.7
BETA       = 0.3
STAND_DIR  = os.path.join(os.path.dirname(os.path.abspath(__file__)), "stand")
_stand_cache = None


def load_stand():
    global _stand_cache
    if _stand_cache: return _stand_cache
    paths = {k: os.path.join(STAND_DIR,f"stand_{k}.json") for k in ("axes","lexicon","rules")}
    if not all(os.path.exists(p) for p in paths.values()): return None
    stand = {}
    for k,p in paths.items():
        with open(p,"r",encoding="utf-8") as f: stand[k]=json.load(f)
    _stand_cache = stand; return stand


def get_search_terms(keyword):
    stand = load_stand()
    aliases = stand["axes"].get("keyword_aliases",{}) if stand else {}
    terms = list(aliases.get(keyword,[keyword]))
    if keyword not in terms: terms.append(keyword)
    return terms


# ── CSV에서 이미지 리뷰 로드 ──────────────────────────────────

def load_reviews_with_images(keyword):
    frames = []
    for f in sorted(glob.glob("*.csv")):
        if any(x in f for x in ["_dictionary","_vectors","_axes","candidate"]):
            continue
        try:
            tmp = pd.read_csv(f, encoding="utf-8-sig")
            if "Review" in tmp.columns and "Restaurant" in tmp.columns:
                frames.append(tmp)
        except Exception: pass
    if not frames: return None
    df = pd.concat(frames, ignore_index=True)
    df = df.drop_duplicates(subset=["Restaurant","Date","Review"], keep="first")

    terms = get_search_terms(keyword)
    def has_kw(row):
        text = (str(row.get("Menu","")) + " " + str(row.get("Review",""))).lower()
        return any(t.lower() in text for t in terms)
    df_filtered = df[df.apply(has_kw, axis=1)].copy()

    def check_img(row):
        for col in ["ImageURLs","ImagePaths","Image_Links","review_images","image_url"]:
            val = str(row.get(col,""))
            if val and val not in ["","nan","[]"]: return 1
        return 0
    if "HasPicture" not in df_filtered.columns:
        df_filtered["HasPicture"] = df_filtered.apply(check_img, axis=1)
    return df_filtered


# ── CLIP ──────────────────────────────────────────────────────

_clip = {"model":None,"preprocess":None,"tokenizer":None,"device":None,"cache":{}}


def _get_device():
    import torch
    if torch.cuda.is_available(): return torch.device("cuda")
    if hasattr(torch.backends,"mps") and torch.backends.mps.is_available(): return torch.device("mps")
    return torch.device("cpu")


def _load_clip():
    if _clip["model"]: return True
    try:
        import open_clip, torch
        device = _get_device()
        model, _, preprocess = open_clip.create_model_and_transforms(
            'ViT-B-32', pretrained='laion2b_s34b_b79k')
        model = model.to(device).eval()
        tokenizer = open_clip.get_tokenizer('ViT-B-32')
        _clip.update({"model":model,"preprocess":preprocess,"tokenizer":tokenizer,"device":device})
        print(f"  ✅ CLIP (ViT-B-32, {device})")
        return True
    except ImportError:
        print("  ⚠️ open_clip 미설치 → simple 모드"); return False
    except Exception as e:
        print(f"  ⚠️ CLIP 실패: {e}"); return False


def _text_emb(prompt):
    import torch
    if prompt in _clip["cache"]: return _clip["cache"][prompt]
    tokens = _clip["tokenizer"]([prompt]).to(_clip["device"])
    with torch.no_grad():
        emb = _clip["model"].encode_text(tokens)
        emb = emb / emb.norm(dim=-1, keepdim=True)
    _clip["cache"][prompt] = emb
    return emb


def analyze_clip(image_path, pos_prompts, neg_prompts):
    try:
        import torch
        from PIL import Image
        if not _load_clip(): return 0.0
        img = _clip["preprocess"](Image.open(image_path)).unsqueeze(0).to(_clip["device"])
        with torch.no_grad():
            ie = _clip["model"].encode_image(img)
            ie = ie / ie.norm(dim=-1, keepdim=True)
        ps = [(ie @ _text_emb(p).T).squeeze().item() for p in pos_prompts]
        ns = [(ie @ _text_emb(p).T).squeeze().item() for p in neg_prompts]
        mp, mn = (sum(ps)/len(ps) if ps else 0), (sum(ns)/len(ns) if ns else 0)
        d = abs(mp) + abs(mn)
        return float(np.clip((mp-mn)/d,-1.0,1.0)) if d > 0.01 else 0.0
    except Exception: return 0.0


def simple_sentiment(review_text):
    score = 0.0
    if isinstance(review_text, str):
        if any(w in review_text for w in ["맛있","좋","짱","최고","굿"]): score += 0.5
        if any(w in review_text for w in ["맛없","별로","최악","비추"]):  score -= 0.5
    return float(np.clip(score,-1.0,1.0))


def make_prompts(axis_name, axis_info, keyword="food"):
    pos = axis_info.get("clip_prompt_pos",[])
    neg = axis_info.get("clip_prompt_neg",[])
    if not pos:
        kws = axis_info.get("positive_keywords", axis_info.get("positive",[]))
        pos = [f"a photo of {kw} {keyword}" for kw in kws[:3]] or [f"a photo of {axis_name} {keyword}"]
    if not neg:
        kws = axis_info.get("negative_keywords", axis_info.get("negative",[]))
        neg = [f"a photo of {kw} {keyword}" for kw in kws[:2]] or [f"a photo of {keyword} lacking {axis_name}"]
    return pos[:3], neg[:3]


# ── 이미지 벡터 생성 ──────────────────────────────────────────

def build_image_vectors(df, axes_config, keyword, mode):
    img_reviews = df[df["HasPicture"]==1].copy()
    if len(img_reviews) == 0:
        print("  ⚠️ 이미지 리뷰 없음"); return {}
    print(f"  📷 이미지 리뷰: {len(img_reviews)}건")

    rest_scores = defaultdict(lambda: defaultdict(list))
    clip_calls  = 0

    for _, row in img_reviews.iterrows():
        text       = str(row.get("Review",""))
        restaurant = str(row.get("Restaurant",""))
        img_path   = None
        paths_str  = str(row.get("ImagePaths",""))
        if paths_str and paths_str not in ["","nan"]:
            img_path = paths_str.split(" | ")[0].strip()

        for ax_name, ax_info in axes_config.items():
            pos_kws = ax_info.get("positive_keywords", ax_info.get("positive",[]))
            neg_kws = ax_info.get("negative_keywords", ax_info.get("negative",[]))
            pos_hit = any(kw in text for kw in pos_kws) if pos_kws else False
            neg_hit = any(kw in text for kw in neg_kws) if neg_kws else False
            if not (pos_hit or neg_hit): continue

            if mode == "clip" and img_path and os.path.exists(img_path):
                ap, an = make_prompts(ax_name, ax_info, keyword)
                score  = analyze_clip(img_path, ap, an)
                clip_calls += 1
            else:
                score = simple_sentiment(text)

            if score == 0: continue
            if pos_hit: rest_scores[restaurant][ax_name].append(score)
            elif neg_hit: rest_scores[restaurant][ax_name].append(-score)

    img_vecs = {r: {a: round(float(np.mean(s)),3) for a,s in axes.items()}
                for r, axes in rest_scores.items()}
    print(f"  ✅ {len(img_vecs)}개 식당 | CLIP {clip_calls}회")
    return img_vecs


def late_fusion(text_vecs, img_vecs, alpha=ALPHA, beta=BETA):
    fused = {}
    for rest, tv in text_vecs.items():
        iv = img_vecs.get(rest, {})
        fused[rest] = {}
        for ax in set(list(tv.keys())+list(iv.keys())):
            t, i = tv.get(ax,0.0), iv.get(ax,0.0)
            if ax in iv and ax in tv: fused[rest][ax] = round(alpha*t+beta*i,3)
            elif ax in tv:            fused[rest][ax] = t
            else:                     fused[rest][ax] = round(beta*i,3)
        fused[rest]["_img_coverage"] = round(len(iv)/max(len(tv),1),2)
    return fused


def extract_rep_images(df, axes_config, img_vecs, keyword, top_n=6):
    img_reviews = df[df["HasPicture"]==1].copy()
    if len(img_reviews) == 0: return []

    def get_src(row):
        for col in ["ImageURLs","ImagePaths","Image_Links","review_images","image_url"]:
            val = str(row.get(col,""))
            if val and val not in ["","nan","[]"]:
                return val.split(" | ")[0].split("|")[0].strip().strip("[]'\"")
        return ""

    candidates, seen = [], set()
    for ax_name, ax_info in axes_config.items():
        pos_kws = ax_info.get("positive_keywords", ax_info.get("positive",[]))
        if not pos_kws: continue
        matches = []
        for _, row in img_reviews.iterrows():
            rt   = str(row.get("Review",""))
            rest = str(row.get("Restaurant",""))
            src  = get_src(row)
            if not src or src in seen: continue
            mc = sum(1 for kw in pos_kws if kw in rt)
            if mc == 0: continue
            isc = img_vecs.get(rest,{}).get(ax_name,0.0)
            matches.append({"image_src":src,"axis":ax_name,"label":ax_name,"restaurant":rest,
                            "score":round(mc+isc,3),"review_snippet":rt[:80]})
        if matches:
            matches.sort(key=lambda x:-x["score"])
            seen.add(matches[0]["image_src"])
            candidates.append(matches[0])

    candidates.sort(key=lambda x:-x["score"])
    rep = candidates[:top_n]
    for item in rep:
        item["clip_vector"] = img_vecs.get(item["restaurant"],{})

    safe = keyword.replace(" ","_")
    with open(f"{safe}_representative_images.json","w",encoding="utf-8") as f:
        json.dump({"keyword":keyword,"images":rep}, f, ensure_ascii=False, indent=2)
    print(f"  🖼️  대표 이미지 {len(rep)}장 → JSON 저장")
    return rep


# ── DB 저장 ───────────────────────────────────────────────────

def save_to_db(keyword, fused_vecs, text_vecs, img_vecs, rep_images, alpha, beta, mode):
    with get_conn() as conn:
        for rest_name, fv in fused_vecs.items():
            rest_id = upsert_restaurant(conn, {"name":rest_name,"source":"multimodal"})
            upsert_multimodal_profile(conn, rest_id, keyword, {
                "fused_vector":     fv,
                "text_only_vector": text_vecs.get(rest_name,{}),
                "image_sentiment":  img_vecs.get(rest_name,{}),
                "has_image_data":   rest_name in img_vecs,
                "fusion_mode":      "late",
                "alpha":            alpha, "beta": beta, "image_mode": mode,
            })
        upsert_representative_images(conn, keyword, rep_images)
    print(f"  ✅ DB 저장: {len(fused_vecs)}개 식당 | 대표이미지 {len(rep_images)}장")


# ── 진입점 ────────────────────────────────────────────────────

def main():
    keyword = sys.argv[1] if len(sys.argv)>1 else input("키워드: ").strip()
    mode    = "simple" if "--mode" in sys.argv and sys.argv[sys.argv.index("--mode")+1]=="simple" else IMAGE_MODE
    if not keyword: print("키워드를 입력하세요!"); return
    safe = keyword.replace(" ","_")

    print(f"\n{'━'*55}")
    print(f"  Multimodal Profiler v6  '{keyword}'  α={ALPHA} β={BETA}  mode={mode}")
    print(f"{'━'*55}")

    # 텍스트 벡터 로드 (Food_profiler 결과)
    vec_file = f"{safe}_vectors.json"
    if not os.path.exists(vec_file):
        print(f"  ❌ {vec_file} 없음 → python Food_profiler.py {keyword} 먼저 실행"); return
    with open(vec_file,"r",encoding="utf-8") as f:
        text_data = json.load(f)

    axes_config  = text_data.get("axes_config",{})
    text_vecs    = {}
    axes_list    = text_data.get("axes",[])
    for rest, info in text_data.get("restaurants",{}).items():
        if isinstance(info,dict):
            if "normalized" in info: text_vecs[rest] = info["normalized"]
            elif "vector" in info and axes_list: text_vecs[rest] = dict(zip(axes_list,info["vector"]))
    print(f"\n📊 텍스트 벡터: {len(text_vecs)}개 식당")

    # 이미지 리뷰 로드 (CSV)
    df = load_reviews_with_images(keyword)
    if df is None or len(df)==0:
        print("  ⚠️ 이미지 리뷰 없음 → 텍스트 벡터만 DB 저장")
        with get_conn() as conn:
            for rest_name, tv in text_vecs.items():
                rest_id = upsert_restaurant(conn,{"name":rest_name,"source":"profiler"})
                upsert_multimodal_profile(conn, rest_id, keyword, {
                    "fused_vector": tv, "text_only_vector": tv,
                    "image_sentiment":{}, "has_image_data":False,
                })
        print(f"  ✅ {len(text_vecs)}개 식당 텍스트 벡터 저장 완료")
        return

    total, img_cnt = len(df), int(df["HasPicture"].sum())
    print(f"  리뷰 {total}건 | 이미지 {img_cnt}건 ({img_cnt/total*100:.1f}%)")

    print(f"\n🔍 이미지 벡터 생성...")
    img_vecs = build_image_vectors(df, axes_config, keyword, mode)

    print(f"\n🔀 Late Fusion...")
    fused_vecs = late_fusion(text_vecs, img_vecs, ALPHA, BETA)
    print(f"  {len(fused_vecs)}개 식당 (이미지보정: {sum(1 for r in fused_vecs if r in img_vecs)}개)")

    print(f"\n🖼️  대표 이미지...")
    rep = extract_rep_images(df, axes_config, img_vecs, keyword, 6)

    print(f"\n💾 DB 저장...")
    save_to_db(keyword, fused_vecs, text_vecs, img_vecs, rep, ALPHA, BETA, mode)

    # JSON 백업
    safe = keyword.replace(" ","_")
    with open(f"{safe}_multimodal_vectors.json","w",encoding="utf-8") as f:
        json.dump({
            "keyword":keyword,"fusion_mode":"late","alpha":ALPHA,"beta":BETA,"image_mode":mode,
            "total_reviews":total,"image_reviews":img_cnt,
            "restaurants":{r:{"fused_vector":fused_vecs[r],
                               "text_only_vector":text_vecs.get(r,{}),
                               "image_sentiment":img_vecs.get(r,{}),
                               "has_image_data":r in img_vecs}
                           for r in fused_vecs},
        }, f, ensure_ascii=False, indent=2)
    print(f"  ✅ {safe}_multimodal_vectors.json")
    print(f"\n{'━'*55}")
    print(f"  완료! 텍스트:{len(text_vecs)} 이미지:{len(img_vecs)} 최종:{len(fused_vecs)}")
    print(f"{'━'*55}")


if __name__ == "__main__":
    main()
