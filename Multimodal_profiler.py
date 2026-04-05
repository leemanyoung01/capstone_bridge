"""
모듈형 멀티모달 파이프라인 (Modular Multimodal Profiler)
======================================================
Food profiler에서 생성된 '공통 좌표계(clip_prompt)'를 활용하여 
CLIP 모델이 축별로 이미지를 정밀 분석하고 Late Fusion을 수행합니다.
"""

import os
import sys
import json
import glob
import pandas as pd
import numpy as np
from collections import defaultdict

IMAGE_MODE = "clip" 
ALPHA = 0.7   # 텍스트 가중치
BETA  = 0.3   # 이미지 가중치

def load_reviews_with_images(keyword):
    """
    CSV 파일에서 리뷰를 로드하고 키워드로 필터링한 후, 
    이미지가 포함된 리뷰에 HasPicture 플래그를 추가합니다.
    """
    import glob
    csv_files = sorted(glob.glob("*.csv"))
    frames = []
    for f in csv_files:
        try:
            tmp = pd.read_csv(f, encoding="utf-8-sig")
            if "Review" in tmp.columns and "Restaurant" in tmp.columns:
                frames.append(tmp)
        except:
            pass
            
    if not frames:
        return None
        
    df = pd.concat(frames, ignore_index=True)
    df = df.drop_duplicates(subset=["Restaurant", "Date", "Review"], keep="first")
    
    aliases = {
        "짜장면": ["짜장"], "아메리카노": ["아메리카노", "americano", "아아", "뜨아", "아.아", "블랙커피"],
        "짬뽕": ["짬뽕"], "떡볶이": ["떡볶이", "tteokbokki"],
        "치킨": ["치킨", "chicken"], "커피": ["커피", "coffee"],
        "탕수육": ["탕수육"], "볶음밥": ["볶음밥"], 
        "라떼": ["라떼", "latte", "바닐라라떼", "카페라떼", "연유라떼", "돌체라떼", "바라", "아바라"],
        "피자": ["피자", "pizza"], "햄버거": ["버거", "hamburger", "burger", "수제버거"]
    }
    
    search_terms = aliases.get(keyword, [keyword])
    if keyword not in search_terms:
        search_terms.append(keyword)

    def has_keyword(row):
        text = (str(row.get("Menu", "")) + " " + str(row.get("Review", ""))).lower()
        return any(term.lower() in text for term in search_terms)

    mask = df.apply(has_keyword, axis=1)
    df_filtered = df[mask].copy()

    def check_img(row):
        for col in ["ImageURLs", "ImagePaths", "Image_Links", "review_images", "image_url"]:
            val = str(row.get(col, ""))
            if val and val not in ["", "nan", "[]"]:
                return 1
        return 0
        
    if "HasPicture" not in df_filtered.columns:
        df_filtered["HasPicture"] = df_filtered.apply(check_img, axis=1)
        
    return df_filtered

# ============================================================
# 2. 축별 CLIP 점수화 (리뷰-조건부 프롬프트)
# ============================================================

def analyze_image_sentiment_simple(review_text):
    """기존 Simple 모드 로직 유지: 단순 텍스트 기반 감성 추정"""
    score = 0.0
    if isinstance(review_text, str):
        if any(w in review_text for w in ["맛있", "좋", "짱", "최고", "굿"]):
            score += 0.5
        if any(w in review_text for w in ["맛없", "별로", "최악", "비추", "실망"]):
            score -= 0.5
    return float(np.clip(score, -1.0, 1.0))


# ── CLIP 모델 + 프롬프트 임베딩 캐시 ──
_clip_cache = {
    "model": None,
    "preprocess": None,
    "tokenizer": None,
    "prompt_embeddings": {},  # {prompt_text: embedding_tensor}
}


def _load_clip():
    """CLIP 모델 1회 로드 + 캐시"""
    if _clip_cache["model"] is not None:
        return True
    try:
        import open_clip
        import torch
        model, _, preprocess = open_clip.create_model_and_transforms(
            'ViT-B-32', pretrained='laion2b_s34b_b79k'
        )
        tokenizer = open_clip.get_tokenizer('ViT-B-32')
        _clip_cache["model"] = model
        _clip_cache["preprocess"] = preprocess
        _clip_cache["tokenizer"] = tokenizer
        print("  ✅ CLIP 모델 로드 완료 (ViT-B-32)")
        return True
    except ImportError:
        print("  ⚠️ open_clip 미설치 → simple 모드로 폴백")
        return False
    except Exception as e:
        print(f"  ⚠️ CLIP 로드 실패: {e}")
        return False


def _get_text_embedding(prompt_text):
    """프롬프트 텍스트의 임베딩을 캐시에서 가져오거나 새로 계산"""
    import torch
    if prompt_text in _clip_cache["prompt_embeddings"]:
        return _clip_cache["prompt_embeddings"][prompt_text]
    
    tokenizer = _clip_cache["tokenizer"]
    model = _clip_cache["model"]
    
    tokens = tokenizer([prompt_text])
    with torch.no_grad():
        emb = model.encode_text(tokens)
        emb = emb / emb.norm(dim=-1, keepdim=True)
    
    _clip_cache["prompt_embeddings"][prompt_text] = emb
    return emb


def make_axis_prompts(axis_name, axis_info, keyword="food"):
    """
    축 정보(axes_config)에서 CLIP pos/neg 프롬프트 생성.
    
    우선순위:
    1. clip_prompt_pos/neg가 이미 정의되어 있으면 그대로 사용
    2. 없으면 positive/negative 키워드에서 자동 생성
    """
    # axes_config 포맷 지원 (positive_keywords vs positive)
    pos_kws = axis_info.get("clip_prompt_pos", [])
    neg_kws = axis_info.get("clip_prompt_neg", [])
    
    # clip_prompt_pos가 이미 있으면 그대로 사용
    if pos_kws and neg_kws:
        return pos_kws[:3], neg_kws[:3]
    
    # 기존 단일 clip_prompt 호환
    if not pos_kws and axis_info.get("clip_prompt"):
        pos_kws = [axis_info["clip_prompt"]]
    
    # 없으면 키워드에서 자동 생성
    if not pos_kws:
        keywords = axis_info.get("positive_keywords", axis_info.get("positive", []))
        for kw in keywords[:3]:
            pos_kws.append(f"a photo of {kw} {keyword}")
        if not pos_kws:
            pos_kws = [f"a photo of {axis_name} {keyword}"]
    
    if not neg_kws:
        keywords = axis_info.get("negative_keywords", axis_info.get("negative", []))
        for kw in keywords[:2]:
            neg_kws.append(f"a photo of {kw} {keyword}")
        if not neg_kws:
            neg_kws = [f"a photo of {keyword} lacking {axis_name}"]
    
    return pos_kws[:3], neg_kws[:3]


def analyze_image_clip_axis(image_path, pos_prompts, neg_prompts):
    """
    [핵심] 이미지 vs 축별 pos/neg 프롬프트 리스트 비교
    
    score = (mean_pos_sim - mean_neg_sim) / (mean_pos_sim + mean_neg_sim)
    범위: -1.0 ~ +1.0
    
    프롬프트 임베딩은 캐시 재사용 (같은 축 프롬프트는 식당마다 반복)
    """
    try:
        import torch
        from PIL import Image
        
        if not _load_clip():
            return 0.0
        
        model = _clip_cache["model"]
        preprocess = _clip_cache["preprocess"]
        
        # 이미지 인코딩 (매번 새로)
        image = preprocess(Image.open(image_path)).unsqueeze(0)
        with torch.no_grad():
            img_emb = model.encode_image(image)
            img_emb = img_emb / img_emb.norm(dim=-1, keepdim=True)
        
        # pos/neg 프롬프트 임베딩 (캐시 재사용)
        pos_sims = []
        for p in pos_prompts:
            text_emb = _get_text_embedding(p)
            sim = (img_emb @ text_emb.T).squeeze().item()
            pos_sims.append(sim)
        
        neg_sims = []
        for p in neg_prompts:
            text_emb = _get_text_embedding(p)
            sim = (img_emb @ text_emb.T).squeeze().item()
            neg_sims.append(sim)
        
        mean_pos = sum(pos_sims) / len(pos_sims) if pos_sims else 0
        mean_neg = sum(neg_sims) / len(neg_sims) if neg_sims else 0
        
        denom = abs(mean_pos) + abs(mean_neg)
        if denom < 0.01:
            return 0.0
        
        score = (mean_pos - mean_neg) / denom
        return float(np.clip(score, -1.0, 1.0))
    
    except Exception as e:
        return 0.0


# ============================================================
# 3. 축별 이미지 감성 벡터 생성
# ============================================================

def build_image_vectors(df_filtered, axes_config, keyword="food", mode="clip"):
    """
    [핵심 변경] 리뷰-조건부 축별 직접 CLIP 점수화
    
    흐름:
    1. 이미지가 있는 리뷰에서 어떤 축이 걸리는지 찾기
    2. 걸린 축마다 axes_config에서 pos/neg 프롬프트 생성
    3. 이미지와 직접 CLIP 비교 → 축별 점수
    4. restaurant_img_scores[식당][축].append(점수)
    """
    img_reviews = df_filtered[df_filtered["HasPicture"] == 1].copy()
    if len(img_reviews) == 0:
        print("  ⚠️ 이미지 있는 리뷰가 없습니다")
        return {}
    
    print(f"  📷 이미지 리뷰: {len(img_reviews)}건 / 전체 {len(df_filtered)}건")
    
    restaurant_img_scores = defaultdict(lambda: defaultdict(list))
    clip_calls = 0
    
    for _, row in img_reviews.iterrows():
        review_text = str(row.get("Review", ""))
        restaurant = str(row.get("Restaurant", ""))
        
        # 이미지 경로 추출
        image_path = None
        paths_str = str(row.get("ImagePaths", ""))
        if paths_str and paths_str not in ["", "nan"]:
            image_path = paths_str.split(" | ")[0].strip()
        
        for axis_name, axis_info in axes_config.items():
            pos_kws = axis_info.get("positive_keywords", axis_info.get("positive", []))
            neg_kws = axis_info.get("negative_keywords", axis_info.get("negative", []))
            
            pos_matched = any(kw in review_text for kw in pos_kws) if pos_kws else False
            neg_matched = any(kw in review_text for kw in neg_kws) if neg_kws else False
            
            if not (pos_matched or neg_matched):
                continue
            
            # ── 축별 CLIP 점수화 ──
            if mode == "clip" and image_path and os.path.exists(image_path):
                axis_pos_prompts, axis_neg_prompts = make_axis_prompts(
                    axis_name, axis_info, keyword
                )
                axis_score = analyze_image_clip_axis(
                    image_path, axis_pos_prompts, axis_neg_prompts
                )
                clip_calls += 1
            else:
                axis_score = analyze_image_sentiment_simple(review_text)
            
            if axis_score == 0:
                continue
            
            if pos_matched:
                restaurant_img_scores[restaurant][axis_name].append(axis_score)
            elif neg_matched:
                restaurant_img_scores[restaurant][axis_name].append(-axis_score)
    
    # 평균 계산
    img_vectors = {}
    for restaurant, axes in restaurant_img_scores.items():
        img_vectors[restaurant] = {
            axis_name: round(float(np.mean(scores)), 3)
            for axis_name, scores in axes.items()
        }
    
    print(f"  ✅ {len(img_vectors)}개 식당 이미지 벡터 생성")
    print(f"     CLIP 호출: {clip_calls}회 | 프롬프트 캐시: {len(_clip_cache['prompt_embeddings'])}개")
    
    return img_vectors




# ============================================================
# 4. Late Fusion: ① 텍스트 벡터 + ② 이미지 벡터 → 최종 벡터
# ============================================================

def late_fusion(text_vectors, image_vectors, alpha=ALPHA, beta=BETA):
    """
    Late Fusion: 두 벡터를 가중 합산.
    
    final_vector[축] = α × text_vector[축] + β × image_vector[축]
    
    α=0.7, β=0.3 (텍스트가 주, 이미지가 보조)
    
    이미지 벡터가 없는 축은 텍스트 벡터 그대로 유지.
    이미지 벡터가 없는 식당은 텍스트 벡터 그대로 유지.
    """
    fused = {}
    
    for restaurant, text_vec in text_vectors.items():
        img_vec = image_vectors.get(restaurant, {})
        
        fused[restaurant] = {}
        all_axes = set(list(text_vec.keys()) + list(img_vec.keys()))
        
        for axis in all_axes:
            t_score = text_vec.get(axis, 0.0)
            i_score = img_vec.get(axis, 0.0)
            
            if axis in img_vec and axis in text_vec:
                # 둘 다 있으면 Late Fusion
                fused[restaurant][axis] = round(alpha * t_score + beta * i_score, 3)
            elif axis in text_vec:
                # 텍스트만 있으면 그대로
                fused[restaurant][axis] = t_score
            else:
                # 이미지만 있으면 (드문 경우)
                fused[restaurant][axis] = round(beta * i_score, 3)
        
        # confidence 업데이트: 이미지 리뷰 비율 반영
        img_ratio = len(img_vec) / max(len(text_vec), 1)
        fused[restaurant]["_img_coverage"] = round(img_ratio, 2)
    
    return fused


# ============================================================
# 5. ★ 축별 대표 이미지 추출 (사용자 이미지 선택용)
# ============================================================

def extract_representative_images(df_filtered, axes_config, image_vectors, keyword, top_n=6):
    """
    각 맛 축에서 가장 점수가 높은 이미지를 추출하여
    사용자 이미지 선택 UI에 제공.
    
    전략:
    1. 이미지가 있는 리뷰 중, 각 축 점수가 가장 높은 리뷰의 이미지를 선택
    2. 축별 1장씩 → 중복 제거 → 상위 top_n장
    3. 각 이미지에 "이 사진이 대표하는 맛 특성" 라벨 부여
    
    출력: {keyword}_representative_images.json
    """
    img_reviews = df_filtered[df_filtered["HasPicture"] == 1].copy()
    
    if len(img_reviews) == 0:
        print("  ⚠️ 이미지 리뷰 없음 → 대표 이미지 추출 건너뜀")
        return []
    
    # 이미지 URL/경로 추출 함수
    def get_image_info(row):
        """리뷰에서 이미지 URL/경로 추출"""
        # ImageURLs 컬럼 (네이버 크롤러 출력)
        urls = str(row.get("ImageURLs", ""))
        if urls and urls not in ["", "nan"]:
            return urls.split(" | ")[0].strip()
        
        # ImagePaths 컬럼 (로컬 다운로드)
        paths = str(row.get("ImagePaths", ""))
        if paths and paths not in ["", "nan"]:
            return paths.split(" | ")[0].strip()
        
        # Image_Links 등 기타
        for col in ["Image_Links", "review_images", "image_url"]:
            val = str(row.get(col, ""))
            if val and val not in ["", "nan", "[]"]:
                return val.split("|")[0].strip().strip("[]'\"")
        
        return ""
    
    # 축별 맛 라벨 (사용자에게 보여줄 설명)
    AXIS_LABELS = {
        "매운맛": "매콤한 맛",
        "바삭함": "바삭한 식감",
        "촉촉쫄깃": "촉촉하고 부드러운",
        "감칠맛": "깊고 진한 맛",
        "고소함": "고소한 풍미",
        "담백함": "깔끔하고 담백한",
        "소스농도": "걸쭉한 소스",
        "재료퀄리티": "신선한 재료",
        "양많음": "푸짐한 양",
        "불맛웍향": "불맛/웍향",
        "면컨디션": "탱탱한 면",
        "두께": "두툼한 두께",
        "짠맛": "짭짤한 맛",
        "단맛": "달콤한 맛",
        "느끼함": "느끼하지 않은",
        "온도": "뜨끈하게 나온",
        "국물양": "국물 가득",
        "쫄깃함": "쫄깃한 식감",
        "전반적만족": "전반적으로 맛있는",
        "가성비": "가성비 좋은",
    }
    
    # 각 축별 최고 점수 이미지 찾기
    candidates = []
    seen_images = set()
    
    for axis_name, axis_info in axes_config.items():
        pos_kws = axis_info.get("positive_keywords", axis_info.get("positive", []))
        if not pos_kws:
            continue
        
        # 이 축의 키워드가 포함된 이미지 리뷰 찾기
        axis_matches = []
        
        for _, row in img_reviews.iterrows():
            review_text = str(row.get("Review", ""))
            restaurant = str(row.get("Restaurant", ""))
            image_src = get_image_info(row)
            
            if not image_src or image_src in seen_images:
                continue
            
            # 키워드 매칭 강도 계산
            match_count = sum(1 for kw in pos_kws if kw in review_text)
            if match_count == 0:
                continue
            
            # 이미지 벡터에서 해당 축 점수 확인
            img_axis_score = 0.0
            if restaurant in image_vectors:
                img_axis_score = image_vectors[restaurant].get(axis_name, 0.0)
            
            # 최종 점수 = 키워드 매칭 수 + 이미지 감성 점수
            combined_score = match_count + img_axis_score
            
            axis_matches.append({
                "image_src": image_src,
                "axis": axis_name,
                "label": AXIS_LABELS.get(axis_name, axis_name),
                "restaurant": restaurant,
                "score": round(combined_score, 3),
                "review_snippet": review_text[:80],
                "match_count": match_count,
            })
        
        if axis_matches:
            # 점수 높은 순 정렬 → 1장 선택
            axis_matches.sort(key=lambda x: -x["score"])
            best = axis_matches[0]
            seen_images.add(best["image_src"])
            candidates.append(best)
    
    # 점수 높은 순으로 top_n개만
    candidates.sort(key=lambda x: -x["score"])
    representative = candidates[:top_n]
    
    # CLIP 벡터 정보 추가 (사용자 선택 시 퓨전용)
    for item in representative:
        rest = item["restaurant"]
        if rest in image_vectors:
            item["clip_vector"] = image_vectors[rest]
        else:
            item["clip_vector"] = {}
    
    # 저장
    safe = keyword.replace(" ", "_")
    output = {
        "keyword": keyword,
        "total_candidates": len(candidates),
        "images": representative,
    }
    
    out_file = f"{safe}_representative_images.json"
    with open(out_file, "w", encoding="utf-8") as f:
        json.dump(output, f, ensure_ascii=False, indent=2)
    
    print(f"\n  🖼️  대표 이미지 {len(representative)}장 추출")
    for i, img in enumerate(representative, 1):
        print(f"    {i}. [{img['axis']}] {img['label']} — {img['restaurant'][:15]} (점수:{img['score']})")
    print(f"  → {out_file}")
    
    return representative


# ============================================================
# 6. 메인: ①+② 통합 파이프라인
# ============================================================

def main():
    if len(sys.argv) > 1:
        keyword = sys.argv[1]
    else:
        keyword = input("음식 키워드: ").strip()
    
    if not keyword:
        print("키워드를 입력해주세요!")
        return
    
    safe = keyword.replace(" ", "_")
    
    print(f"\n{'━'*55}")
    print(f"  🔀 TasteBridge 멀티모달 파이프라인")
    print(f"  키워드: '{keyword}' | 모드: {IMAGE_MODE}")
    print(f"  Fusion: α={ALPHA}(텍스트) + β={BETA}(이미지)")
    print(f"{'━'*55}")
    
    # ── STEP 1: ① 텍스트 벡터 로드 (food_profiler.py 출력물) ──
    print(f"\n📊 STEP 1: ① 텍스트 벡터 로드")
    
    vectors_file = f"{safe}_vectors.json"
    axes_file = f"{safe}_axes_config.json"
    
    if not os.path.exists(vectors_file):
        print(f"  ❌ {vectors_file} 없음!")
        print(f"  → 먼저 실행: python food_profiler.py {keyword}")
        return
    
    with open(vectors_file, "r", encoding="utf-8") as f:
        text_data = json.load(f)
    
    # axes config 로드 → STEP 1 이후에 vectors.json 에서 로드
    axes_config = {}
    
    # text_vectors 추출 (식당별 축별 점수)
    text_vectors = {}
    axes_from_file = text_data.get("axes", [])
    
    for restaurant, info in text_data.get("restaurants", {}).items():
        if isinstance(info, dict):
            # normalized 딕셔너리가 있으면 사용, 없으면 vector 배열 + axes로 매핑
            if "normalized" in info:
                text_vectors[restaurant] = info["normalized"]
            elif "vector" in info and axes_from_file:
                text_vectors[restaurant] = dict(zip(axes_from_file, info["vector"]))
            elif "scores" in info:
                text_vectors[restaurant] = info["scores"]
    
    # axes_config 로드: vectors 파일 내장 버전 우선 (positive_keywords 포맷)
    if "axes_config" in text_data:
        axes_config = text_data["axes_config"]
    elif os.path.exists(axes_file):
        with open(axes_file, "r", encoding="utf-8") as f:
            axes_config = json.load(f)
    
    print(f"  ✅ {len(text_vectors)}개 식당 텍스트 벡터 로드")
    
    # ── STEP 2: 리뷰+이미지 데이터 로드 ──
    print(f"\n📷 STEP 2: ② 이미지 리뷰 분석")
    
    df = load_reviews_with_images(keyword)
    if df is None or len(df) == 0:
        print("  ❌ 리뷰 데이터 없음!")
        return
    
    total = len(df)
    img_count = df["HasPicture"].sum()
    print(f"  전체 리뷰: {total}건 | 이미지 포함: {img_count}건 ({img_count/total*100:.1f}%)")
    
    # ── STEP 3: 이미지 감성 벡터 생성 ──
    print(f"\n🔍 STEP 3: 이미지 감성 벡터 생성")
    
    image_vectors = build_image_vectors(df, axes_config, keyword=keyword, mode=IMAGE_MODE)
    
    if image_vectors:
        print(f"  ✅ {len(image_vectors)}개 식당 이미지 벡터 생성")
        
        # 샘플 출력
        for rest, vec in list(image_vectors.items())[:3]:
            top_axes = sorted(vec.items(), key=lambda x: abs(x[1]), reverse=True)[:3]
            axes_str = ", ".join(f"{a}:{s:+.2f}" for a, s in top_axes)
            print(f"    {rest[:15]:15s} → {axes_str}")
    else:
        print("  ⚠️ 이미지 벡터 없음 (이미지 리뷰 부족)")
    
    # ── STEP 4: Late Fusion ──
    print(f"\n🔀 STEP 4: Late Fusion (α={ALPHA} × 텍스트 + β={BETA} × 이미지)")
    
    fused_vectors = late_fusion(text_vectors, image_vectors, ALPHA, BETA)
    
    # 변화가 있는 식당만 표시
    changed = 0
    for rest in fused_vectors:
        if rest in text_vectors and rest in image_vectors:
            changed += 1
    
    print(f"  ✅ 최종 벡터: {len(fused_vectors)}개 식당")
    print(f"     이미지로 보정된 식당: {changed}개")
    print(f"     텍스트만 사용: {len(fused_vectors) - changed}개")
    
    # 변화 상세
    if changed > 0:
        print(f"\n  📊 Fusion 전후 비교 (상위 3개 식당):")
        for rest in list(image_vectors.keys())[:3]:
            if rest not in text_vectors:
                continue
            print(f"\n    [{rest[:20]}]")
            for axis in list(image_vectors[rest].keys())[:5]:
                t = text_vectors.get(rest, {}).get(axis, 0)
                i = image_vectors.get(rest, {}).get(axis, 0)
                f_val = fused_vectors.get(rest, {}).get(axis, 0)
                arrow = "↑" if f_val > t else "↓" if f_val < t else "="
                print(f"      {axis:12s}  텍스트:{t:+.3f}  이미지:{i:+.3f}  → 최종:{f_val:+.3f} {arrow}")
    
    # ── STEP 4.5: 축별 대표 이미지 추출 (사용자 선택 UI용) ──
    print(f"\n🖼️  STEP 4.5: 축별 대표 이미지 추출")
    rep_images = extract_representative_images(df, axes_config, image_vectors, keyword, top_n=6)

    # ── STEP 5: 저장 ──
    output = {
        "keyword": keyword,
        "fusion_mode": "late",
        "alpha": ALPHA,
        "beta": BETA,
        "image_mode": IMAGE_MODE,
        "total_reviews": total,
        "image_reviews": int(img_count),
        "restaurants": {}
    }
    
    for rest in fused_vectors:
        output["restaurants"][rest] = {
            "fused_vector": fused_vectors[rest],
            "text_only_vector": text_vectors.get(rest, {}),
            "image_sentiment": image_vectors.get(rest, {}),
            "has_image_data": rest in image_vectors,
        }
    
    out_file = f"{safe}_multimodal_vectors.json"
    with open(out_file, "w", encoding="utf-8") as f:
        json.dump(output, f, ensure_ascii=False, indent=2)
    
    print(f"\n  💾 {out_file} 저장 완료")
    
    print(f"\n{'━'*55}")
    print(f"  완료! 실행 요약:")
    print(f"  ① 텍스트 벡터: {len(text_vectors)}개 식당 (food_profiler.py)")
    print(f"  ② 이미지 감성: {len(image_vectors)}개 식당 (이미지 리뷰 분석)")
    print(f"  ③ 최종 벡터:   {len(fused_vectors)}개 식당 (Late Fusion)")
    print(f"  ④ 대표 이미지: {len(rep_images)}장 (사용자 선택 UI용)")
    print(f"{'━'*55}")


if __name__ == "__main__":
    main()