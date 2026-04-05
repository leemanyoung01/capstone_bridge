"""
TasteBridge 최종 추천 엔진 (recommend.py) — 양쪽 멀티모달
==========================================================
모든 파이프라인의 결과를 통합하여 최종 추천을 수행.

변경점 (v2):
  1. 대표 이미지 로드 → 사용자 이미지 선택
  2. user_text_vec + user_image_vec → Late Fusion
  3. 식당 fused_vector vs 사용자 fused_vector 매칭
  4. 이미지 건너뛰기 시 텍스트만으로 동작 (하위 호환)

입력: 사용자 맛 선호도 (텍스트 + 이미지 선택)
출력: Top-N 추천 식당 + 주소 + 맛 프로필 + 근거 + 퓨전 모드

실행: python recommend.py 짜장면
설치: pip install numpy pandas
"""

import json
import os
import sys
import numpy as np
from collections import OrderedDict

# ============================================================
# 1. 데이터 로드
# ============================================================

def load_vectors(keyword):
    """멀티모달 벡터 우선, 없으면 텍스트 벡터 로드"""
    safe = keyword.replace(" ", "_")
    
    # 우선순위: multimodal > text-only
    for suffix in ["_multimodal_vectors.json", "_vectors.json"]:
        path = f"{safe}{suffix}"
        if os.path.exists(path):
            with open(path, "r", encoding="utf-8") as f:
                data = json.load(f)
            print(f"  벡터 로드: {path}")
            return data, "multimodal" if "multimodal" in suffix else "text"
    
    return None, None


def load_restaurant_db():
    """식당 주소 DB 로드"""
    if os.path.exists("restaurant_db.json"):
        with open("restaurant_db.json", "r", encoding="utf-8") as f:
            return json.load(f)
    return {}


def load_dictionary(keyword):
    """맛 사전 (근거 문장용)"""
    safe = keyword.replace(" ", "_")
    path = f"{safe}_dictionary.csv"
    
    if not os.path.exists(path):
        return {}
    
    import pandas as pd
    df = pd.read_csv(path, encoding="utf-8-sig")
    
    evidence = {}
    for _, row in df.iterrows():
        rest = str(row.get("Restaurant", ""))
        if rest not in evidence:
            evidence[rest] = {}
        
        for col in df.columns:
            if col in ["Restaurant", "review_count", "scored_count"]:
                continue
            val = row.get(col, 0)
            if isinstance(val, (int, float)) and val != 0:
                evidence[rest][col] = val
    
    return evidence


def load_representative_images(keyword):
    """축별 대표 이미지 로드 (사용자 이미지 선택용)"""
    safe = keyword.replace(" ", "_")
    path = f"{safe}_representative_images.json"
    
    if not os.path.exists(path):
        return []
    
    with open(path, "r", encoding="utf-8") as f:
        data = json.load(f)
    
    images = data.get("images", [])
    print(f"  대표 이미지: {path} ({len(images)}장)")
    return images


# ============================================================
# 2. 사용자 벡터 생성
# ============================================================

def create_user_vector_interactive(axes, axes_config=None):
    """
    대화형으로 사용자 맛 선호도 입력 → user_vector
    
    각 축에 대해 -1(싫음) ~ 0(무관) ~ +1(좋음) 입력
    """
    print(f"\n{'━'*50}")
    print(f"  🎯 맛 선호도 입력 (각 항목 -1 ~ 0 ~ +1)")
    print(f"     -1: 싫음  0: 상관없음  +1: 좋음")
    print(f"     Enter: 상관없음(0)")
    print(f"{'━'*50}")
    
    user_vector = {}
    
    for axis in axes:
        question = ""
        if axes_config and axis in axes_config:
            question = axes_config[axis].get("question", "")
        
        prompt = f"  {axis}"
        if question:
            prompt += f" ({question})"
        prompt += " [-1~+1]: "
        
        try:
            val = input(prompt).strip()
            if val == "":
                user_vector[axis] = 0.0
            else:
                user_vector[axis] = max(-1.0, min(1.0, float(val)))
        except (ValueError, EOFError):
            user_vector[axis] = 0.0
    
    return user_vector


def create_user_vector_preset(axes, preset="매운맛선호"):
    """미리 정의된 프리셋으로 user_vector 생성 (테스트용)"""
    presets = {
        "매운맛선호": {"매운맛": 0.8, "전반적만족": 0.5, "재주문의사": 0.3, "가성비": 0.4},
        "담백선호": {"담백함": 0.8, "깔끔함": 0.7, "느끼함": -0.8, "전반적만족": 0.5},
        "가성비선호": {"가성비": 0.9, "양많음": 0.8, "전반적만족": 0.4},
        "분위기선호": {"서비스": 0.7, "전반적만족": 0.6, "포장상태": 0.5},
    }
    
    preset_values = presets.get(preset, {})
    user_vector = {}
    for axis in axes:
        user_vector[axis] = preset_values.get(axis, 0.0)
    
    return user_vector


# ============================================================
# 2-B. 사용자 이미지 선택 (멀티모달)
# ============================================================

def select_images_interactive(rep_images):
    """
    대표 이미지 목록을 보여주고 사용자가 선택.
    
    각 이미지는 맛 축의 대표 사진으로,
    선택된 이미지들의 CLIP 벡터를 평균하여 user_image_vector 생성.
    """
    if not rep_images:
        print("\n  ⚠️ 대표 이미지가 없습니다 (건너뜀)")
        return []
    
    print(f"\n{'━'*50}")
    print(f"  📸 음식 이미지 선택 (시각적 취향 반영)")
    print(f"     끌리는 사진 번호를 선택하세요 (여러 개 가능)")
    print(f"     Enter: 건너뛰기 (텍스트만으로 추천)")
    print(f"{'━'*50}")
    
    for i, img in enumerate(rep_images, 1):
        axis = img.get("axis", "")
        label = img.get("label", axis)
        restaurant = img.get("restaurant", "")[:15]
        score = img.get("score", 0)
        has_src = "📷" if img.get("image_src") else "📝"
        
        print(f"    {i}) {has_src} {label:16s} — {restaurant} (축:{axis}, 점수:{score})")
    
    print(f"    0) 건너뛰기 (이미지 선택 없이 진행)")
    
    try:
        sel = input(f"\n  선택 (쉼표 구분, 예: 1,3,5): ").strip()
    except EOFError:
        sel = ""
    
    if not sel or sel == "0":
        print("  → 이미지 선택 건너뜀")
        return []
    
    try:
        indices = [int(x.strip()) - 1 for x in sel.split(",")]
        selected = [rep_images[i] for i in indices if 0 <= i < len(rep_images)]
    except (ValueError, IndexError):
        print("  → 입력 오류, 이미지 선택 건너뜀")
        return []
    
    if selected:
        print(f"  → {len(selected)}장 선택:")
        for img in selected:
            print(f"     · {img.get('label', img.get('axis', ''))}")
    
    return selected


def build_user_image_vector(selected_images, axes):
    """
    선택된 이미지들의 CLIP 벡터를 평균 → user_image_vector
    
    추가로 선택된 이미지의 대표 축에 보너스 부여:
    사용자가 '바삭한 식감' 이미지를 골랐다 → 바삭함 축 +0.5
    """
    if not selected_images:
        return {ax: 0.0 for ax in axes}
    
    axis_scores = {ax: [] for ax in axes}
    
    for img_info in selected_images:
        clip_vec = img_info.get("clip_vector", {})
        main_axis = img_info.get("axis", "")
        
        # clip_vector에서 축별 점수 추출
        for ax in axes:
            val = clip_vec.get(ax, 0.0)
            if isinstance(val, (int, float)):
                axis_scores[ax].append(val)
        
        # 대표 축 보너스
        if main_axis in axis_scores:
            axis_scores[main_axis].append(0.5)
    
    # 축별 평균
    user_img_vec = {}
    for ax in axes:
        vals = axis_scores[ax]
        user_img_vec[ax] = round(float(np.mean(vals)), 4) if vals else 0.0
    
    return user_img_vec


def fuse_user_vectors(text_vec, image_vec, axes, alpha=0.7, beta=0.3):
    """
    사용자 텍스트 벡터 + 이미지 벡터 → Late Fusion
    
    이미지 벡터가 영벡터면 텍스트만 사용 (하위 호환)
    
    Returns: (fused_vector, fusion_mode)
    """
    img_nonzero = sum(1 for v in image_vec.values() if abs(v) > 0.001)
    
    if img_nonzero == 0:
        return text_vec, "text_only"
    
    fused = {}
    for ax in axes:
        t = text_vec.get(ax, 0.0)
        i = image_vec.get(ax, 0.0)
        
        if abs(i) > 0.001 and abs(t) > 0.001:
            fused[ax] = round(alpha * t + beta * i, 4)
        elif abs(t) > 0.001:
            fused[ax] = t
        elif abs(i) > 0.001:
            fused[ax] = round(beta * i, 4)
        else:
            fused[ax] = 0.0
    
    return fused, "multimodal"


# ============================================================
# 3. 코사인 유사도 + 랭킹
# ============================================================

def cosine_similarity(vec_a, vec_b, axes):
    """두 벡터의 코사인 유사도 계산"""
    a = np.array([vec_a.get(axis, 0.0) for axis in axes])
    b = np.array([vec_b.get(axis, 0.0) for axis in axes])
    
    norm_a = np.linalg.norm(a)
    norm_b = np.linalg.norm(b)
    
    if norm_a == 0 or norm_b == 0:
        return 0.0
    
    return float(np.dot(a, b) / (norm_a * norm_b))


def compute_confidence(restaurant_data):
    """
    식당의 신뢰도 점수 (0~1)
    리뷰 수, 이미지 비율 등으로 계산
    """
    # 기본 신뢰도
    confidence = 0.5
    
    if isinstance(restaurant_data, dict):
        # 이미지 데이터 있으면 신뢰도 상승
        if restaurant_data.get("has_image_data"):
            confidence += 0.2
        
        # 벡터 값이 0이 아닌 축이 많으면 신뢰도 상승
        vec = restaurant_data.get("fused_vector", restaurant_data.get("normalized", {}))
        if isinstance(vec, dict):
            nonzero = sum(1 for v in vec.values() if isinstance(v, (int, float)) and v != 0)
            confidence += min(0.3, nonzero * 0.03)
    
    return min(1.0, confidence)


def rank_restaurants(user_vector, vectors_data, axes, top_n=10, alpha=0.5):
    """
    user_vector와 모든 식당 벡터의 유사도 계산 → 랭킹
    
    final_score = cosine_similarity × confidence^α
    """
    restaurants = vectors_data.get("restaurants", {})
    results = []
    
    for name, data in restaurants.items():
        # 벡터 추출 (multimodal 또는 text-only)
        if isinstance(data, dict):
            vec = data.get("fused_vector",
                  data.get("normalized",
                  data.get("text_only_vector", {})))
        else:
            continue
        
        if not isinstance(vec, dict):
            continue
        
        # 내부 메타데이터 제외
        clean_vec = {k: v for k, v in vec.items()
                     if not k.startswith("_") and isinstance(v, (int, float))}
        
        # 코사인 유사도
        sim = cosine_similarity(user_vector, clean_vec, axes)
        
        # 신뢰도
        conf = compute_confidence(data)
        
        # 최종 점수
        final = sim * (conf ** alpha)
        
        # 기여 축 Top-3 (왜 이 식당이 추천되는지)
        contributions = []
        for axis in axes:
            u_val = user_vector.get(axis, 0.0)
            r_val = clean_vec.get(axis, 0.0)
            if u_val != 0 and r_val != 0:
                contrib = u_val * r_val
                contributions.append((axis, contrib, r_val))
        
        contributions.sort(key=lambda x: abs(x[1]), reverse=True)
        top_reasons = contributions[:3]
        
        results.append({
            "name": name,
            "similarity": round(sim, 4),
            "confidence": round(conf, 2),
            "final_score": round(final, 4),
            "vector": clean_vec,
            "top_reasons": top_reasons,
            "has_image": data.get("has_image_data", False) if isinstance(data, dict) else False,
        })
    
    # 최종 점수 기준 정렬
    results.sort(key=lambda x: x["final_score"], reverse=True)
    
    return results[:top_n]


# ============================================================
# 4. 결과 출력 (주소 포함)
# ============================================================

def print_results(results, restaurant_db, keyword, fusion_mode="text_only"):
    """추천 결과를 주소/전화번호와 함께 출력"""
    
    mode_label = "🔀 멀티모달 (텍스트+이미지)" if fusion_mode == "multimodal" else "📝 텍스트 기반"
    
    print(f"\n{'═'*60}")
    print(f"  🏆 TasteBridge 추천 결과: '{keyword}'")
    print(f"  추천 모드: {mode_label}")
    print(f"{'═'*60}")
    
    for i, r in enumerate(results, 1):
        name = r["name"]
        sim = r["similarity"]
        conf = r["confidence"]
        final = r["final_score"]
        has_img = "📷" if r["has_image"] else "  "
        
        # DB에서 주소 조회
        db_info = restaurant_db.get(name, {})
        address = db_info.get("road_address") or db_info.get("address") or ""
        phone = db_info.get("phone", "")
        naver_url = db_info.get("naver_url", db_info.get("link", ""))
        
        # 신뢰도 배지
        if conf >= 0.8:
            badge = "🟢 High"
        elif conf >= 0.5:
            badge = "🟡 Mid"
        else:
            badge = "🔴 Low"
        
        print(f"\n  {'─'*56}")
        print(f"  #{i}  {name}  {has_img}")
        print(f"      유사도: {sim:.1%}  |  신뢰도: {badge}  |  점수: {final:.3f}")
        
        if address:
            print(f"      📍 {address}")
        if phone:
            print(f"      📞 {phone}")
        if naver_url:
            print(f"      🗺️  {naver_url}")
        
        # 추천 근거
        if r["top_reasons"]:
            reasons = []
            for axis, contrib, r_val in r["top_reasons"]:
                direction = "+" if r_val > 0 else "-"
                reasons.append(f"{axis}({direction}{abs(r_val):.2f})")
            print(f"      💡 기여 축: {' / '.join(reasons)}")
    
    print(f"\n{'═'*60}")


def export_results_json(results, restaurant_db, keyword, fusion_mode="text_only"):
    """추천 결과를 JSON으로 저장 (프론트엔드 연동용)"""
    output = {
        "keyword": keyword,
        "fusion_mode": fusion_mode,
        "recommendations": []
    }
    
    for i, r in enumerate(results, 1):
        name = r["name"]
        db_info = restaurant_db.get(name, {})
        
        rec = {
            "rank": i,
            "name": name,
            "similarity": r["similarity"],
            "confidence": r["confidence"],
            "final_score": r["final_score"],
            "has_image_data": r["has_image"],
            "address": db_info.get("road_address") or db_info.get("address", ""),
            "phone": db_info.get("phone", ""),
            "lat": db_info.get("lat", 0),
            "lng": db_info.get("lng", 0),
            "naver_url": db_info.get("naver_url", db_info.get("link", "")),
            "category": db_info.get("category", ""),
            "top_reasons": [
                {"axis": axis, "contribution": round(contrib, 3), "score": round(r_val, 3)}
                for axis, contrib, r_val in r["top_reasons"]
            ],
            "taste_profile": r["vector"],
            "fusion_mode": fusion_mode,
        }
        output["recommendations"].append(rec)
    
    safe = keyword.replace(" ", "_")
    path = f"{safe}_recommendations.json"
    with open(path, "w", encoding="utf-8") as f:
        json.dump(output, f, ensure_ascii=False, indent=2)
    
    print(f"  💾 {path} 저장 완료 (프론트엔드 연동용)")
    return path


# ============================================================
# 5. 메인
# ============================================================

def main():
    if len(sys.argv) > 1:
        keyword = sys.argv[1]
    else:
        keyword = input("음식 키워드 (예: 짜장면): ").strip()
    
    if not keyword:
        print("키워드를 입력해주세요!")
        return
    
    print(f"\n{'━'*60}")
    print(f"  🍽️  TasteBridge 추천 엔진 (양쪽 멀티모달)")
    print(f"  키워드: '{keyword}'")
    print(f"{'━'*60}")
    
    # ── 데이터 로드 ──
    vectors_data, vec_type = load_vectors(keyword)
    if not vectors_data:
        print(f"\n  ❌ 벡터 파일 없음!")
        print(f"  → 먼저 실행: python food_profiler.py {keyword}")
        return
    
    restaurant_db = load_restaurant_db()
    rep_images = load_representative_images(keyword)
    
    axes = vectors_data.get("axes", [])
    axes_config = vectors_data.get("axes_config", {})
    restaurants = vectors_data.get("restaurants", {})
    
    # multimodal 파일에 axes가 없으면 원본 vectors에서 로드
    if not axes:
        safe = keyword.replace(" ", "_")
        orig_path = f"{safe}_vectors.json"
        if os.path.exists(orig_path):
            with open(orig_path, "r", encoding="utf-8") as f:
                orig = json.load(f)
            axes = orig.get("axes", [])
            axes_config = orig.get("axes_config", {})
            print(f"  축 정보: {orig_path}에서 로드")
    
    print(f"  벡터 타입: {vec_type}")
    print(f"  식당 수: {len(restaurants)}개")
    print(f"  축 수: {len(axes)}개")
    print(f"  주소 DB: {len(restaurant_db)}개")
    print(f"  대표 이미지: {len(rep_images)}장")
    
    # ════════════════════════════════════════════
    # STEP 1: 사용자 텍스트 벡터 생성
    # ════════════════════════════════════════════
    print(f"\n  [STEP 1] 텍스트 취향 입력:")
    print(f"    1) 대화형 (직접 입력)")
    print(f"    2) 매운맛 선호 프리셋")
    print(f"    3) 담백 선호 프리셋")
    print(f"    4) 가성비 선호 프리셋")
    
    try:
        choice = input("\n  선택 [1-4]: ").strip()
    except EOFError:
        choice = "2"
    
    if choice == "1":
        user_text_vec = create_user_vector_interactive(axes, axes_config)
    elif choice == "3":
        user_text_vec = create_user_vector_preset(axes, "담백선호")
    elif choice == "4":
        user_text_vec = create_user_vector_preset(axes, "가성비선호")
    else:
        user_text_vec = create_user_vector_preset(axes, "매운맛선호")
    
    # 텍스트 벡터 요약
    nonzero = {k: v for k, v in user_text_vec.items() if v != 0}
    if nonzero:
        print(f"\n  📊 텍스트 벡터:")
        for axis, val in sorted(nonzero.items(), key=lambda x: -abs(x[1])):
            bar = "█" * int(abs(val) * 10)
            sign = "+" if val > 0 else "-"
            print(f"    {axis:12s} {sign}{abs(val):.1f} {bar}")
    
    # ════════════════════════════════════════════
    # STEP 2: 사용자 이미지 선택 (선택 사항)
    # ════════════════════════════════════════════
    print(f"\n  [STEP 2] 이미지 취향 선택:")
    selected_images = select_images_interactive(rep_images)
    
    # ════════════════════════════════════════════
    # STEP 3: 사용자 벡터 퓨전
    # ════════════════════════════════════════════
    if selected_images:
        user_img_vec = build_user_image_vector(selected_images, axes)
        user_vector, fusion_mode = fuse_user_vectors(user_text_vec, user_img_vec, axes)
        
        # 퓨전 결과 요약
        print(f"\n  🔀 사용자 벡터 퓨전: {fusion_mode}")
        if fusion_mode == "multimodal":
            # 이미지로 인해 변화된 축 표시
            changed_axes = []
            for ax in axes:
                t = user_text_vec.get(ax, 0)
                f_val = user_vector.get(ax, 0)
                if abs(f_val - t) > 0.001:
                    changed_axes.append((ax, t, f_val))
            
            if changed_axes:
                print(f"  이미지 선택으로 변화된 축:")
                for ax, before, after in sorted(changed_axes, key=lambda x: -abs(x[2]-x[1])):
                    arrow = "↑" if after > before else "↓"
                    print(f"    {ax:12s}  {before:+.2f} → {after:+.2f} {arrow}")
    else:
        user_vector = user_text_vec
        fusion_mode = "text_only"
        print(f"\n  📝 텍스트만 사용 (이미지 선택 없음)")
    
    # ════════════════════════════════════════════
    # STEP 4: 추천 실행
    # ════════════════════════════════════════════
    results = rank_restaurants(user_vector, vectors_data, axes, top_n=10)
    
    if not results:
        print("\n  ❌ 추천 결과 없음")
        return
    
    # ── 결과 출력 ──
    print_results(results, restaurant_db, keyword, fusion_mode)
    
    # ── JSON 저장 ──
    export_results_json(results, restaurant_db, keyword, fusion_mode)


if __name__ == "__main__":
    main()
