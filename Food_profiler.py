"""
Food Profiler v4 — Stand JSON 완전 연동
=========================================
stand/ 폴더의 3개 JSON을 읽어서:
  - stand_axes.json  → 축 조립 (맛축 vs 메타축 분리)
  - stand_lexicon.json → 키워드 사전 + CLIP 프롬프트
  - stand_rules.json → 점수화 규칙 (하드코딩 제거)

CLIP은 여기서 실행하지 않음.

실행: python Food_profiler.py 짜장면
입력: stand/ 폴더 JSON + 현재 폴더 CSV
출력: {키워드}_vectors.json / _dictionary.csv / _axes_config.json
"""

import pandas as pd
import numpy as np
import json
import os
import sys
import glob
from collections import defaultdict

STAND_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "stand")

# ============================================================
# 1. Stand 로더
# ============================================================

_stand_cache = None

def load_stand():
    """stand/ 폴더에서 JSON 3개 로드. 캐시하여 중복 로드 방지."""
    global _stand_cache
    if _stand_cache is not None:
        return _stand_cache

    paths = {
        "axes":    os.path.join(STAND_DIR, "stand_axes.json"),
        "lexicon": os.path.join(STAND_DIR, "stand_lexicon.json"),
        "rules":   os.path.join(STAND_DIR, "stand_rules.json"),
    }

    if not all(os.path.exists(p) for p in paths.values()):
        missing = [k for k, p in paths.items() if not os.path.exists(p)]
        print(f"  ⚠️ stand/ 파일 누락: {missing} → 하드코딩 폴백")
        return None

    stand = {}
    for key, path in paths.items():
        with open(path, "r", encoding="utf-8") as f:
            stand[key] = json.load(f)

    print(f"  ✅ Stand 로드 완료 ({STAND_DIR})")
    _stand_cache = stand
    return stand


# ============================================================
# 2. 축 조립 — 맛축 + 메타축 분리
# ============================================================

def compose_axes(keyword):
    """
    Stand JSON에서 keyword에 맞는 축 조립.
    반환: (taste_axes_dict, meta_axes_dict)
    각 dict = {축이름: {positive, negative, group, clip_prompt_pos, clip_prompt_neg}}
    """
    stand = load_stand()
    if not stand:
        return _fallback_axes()

    axes_def = stand["axes"]
    lexicon = stand["lexicon"]

    def build_axis_entry(axis_name, group, lex_section, lex_key=None):
        lex = lex_section.get(lex_key or axis_name, {})
        return {
            "positive": lex.get("positive", []),
            "negative": lex.get("negative", []),
            "group": group,
            "clip_prompt_pos": lex.get("clip_prompt_pos", []),
            "clip_prompt_neg": lex.get("clip_prompt_neg", []),
        }

    # 메타축
    meta = {}
    for name, info in axes_def.get("meta_axes", {}).items():
        if name.startswith("_"):
            continue
        meta[name] = build_axis_entry(name, info.get("group", "메타"), lexicon.get("common", {}))
        meta[name]["_weight"] = info.get("weight", 0.3)

    # 맛축 (taste)
    taste = {}
    for name, info in axes_def.get("taste_axes", {}).items():
        if name.startswith("_"):
            continue
        taste[name] = build_axis_entry(name, info.get("group", "맛"), lexicon.get("shared", {}))

    # 음식별 특수 축 매칭
    food_specific = axes_def.get("food_specific_axes", {})
    matched = None
    for category in food_specific:
        if category in keyword or keyword in category:
            matched = category
            break

    # 별칭으로도 매칭
    if not matched:
        for food, aliases in axes_def.get("keyword_aliases", {}).items():
            if keyword in aliases or food in keyword or keyword in food:
                for cat in food_specific:
                    if cat in food or food in cat:
                        matched = cat
                        break
            if matched:
                break

    if matched:
        print(f"  📋 '{keyword}' → '{matched}' 카테고리 매칭")
        spec_lex = lexicon.get("food_specific", {}).get(matched, {})
        for name, info in food_specific[matched].items():
            taste[name] = build_axis_entry(
                name, info.get("group", matched + "특화"), spec_lex
            )
    else:
        print(f"  📋 '{keyword}' → 카테고리 없음 → 공용 전체 사용")

    return taste, meta


def _fallback_axes():
    """Stand JSON 없을 때 하드코딩 폴백"""
    taste = {
        "매운맛": {"positive":["매운","매콤","얼큰"],"negative":["안 매","순한"],"group":"맛","clip_prompt_pos":[],"clip_prompt_neg":[]},
        "바삭함": {"positive":["바삭","겉바","크리스피"],"negative":["눅눅"],"group":"식감","clip_prompt_pos":[],"clip_prompt_neg":[]},
        "촉촉함": {"positive":["촉촉","부드러","육즙"],"negative":["퍽퍽"],"group":"식감","clip_prompt_pos":[],"clip_prompt_neg":[]},
    }
    meta = {
        "전반적만족": {"positive":["맛있","맛나","최고","굿"],"negative":["맛없","별로","실망"],"group":"메타","_weight":0.3,"clip_prompt_pos":[],"clip_prompt_neg":[]},
    }
    return taste, meta


# ============================================================
# 3. 점수 계산 — stand_rules.json에서 읽은 값 사용
# ============================================================

def score_review(row, taste_axes, meta_axes, rules):
    """
    리뷰 1건 → 축별 점수.
    rules: stand_rules.json에서 읽은 dict. 여기서 점수값/강조어/부정어를 가져옴.
    """
    text = str(row.get("Review", "")) if pd.notna(row.get("Review")) else ""
    taste_r = row.get("Taste", 0) if pd.notna(row.get("Taste", None)) else 0
    qty_r = row.get("Quantity", 0) if pd.notna(row.get("Quantity", None)) else 0
    total_r = row.get("Total", 0) if pd.notna(row.get("Total", None)) else 0

    # ── rules에서 점수값 읽기 (하드코딩 제거) ──
    scoring = rules.get("scoring", {})
    POS_HIT = scoring.get("positive_hit", 1.0)
    NEG_HIT = scoring.get("negative_hit", -0.5)
    intensifiers = rules.get("intensifiers", {})
    negations = rules.get("negations", [])
    adjustments = rules.get("rating_adjustments", {})

    all_axes = {**taste_axes, **meta_axes}
    scores = {axis: 0.0 for axis in all_axes}
    has_specific = False

    for axis_name, axis_info in all_axes.items():
        for kw in axis_info.get("positive", []):
            if kw in text:
                # 강조어 체크
                multiplier = 1.0
                for intens, mult in intensifiers.items():
                    if intens + kw in text or intens + " " + kw in text:
                        multiplier = mult
                        break

                # 부정어 체크
                negated = False
                for neg in negations:
                    if neg + " " + kw in text or neg + kw in text:
                        negated = True
                        break

                if negated:
                    scores[axis_name] += NEG_HIT * multiplier
                else:
                    scores[axis_name] += POS_HIT * multiplier

                if axis_name not in meta_axes:
                    has_specific = True

        for kw in axis_info.get("negative", []):
            if kw in text:
                multiplier = 1.0
                for intens, mult in intensifiers.items():
                    if intens + " " + kw in text or intens + kw in text:
                        multiplier = mult
                        break
                scores[axis_name] += NEG_HIT * multiplier
                if axis_name not in meta_axes:
                    has_specific = True

    # ── 숫자 평점 보정 (rules에서 읽기) ──
    if "전반적만족" in scores and "전반적만족" in adjustments:
        adj = adjustments["전반적만족"]
        if scores["전반적만족"] > 0 and not has_specific:
            t_ge = adj.get("taste_ge", [4, 0.3])
            t_le = adj.get("taste_le", [2, -0.5])
            if taste_r >= t_ge[0]: scores["전반적만족"] += t_ge[1]
            elif taste_r <= t_le[0]: scores["전반적만족"] += t_le[1]

        tot_le = adj.get("total_le", [2, -1.0])
        tot_ge = adj.get("total_ge_default", [5, 0.3])
        if total_r <= tot_le[0]: scores["전반적만족"] += tot_le[1]
        elif total_r >= tot_ge[0] and scores["전반적만족"] == 0: scores["전반적만족"] += tot_ge[1]

    if "양많음" in scores and "양많음" in adjustments:
        adj = adjustments["양많음"]
        q_ge = adj.get("quantity_ge", [5, 0.2])
        q_le = adj.get("quantity_le", [2, -0.2])
        if qty_r >= q_ge[0] and scores["양많음"] == 0: scores["양많음"] += q_ge[1]
        elif qty_r <= q_le[0] and scores["양많음"] == 0: scores["양많음"] += q_le[1]

    return scores


# ============================================================
# 4. CSV 로드 + 필터링
# ============================================================

def load_csvs():
    frames = []
    for f in sorted(glob.glob("*.csv")):
        try:
            tmp = pd.read_csv(f, encoding="utf-8-sig")
            if "Review" in tmp.columns and "Restaurant" in tmp.columns:
                frames.append(tmp)
                print(f"  ✓ {f}: {len(tmp)}건")
        except:
            pass
    if not frames:
        print("  ERROR: 유효한 CSV가 없습니다!")
        return None
    df = pd.concat(frames, ignore_index=True)
    df = df.drop_duplicates(subset=["Restaurant", "Date", "Review"], keep="first")
    return df


def filter_reviews(df, keyword):
    stand = load_stand()
    aliases = stand["axes"].get("keyword_aliases", {}) if stand else {}
    search_terms = aliases.get(keyword, [keyword])
    if keyword not in search_terms:
        search_terms.append(keyword)

    def has_kw(row):
        text = (str(row.get("Menu", "")) + " " + str(row.get("Review", ""))).lower()
        return any(t.lower() in text for t in search_terms)

    mask = df.apply(has_kw, axis=1)
    return df[mask].copy(), mask.sum()


# ============================================================
# 5. 프로필 생성
# ============================================================

def build_profiles(df, keyword, taste_axes, meta_axes, rules):
    """식당별 프로필 생성. 맛축과 메타축 모두 점수화하되 분리 보관."""
    all_axes = {**taste_axes, **meta_axes}
    axes_names = list(all_axes.keys())
    taste_names = list(taste_axes.keys())
    meta_names = list(meta_axes.keys())
    profiles = {}

    for restaurant in df["Restaurant"].unique():
        sub = df[df["Restaurant"] == restaurant]
        total_count = len(sub)
        if total_count == 0:
            continue

        kw_count = len(sub[sub.apply(
            lambda r: keyword in str(r.get("Menu", "")) + str(r.get("Review", "")), axis=1
        )])

        sum_scores = {a: 0.0 for a in axes_names}
        evidence = {a: [] for a in axes_names}
        scored_count = 0

        for _, row in sub.iterrows():
            sc = score_review(row, taste_axes, meta_axes, rules)
            if any(s != 0 for s in sc.values()):
                scored_count += 1
            for a in axes_names:
                sum_scores[a] += sc[a]
                if sc[a] != 0 and len(evidence[a]) < 3:
                    txt = str(row.get("Review", ""))[:100]
                    if txt and txt not in evidence[a]:
                        evidence[a].append(txt)

        if scored_count > 0:
            normalized = {a: round(sum_scores[a] / scored_count, 4) for a in axes_names}
        else:
            normalized = {a: 0.0 for a in axes_names}

        avg_total = round(sub["Total"].mean(), 2) if "Total" in sub.columns else 0
        avg_taste = round(sub["Taste"].mean(), 2) if "Taste" in sub.columns and sub["Taste"].notna().any() else 0

        profiles[restaurant] = {
            "vector": [normalized[a] for a in axes_names],
            "normalized": normalized,
            "taste_vector": {a: normalized[a] for a in taste_names},
            "meta_vector": {a: normalized[a] for a in meta_names},
            "evidence": evidence,
            "total_reviews": total_count,
            "keyword_reviews": kw_count,
            "scored_reviews": scored_count,
            "avg_total": avg_total,
            "avg_taste": avg_taste,
        }

    return profiles, axes_names


# ============================================================
# 6. 저장
# ============================================================

def save_results(keyword, profiles, axes_names, all_axes, taste_axes, meta_axes):
    safe = keyword.replace(" ", "_")

    # CSV
    rows = []
    for name, p in profiles.items():
        row = {"음식점": name, "평점": p["avg_total"], "리뷰수": p["total_reviews"],
               f"{keyword}리뷰": p["keyword_reviews"]}
        for a in axes_names:
            row[a] = p["normalized"][a]
        rows.append(row)
    pd.DataFrame(rows).to_csv(f"{safe}_dictionary.csv", index=False, encoding="utf-8-sig")

    # Vectors JSON
    groups = defaultdict(list)
    for name, info in all_axes.items():
        groups[info.get("group", "기타")].append(name)

    output = {
        "keyword": keyword,
        "axes": axes_names,
        "taste_axes": list(taste_axes.keys()),
        "meta_axes": list(meta_axes.keys()),
        "groups": dict(groups),
        "axes_config": {
            name: {
                "group": info.get("group", "기타"),
                "positive_keywords": info.get("positive", []),
                "negative_keywords": info.get("negative", []),
                "clip_prompt_pos": info.get("clip_prompt_pos", []),
                "clip_prompt_neg": info.get("clip_prompt_neg", []),
                "is_meta": name in meta_axes,
            }
            for name, info in all_axes.items()
        },
        "restaurants": {
            name: {
                "vector": p["vector"], "normalized": p["normalized"],
                "taste_vector": p["taste_vector"],
                "meta_vector": p["meta_vector"],
                "evidence": p["evidence"],
                "total_reviews": p["total_reviews"],
                "keyword_reviews": p["keyword_reviews"],
                "avg_total": p["avg_total"], "avg_taste": p["avg_taste"],
            }
            for name, p in profiles.items()
        },
    }
    with open(f"{safe}_vectors.json", "w", encoding="utf-8") as f:
        json.dump(output, f, ensure_ascii=False, indent=2)

    # Axes config JSON
    cfg = {name: {
        "group": info.get("group", "기타"),
        "keywords": info.get("positive", []),
        "clip_prompt_pos": info.get("clip_prompt_pos", []),
        "clip_prompt_neg": info.get("clip_prompt_neg", []),
    } for name, info in all_axes.items()}
    with open(f"{safe}_axes_config.json", "w", encoding="utf-8") as f:
        json.dump(cfg, f, ensure_ascii=False, indent=2)

    print(f"\n  ✅ {safe}_dictionary.csv")
    print(f"  ✅ {safe}_vectors.json")
    print(f"  ✅ {safe}_axes_config.json")


# ============================================================
# 7. 메인
# ============================================================

def main():
    if len(sys.argv) > 1:
        keyword = sys.argv[1]
    else:
        keyword = input("분석할 음식 키워드 (예: 짜장면, 삼겹살): ").strip()

    if not keyword:
        print("키워드를 입력해주세요!")
        return

    print(f"\n{'━'*50}")
    print(f"  🔍 '{keyword}' 맛 프로파일러 (Stand v2.1)")
    print(f"{'━'*50}")

    # 축 조립
    taste_axes, meta_axes = compose_axes(keyword)
    all_axes = {**taste_axes, **meta_axes}
    axes_names = list(all_axes.keys())
    print(f"  맛축 {len(taste_axes)}개: {', '.join(taste_axes.keys())}")
    print(f"  메타축 {len(meta_axes)}개: {', '.join(meta_axes.keys())}")

    # rules 로드
    stand = load_stand()
    rules = stand["rules"] if stand else {"scoring": {"positive_hit": 1.0, "negative_hit": -0.5}}

    # CSV 로드
    print(f"\n📂 CSV 로드...")
    df = load_csvs()
    if df is None:
        return
    print(f"  전체 {len(df)}건, 음식점 {df['Restaurant'].nunique()}개")

    # 필터
    filtered, kw_count = filter_reviews(df, keyword)
    print(f"  '{keyword}' 관련: {kw_count}건 ({kw_count/len(df)*100:.1f}%)")
    if kw_count == 0:
        print(f"  ❌ '{keyword}' 관련 리뷰 없음")
        return

    # 프로필 생성
    print(f"\n📊 프로필 생성...")
    profiles, axes_names = build_profiles(df, keyword, taste_axes, meta_axes, rules)

    # 출력
    for name, p in sorted(profiles.items(), key=lambda x: -x[1]["keyword_reviews"]):
        if p["keyword_reviews"] == 0:
            continue
        print(f"\n  🏪 {name} ({p['total_reviews']}건, 평점 {p['avg_total']})")
        top = sorted(p["taste_vector"].items(), key=lambda x: abs(x[1]), reverse=True)
        for ax, val in top[:5]:
            if abs(val) > 0.001:
                bar = ("+" if val > 0 else "-") * min(int(abs(val) * 10), 15)
                print(f"     {ax:12s} {val:+.3f}  {bar}")

    # 저장
    print(f"\n💾 저장...")
    save_results(keyword, profiles, axes_names, all_axes, taste_axes, meta_axes)
    print(f"\n📋 완료! 맛축 {len(taste_axes)}개 + 메타축 {len(meta_axes)}개 = {len(axes_names)}개")


if __name__ == "__main__":
    main()
