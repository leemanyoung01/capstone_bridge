"""
Food Profiler v5 — PostgreSQL 연동
=====================================
DB에서 리뷰 조회 → 맛 벡터 생성 → DB + JSON 저장.

실행:
  python Food_profiler.py 삼겹살          # DB에서 리뷰 조회
  python Food_profiler.py 삼겹살 --csv   # CSV에서 리뷰 조회 (로컬 테스트용)
"""

import pandas as pd, numpy as np, json, os, sys, glob
from collections import defaultdict
from db import (get_conn, upsert_restaurant, upsert_taste_profile,
                upsert_axes_config, insert_review, get_reviews_for_profiler)

STAND_DIR    = os.path.join(os.path.dirname(os.path.abspath(__file__)), "stand")
_stand_cache = None


# ── Stand JSON 로드 ───────────────────────────────────────────

def load_stand() -> dict | None:
    global _stand_cache
    if _stand_cache:
        return _stand_cache
    paths = {k: os.path.join(STAND_DIR, f"stand_{k}.json")
             for k in ("axes", "lexicon", "rules")}
    if not all(os.path.exists(p) for p in paths.values()):
        print(f"  ⚠️ stand/ 파일 누락: {[k for k,p in paths.items() if not os.path.exists(p)]} → 폴백")
        return None
    stand = {}
    for k, p in paths.items():
        with open(p, "r", encoding="utf-8") as f:
            stand[k] = json.load(f)
    print(f"  ✅ Stand 로드 ({STAND_DIR})")
    _stand_cache = stand
    return stand


def get_search_terms(keyword: str) -> list[str]:
    stand = load_stand()
    aliases = stand["axes"].get("keyword_aliases", {}) if stand else {}
    terms = list(aliases.get(keyword, [keyword]))
    if keyword not in terms:
        terms.append(keyword)
    return terms


def compose_axes(keyword: str) -> tuple[dict, dict]:
    stand = load_stand()
    if not stand:
        return _fallback_axes()

    axes_def, lexicon = stand["axes"], stand["lexicon"]

    def build(name, group, lex_sec):
        lex = lex_sec.get(name, {})
        return {
            "positive":        lex.get("positive", []),
            "negative":        lex.get("negative", []),
            "group":           group,
            "clip_prompt_pos": lex.get("clip_prompt_pos", []),
            "clip_prompt_neg": lex.get("clip_prompt_neg", []),
        }

    meta = {}
    for name, info in axes_def.get("meta_axes", {}).items():
        if name.startswith("_"): continue
        meta[name] = {**build(name, info.get("group","메타"), lexicon.get("common",{})),
                      "_weight": info.get("weight", 0.3)}

    taste = {}
    for name, info in axes_def.get("taste_axes", {}).items():
        if name.startswith("_"): continue
        taste[name] = build(name, info.get("group","맛"), lexicon.get("shared",{}))

    # 음식 특화 축 매핑
    food_specific = axes_def.get("food_specific_axes", {})
    alias_to_cat  = axes_def.get("alias_to_category", {})
    matched = None

    for cat in food_specific:
        if cat in keyword or keyword in cat:
            matched = cat; break
    if not matched and keyword in alias_to_cat and alias_to_cat[keyword] in food_specific:
        matched = alias_to_cat[keyword]
    if not matched:
        for food, aliases in axes_def.get("keyword_aliases", {}).items():
            if keyword in aliases or food in keyword or keyword in food:
                if food in alias_to_cat and alias_to_cat[food] in food_specific:
                    matched = alias_to_cat[food]; break
                for cat in food_specific:
                    if cat in food or food in cat:
                        matched = cat; break
            if matched: break

    if matched:
        print(f"  📋 '{keyword}' → '{matched}' 카테고리")
        spec_lex = lexicon.get("food_specific", {}).get(matched, {})
        for name, info in food_specific[matched].items():
            lex = spec_lex.get(name, {})
            taste[name] = {
                "positive": lex.get("positive",[]), "negative": lex.get("negative",[]),
                "group": info.get("group", matched+"특화"),
                "clip_prompt_pos":[], "clip_prompt_neg":[],
            }
    else:
        print(f"  📋 '{keyword}' → 공용 축 전체 사용")

    return taste, meta


def _fallback_axes():
    taste = {
        "매운맛": {"positive":["매운","매콤","얼큰"],"negative":["안 매","순한"],"group":"맛","clip_prompt_pos":[],"clip_prompt_neg":[]},
        "바삭함": {"positive":["바삭","겉바","크리스피"],"negative":["눅눅"],"group":"식감","clip_prompt_pos":[],"clip_prompt_neg":[]},
    }
    meta = {
        "전반적만족": {"positive":["맛있","맛나","최고","굿"],"negative":["맛없","별로","최악"],"group":"메타","_weight":0.3,"clip_prompt_pos":[],"clip_prompt_neg":[]},
    }
    return taste, meta


# ── 리뷰 스코어링 ─────────────────────────────────────────────

def score_review(row: dict, taste_axes: dict, meta_axes: dict, rules: dict) -> dict:
    text    = str(row.get("Review","") or row.get("content","") or "")
    total_r = float(row.get("Total",0) or row.get("rating",0) or 0)
    taste_r = float(row.get("Taste",0) or 0)
    qty_r   = float(row.get("Quantity",0) or 0)

    scoring = rules.get("scoring", {})
    POS_HIT = scoring.get("positive_hit",  1.0)
    NEG_HIT = scoring.get("negative_hit", -0.5)
    intens  = rules.get("intensifiers", {})
    negs    = rules.get("negations", [])
    adjs    = rules.get("rating_adjustments", {})

    all_axes = {**taste_axes, **meta_axes}
    scores   = {a: 0.0 for a in all_axes}
    has_specific = False

    for ax, info in all_axes.items():
        for kw in info.get("positive", []):
            if kw in text:
                mult = next((m for k,m in intens.items() if k+kw in text or k+" "+kw in text), 1.0)
                negated = any(n+" "+kw in text or n+kw in text for n in negs)
                scores[ax] += (NEG_HIT if negated else POS_HIT) * mult
                if ax not in meta_axes: has_specific = True
        for kw in info.get("negative", []):
            if kw in text:
                mult = next((m for k,m in intens.items() if k+" "+kw in text or k+kw in text), 1.0)
                scores[ax] += NEG_HIT * mult
                if ax not in meta_axes: has_specific = True

    if "전반적만족" in scores and "전반적만족" in adjs:
        adj = adjs["전반적만족"]
        if scores["전반적만족"] > 0 and not has_specific:
            t_ge = adj.get("taste_ge",[4,0.3]); t_le = adj.get("taste_le",[2,-0.5])
            if taste_r >= t_ge[0]: scores["전반적만족"] += t_ge[1]
            elif taste_r <= t_le[0]: scores["전반적만족"] += t_le[1]
        tot_le = adj.get("total_le",[2,-1.0]); tot_ge = adj.get("total_ge_default",[5,0.3])
        if   total_r <= tot_le[0]: scores["전반적만족"] += tot_le[1]
        elif total_r >= tot_ge[0] and scores["전반적만족"] == 0: scores["전반적만족"] += tot_ge[1]

    if "양많음" in scores and "양많음" in adjs:
        adj = adjs["양많음"]
        q_ge = adj.get("quantity_ge",[5,0.2]); q_le = adj.get("quantity_le",[2,-0.2])
        if   qty_r >= q_ge[0] and scores["양많음"] == 0: scores["양많음"] += q_ge[1]
        elif qty_r <= q_le[0] and scores["양많음"] == 0: scores["양많음"] += q_le[1]

    return scores


# ── 리뷰 로드 ─────────────────────────────────────────────────

def load_from_db(keyword: str) -> pd.DataFrame:
    terms = get_search_terms(keyword)
    with get_conn() as conn:
        rows = get_reviews_for_profiler(conn, keyword, terms)
    if not rows:
        return pd.DataFrame()
    df = pd.DataFrame(rows)
    print(f"  DB: {len(df)}건 (식당 {df['Restaurant'].nunique()}개)")
    return df


def load_from_csv(keyword: str) -> pd.DataFrame:
    terms = get_search_terms(keyword)
    frames = []
    for f in sorted(glob.glob("*.csv")):
        if any(x in f for x in ["_dictionary","_vectors","_axes","candidate","summary"]):
            continue
        try:
            tmp = pd.read_csv(f, encoding="utf-8-sig")
            if "Review" in tmp.columns and "Restaurant" in tmp.columns:
                frames.append(tmp)
        except Exception:
            pass
    if not frames:
        return pd.DataFrame()
    df = pd.concat(frames, ignore_index=True)
    df = df.drop_duplicates(subset=["Restaurant","Date","Review"], keep="first")
    def has_kw(row):
        text = (str(row.get("Menu","")) + " " + str(row.get("Review",""))).lower()
        return any(t.lower() in text for t in terms)
    filtered = df[df.apply(has_kw, axis=1)].copy()
    print(f"  CSV: {len(filtered)}건 (식당 {filtered['Restaurant'].nunique()}개)")
    return filtered


# ── 프로필 빌드 ───────────────────────────────────────────────

def build_profiles(df: pd.DataFrame, keyword: str,
                   taste_axes: dict, meta_axes: dict, rules: dict):
    all_axes   = {**taste_axes, **meta_axes}
    axes_names = list(all_axes.keys())
    taste_names= list(taste_axes.keys())
    meta_names = list(meta_axes.keys())

    rest_col   = "Restaurant" if "Restaurant" in df.columns else "restaurant"
    rev_col    = "Review"     if "Review"     in df.columns else "content"
    rating_col = "Total"      if "Total"      in df.columns else "rating"

    profiles = {}
    for restaurant in df[rest_col].unique():
        sub      = df[df[rest_col] == restaurant]
        kw_count = len(sub)
        if kw_count < 2:
            continue

        sums     = {a: 0.0 for a in axes_names}
        evidence = {a: []  for a in axes_names}
        sc_cnt   = 0

        for _, row in sub.iterrows():
            sc = score_review(row, taste_axes, meta_axes, rules)
            if any(s != 0 for s in sc.values()): sc_cnt += 1
            for a in axes_names:
                sums[a] += sc[a]
                if sc[a] != 0 and len(evidence[a]) < 3:
                    txt = str(row.get(rev_col,""))[:100]
                    if txt and txt not in evidence[a]: evidence[a].append(txt)

        normalized = (
            {a: round(sums[a]/sc_cnt, 4) for a in axes_names}
            if sc_cnt > 0 else {a: 0.0 for a in axes_names}
        )
        avg_total = (
            round(float(sub[rating_col].mean()), 2)
            if rating_col in sub.columns and sub[rating_col].notna().any() else 0
        )

        profiles[restaurant] = {
            "vector":          [normalized[a] for a in axes_names],
            "normalized":      normalized,
            "taste_vector":    {a: normalized[a] for a in taste_names},
            "meta_vector":     {a: normalized[a] for a in meta_names},
            "evidence":        evidence,
            "total_reviews":   kw_count,
            "keyword_reviews": kw_count,
            "scored_reviews":  sc_cnt,
            "avg_total":       avg_total,
            "avg_taste":       0.0,
        }

    return profiles, axes_names


# ── 저장 ──────────────────────────────────────────────────────

def save_to_db(keyword: str, profiles: dict, all_axes: dict,
               taste_axes: dict, meta_axes: dict, df: pd.DataFrame):

    rest_col = "Restaurant" if "Restaurant" in df.columns else "restaurant"

    axes_config = {
        name: {
            "group":             info.get("group","기타"),
            "positive_keywords": info.get("positive",[]),
            "negative_keywords": info.get("negative",[]),
            "clip_prompt_pos":   info.get("clip_prompt_pos",[]),
            "clip_prompt_neg":   info.get("clip_prompt_neg",[]),
            "is_meta":           name in meta_axes,
        }
        for name, info in all_axes.items()
    }

    with get_conn() as conn:
        upsert_axes_config(conn, keyword, axes_config)
        saved = 0
        for rest_name, profile in profiles.items():
            rest_id = upsert_restaurant(conn, {"name": rest_name, "source":"profiler"})

            # CSV에서 로드한 경우 리뷰도 DB에 저장
            if "Review" in df.columns:
                for _, row in df[df[rest_col] == rest_name].iterrows():
                    insert_review(conn, rest_id, row.to_dict())

            upsert_taste_profile(conn, rest_id, keyword, profile)
            saved += 1

    print(f"  ✅ DB 저장: {saved}개 식당")


def save_json(keyword: str, profiles: dict, axes_names: list,
              all_axes: dict, taste_axes: dict, meta_axes: dict):
    safe = keyword.replace(" ","_")
    groups = defaultdict(list)
    for name, info in all_axes.items():
        groups[info.get("group","기타")].append(name)

    output = {
        "keyword":    keyword,
        "axes":       axes_names,
        "taste_axes": list(taste_axes.keys()),
        "meta_axes":  list(meta_axes.keys()),
        "groups":     dict(groups),
        "axes_config": {
            name: {
                "group":             info.get("group","기타"),
                "positive_keywords": info.get("positive",[]),
                "negative_keywords": info.get("negative",[]),
                "clip_prompt_pos":   info.get("clip_prompt_pos",[]),
                "clip_prompt_neg":   info.get("clip_prompt_neg",[]),
                "is_meta":           name in meta_axes,
            }
            for name, info in all_axes.items()
        },
        "restaurants": {
            name: {
                "vector":          p["vector"],
                "normalized":      p["normalized"],
                "taste_vector":    p["taste_vector"],
                "meta_vector":     p["meta_vector"],
                "evidence":        p["evidence"],
                "total_reviews":   p["total_reviews"],
                "keyword_reviews": p["keyword_reviews"],
                "avg_total":       p["avg_total"],
            }
            for name, p in profiles.items()
        },
    }
    with open(f"{safe}_vectors.json","w",encoding="utf-8") as f:
        json.dump(output, f, ensure_ascii=False, indent=2)
    print(f"  ✅ {safe}_vectors.json")


# ── 진입점 ────────────────────────────────────────────────────

def main():
    keyword  = sys.argv[1] if len(sys.argv) > 1 else input("키워드 입력: ").strip()
    use_csv  = "--csv" in sys.argv
    if not keyword: print("키워드를 입력하세요!"); return

    print(f"\n{'━'*50}")
    print(f"  '{keyword}' Food Profiler  ({'CSV' if use_csv else 'DB'})")
    print(f"{'━'*50}")

    taste_axes, meta_axes = compose_axes(keyword)
    all_axes   = {**taste_axes, **meta_axes}
    print(f"  맛축 {len(taste_axes)}개 | 메타축 {len(meta_axes)}개")

    stand = load_stand()
    rules = stand["rules"] if stand else {"scoring":{"positive_hit":1.0,"negative_hit":-0.5}}

    print(f"\n📂 리뷰 로드...")
    df = load_from_csv(keyword) if use_csv else load_from_db(keyword)
    if df.empty and not use_csv:
        print("  DB에 없음 → CSV 폴백 시도")
        df = load_from_csv(keyword)
    if df.empty:
        print(f"  ❌ '{keyword}' 관련 리뷰 없음")
        print(f"     크롤러를 먼저 실행하거나: python csv_to_json.py")
        return

    print(f"\n📊 프로필 생성...")
    profiles, axes_names = build_profiles(df, keyword, taste_axes, meta_axes, rules)
    print(f"  {len(profiles)}개 식당")

    if not profiles:
        print(f"  ⚠️ 리뷰 2건 미만 식당만 있어 프로필 생성 불가")
        return

    # 상위 5개 미리보기
    for name, p in sorted(profiles.items(), key=lambda x:-x[1]["keyword_reviews"])[:5]:
        print(f"\n  {name}  ({p['keyword_reviews']}건 / 평점 {p['avg_total']})")
        for ax, val in sorted(p["taste_vector"].items(), key=lambda x:-abs(x[1]))[:4]:
            if abs(val) > 0.001:
                bar = ("▲" if val>0 else "▼") * min(int(abs(val)*8), 12)
                print(f"    {ax:12s} {val:+.3f}  {bar}")

    print(f"\n💾 저장...")
    save_to_db(keyword, profiles, all_axes, taste_axes, meta_axes, df)
    save_json(keyword, profiles, axes_names, all_axes, taste_axes, meta_axes)
    print(f"\n완료!  {len(profiles)}개 식당 | {len(axes_names)}개 축")


if __name__ == "__main__":
    main()
