"""
Food Profiler v6 — PostgreSQL 연동 + 정확도/설명가능성 개선
=============================================================
DB에서 리뷰 조회 → 맛 벡터 생성 → DB + JSON 저장.

v6 개선점:
  - 문장 단위 분할 + 부정어/강조어/반전어 컨텍스트 윈도우 처리
  - "안 매운", "맵지 않다", "바삭하긴 한데 퍽퍽" 같은 표현을 다르게 채점
  - 표본 보정 (리뷰 수 < min_reviews면 sqrt 스케일 다운)
  - tanh 정규화로 [-1, 1] 안정 압축 (메타축은 별도 미정규화)
  - axis별 confidence + evidence sentence 추출
  - --debug : debug_food_profile_<keyword>.json 생성
  - --dry-run : DB/JSON 저장 없이 결과만 출력
  - --restaurant <name> : 특정 식당 1개만 분석
  - --csv : CSV 폴백

실행:
  python Food_profiler.py 버거                     # DB
  python Food_profiler.py 버거 --csv --debug
  python Food_profiler.py 버거 --restaurant 버거스타 --debug
  python Food_profiler.py 버거 --dry-run
"""

import argparse
import glob
import json
import math
import os
import re
import sys
from collections import defaultdict
from typing import Optional

import numpy as np
import pandas as pd

from db import (
    get_conn,
    upsert_restaurant,
    upsert_taste_profile,
    upsert_axes_config,
    insert_review,
    get_reviews_for_profiler,
)

STAND_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "stand")
_stand_cache = None

# 문장 분할 패턴 — 한국어 종결어미 + 일반 구두점
_SENT_SPLIT = re.compile(r"(?<=[.!?。…\n])\s+|(?<=[다요죠음])\s+(?=[가-힣A-Za-z])")
# 반전절 트리거 (절을 둘로 쪼개는 접속어)
_DEFAULT_REVERSALS = ["하지만", "근데", "그런데", "다만", "그래도", "이긴 한데", "긴 한데", "한데", "는데"]


# ── Stand JSON 로드 ───────────────────────────────────────────

def load_stand() -> Optional[dict]:
    global _stand_cache
    if _stand_cache:
        return _stand_cache

    paths = {k: os.path.join(STAND_DIR, f"stand_{k}.json") for k in ("axes", "lexicon", "rules")}
    if not all(os.path.exists(p) for p in paths.values()):
        print(f"  ⚠️ stand/ 파일 누락: {[k for k, p in paths.items() if not os.path.exists(p)]} → 폴백")
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

    axes_def = stand["axes"]
    lexicon = stand["lexicon"]

    def build(name, group, lex_sec, label=None):
        lex = lex_sec.get(name, {})
        return {
            "positive": lex.get("positive", []),
            "negative": lex.get("negative", []),
            "group": group,
            "label": label or name,
            "clip_prompt_pos": lex.get("clip_prompt_pos", []),
            "clip_prompt_neg": lex.get("clip_prompt_neg", []),
        }

    meta = {}
    for name, info in axes_def.get("meta_axes", {}).items():
        if name.startswith("_"):
            continue
        meta[name] = {
            **build(name, info.get("group", "메타"), lexicon.get("common", {}),
                    label=info.get("label")),
            "_weight": info.get("weight", 0.3),
        }

    taste = {}
    for name, info in axes_def.get("taste_axes", {}).items():
        if name.startswith("_"):
            continue
        taste[name] = build(name, info.get("group", "맛"), lexicon.get("shared", {}),
                            label=info.get("label"))

    food_specific = axes_def.get("food_specific_axes", {})
    alias_to_cat = axes_def.get("alias_to_category", {})
    matched = None

    for cat in food_specific:
        if cat in keyword or keyword in cat:
            matched = cat
            break

    if not matched and keyword in alias_to_cat and alias_to_cat[keyword] in food_specific:
        matched = alias_to_cat[keyword]

    if not matched:
        for food, aliases in axes_def.get("keyword_aliases", {}).items():
            if keyword in aliases or food in keyword or keyword in food:
                if food in alias_to_cat and alias_to_cat[food] in food_specific:
                    matched = alias_to_cat[food]
                    break
                for cat in food_specific:
                    if cat in food or food in cat:
                        matched = cat
                        break
            if matched:
                break

    if matched:
        print(f"  📋 '{keyword}' → '{matched}' 카테고리")
        spec_lex = lexicon.get("food_specific", {}).get(matched, {})
        for name, info in food_specific[matched].items():
            lex = spec_lex.get(name, {})
            taste[name] = {
                "positive": lex.get("positive", []),
                "negative": lex.get("negative", []),
                "group": info.get("group", matched + "특화"),
                "label": info.get("label", name),
                "clip_prompt_pos": lex.get("clip_prompt_pos", []),
                "clip_prompt_neg": lex.get("clip_prompt_neg", []),
            }
    else:
        print(f"  📋 '{keyword}' → 공용 축 전체 사용")

    return taste, meta


def _fallback_axes():
    taste = {
        "매운맛": {"positive": ["매운", "매콤"], "negative": ["안 매", "순한"],
                   "group": "맛", "clip_prompt_pos": [], "clip_prompt_neg": []},
        "바삭함": {"positive": ["바삭", "겉바"], "negative": ["눅눅"],
                   "group": "식감", "clip_prompt_pos": [], "clip_prompt_neg": []},
    }
    meta = {
        "전반적만족": {"positive": ["맛있", "최고"], "negative": ["맛없", "별로"],
                       "group": "메타", "_weight": 0.3,
                       "clip_prompt_pos": [], "clip_prompt_neg": []},
    }
    return taste, meta


# ── 문장 분할 + 절 분리 ───────────────────────────────────────

def split_clauses(text: str, reversals: list[str]) -> list[tuple[str, int]]:
    """
    문장 → 반전절로 쪼갬. 반전절 뒤 부분은 polarity_sign=-1로 마킹.
    반환: [(절_텍스트, polarity_sign), ...]
    """
    if not text:
        return []
    sentences = _SENT_SPLIT.split(text.strip())
    clauses: list[tuple[str, int]] = []

    for sent in sentences:
        sent = sent.strip()
        if not sent:
            continue

        positions = []
        for r in reversals:
            idx = 0
            while True:
                p = sent.find(r, idx)
                if p < 0:
                    break
                positions.append(p)
                idx = p + len(r)
        positions.sort()

        if not positions:
            clauses.append((sent, 1))
            continue

        prev = 0
        sign = 1
        for p in positions:
            chunk = sent[prev:p].strip()
            if chunk:
                clauses.append((chunk, sign))
            prev = p
            sign = -sign
        tail = sent[prev:].strip()
        if tail:
            clauses.append((tail, sign))

    return clauses


_PREFIX_NEGATIONS = {"안", "못", "덜", "별로", "그닥"}  # 어두에 단독으로 오는 부정


def _has_negation(clause: str, kw: str, kw_idx: int, negations: list[str]) -> bool:
    """
    한국어 부정 검출:
      1) keyword 뒤 8글자에 '-지 않/-지 못/-지 마' 패턴
      2) keyword 직전 어절이 _PREFIX_NEGATIONS 단독 어절일 때만 적용
         ("안 맛있", "별로 맛있" → ✓ / "않고", "않은" → ✗ - 다른 어간 부정 어미)
      3) keyword가 negation을 이미 포함하면 1)만 확인
    """
    after = clause[kw_idx + len(kw): kw_idx + len(kw) + 8]
    if re.search(r"^[가-힣]?\s*지\s*(않|못|마)", after):
        return True

    if any(n in kw for n in negations):
        return False

    before = clause[:kw_idx].rstrip()
    if not before:
        return False
    last_space = before.rfind(" ")
    prev_word = before[last_space + 1:] if last_space >= 0 else before
    if prev_word in _PREFIX_NEGATIONS:
        return True
    return False


def _intensifier_mult(window: str, intensifiers: dict) -> float:
    mult = 1.0
    for k, m in intensifiers.items():
        if k in window:
            mult *= float(m)
            break
    return mult


# ── 리뷰 스코어링 (절 단위 + 부정/강조/반전) ────────────────

def score_review(row: dict, taste_axes: dict, meta_axes: dict, rules: dict,
                 collect_evidence: bool = False) -> dict:
    """
    반환: {axis: score, ...}
    collect_evidence=True면 반환에 "_evidence" 키로 {axis: [(score, sentence), ...]} 추가.
    """
    text = str(row.get("Review", "") or row.get("content", "") or "")
    total_r = float(row.get("Total", 0) or row.get("rating", 0) or 0)
    taste_r = float(row.get("Taste", 0) or 0)
    qty_r = float(row.get("Quantity", 0) or 0)

    scoring = rules.get("scoring", {})
    POS_HIT = float(scoring.get("positive_hit", 1.0))
    NEG_HIT = float(scoring.get("negative_hit", -0.5))
    intens = rules.get("intensifiers", {})
    negs = rules.get("negations", [])
    revs = rules.get("reversals", _DEFAULT_REVERSALS)
    adjs = rules.get("rating_adjustments", {})

    all_axes = {**taste_axes, **meta_axes}
    scores = {a: 0.0 for a in all_axes}
    evidence: dict[str, list] = defaultdict(list)
    has_specific = False

    # 절 분리: 절 단위로 부정어 윈도우 경계 차단. polarity는 곱하지 않음.
    # 반전절 뒤쪽은 0.7 weight (메인 메시지가 첫 절이라는 가정).
    clauses = split_clauses(text, revs) or [(text, 1)]

    for ci, (clause, polarity) in enumerate(clauses):
        if not clause:
            continue
        clause_weight = 1.0 if ci == 0 else 0.7
        for ax, info in all_axes.items():
            for kw in info.get("positive", []):
                if not kw or kw not in clause:
                    continue
                pos_idx = clause.find(kw)
                int_window = clause[max(0, pos_idx - 6): pos_idx + len(kw)]
                neg = _has_negation(clause, kw, pos_idx, negs)
                mult = _intensifier_mult(int_window, intens)
                base = (NEG_HIT if neg else POS_HIT) * mult * clause_weight
                scores[ax] += base
                if collect_evidence and abs(base) > 0.01:
                    evidence[ax].append((round(base, 3), clause[:120]))
                if ax not in meta_axes and abs(base) > 0.01:
                    has_specific = True

            for kw in info.get("negative", []):
                if not kw or kw not in clause:
                    continue
                pos_idx = clause.find(kw)
                int_window = clause[max(0, pos_idx - 6): pos_idx + len(kw)]
                neg = _has_negation(clause, kw, pos_idx, negs)
                mult = _intensifier_mult(int_window, intens)
                # negative 키워드에 부정어 붙으면 부호 반전 ("느끼하지 않다" → +)
                base = (POS_HIT if neg else NEG_HIT) * mult * clause_weight
                scores[ax] += base
                if collect_evidence and abs(base) > 0.01:
                    evidence[ax].append((round(base, 3), clause[:120]))
                if ax not in meta_axes and abs(base) > 0.01:
                    has_specific = True

    # 메타축 평점 보정 (전반적만족, 양많음만)
    if "전반적만족" in scores and "전반적만족" in adjs:
        adj = adjs["전반적만족"]
        if scores["전반적만족"] > 0 and not has_specific:
            t_ge = adj.get("taste_ge", [4, 0.3])
            t_le = adj.get("taste_le", [2, -0.5])
            if taste_r >= t_ge[0]:
                scores["전반적만족"] += t_ge[1]
            elif taste_r <= t_le[0]:
                scores["전반적만족"] += t_le[1]

        tot_le = adj.get("total_le", [2, -1.0])
        tot_ge = adj.get("total_ge_default", [5, 0.3])
        if total_r > 0:  # ★ 평점이 있을 때만 보정
            if total_r <= tot_le[0]:
                scores["전반적만족"] += tot_le[1]
            elif total_r >= tot_ge[0] and scores["전반적만족"] == 0:
                scores["전반적만족"] += tot_ge[1]

    if "양많음" in scores and "양많음" in adjs:
        adj = adjs["양많음"]
        q_ge = adj.get("quantity_ge", [5, 0.2])
        q_le = adj.get("quantity_le", [2, -0.2])
        if qty_r >= q_ge[0] and scores["양많음"] == 0:
            scores["양많음"] += q_ge[1]
        elif qty_r <= q_le[0] and scores["양많음"] == 0:
            scores["양많음"] += q_le[1]

    if collect_evidence:
        scores["_evidence"] = dict(evidence)
    return scores


# ── 리뷰 로드 ─────────────────────────────────────────────────

def load_from_db(keyword: str, debug_dump: dict | None = None) -> pd.DataFrame:
    """
    DB에서 keyword 관련 리뷰 로드.

    source boundary 기반 후보 산정 결과를 항상(debug 모드 아니어도) 콘솔에 출력한다.
    debug_dump dict가 주어지면 keyword_filter 디버그 dict를 그대로 적재.
    """
    terms = get_search_terms(keyword)
    with get_conn() as conn:
        rows, kw_debug = get_reviews_for_profiler(conn, keyword, terms, return_debug=True)

    # ── source boundary 로그 (항상 출력) ──
    src_prefix = kw_debug.get("source_by_prefix_restaurants", 0)
    src_crawl = kw_debug.get("crawl_keyword_restaurants", 0)
    text_alias = kw_debug.get("text_alias_matched_restaurants", 0)
    final_rest = kw_debug.get("final_restaurants", 0)
    excluded_no_src = kw_debug.get("excluded_no_source_prefix", 0)
    print(f"  🔎 source boundary "
          f"| /reviews/{keyword}/ prefix 식당 {src_prefix} "
          f"+ crawl_keyword 식당 {src_crawl} "
          f"| text/alias 매치 {text_alias}곳 "
          f"→ 최종 {final_rest}곳 "
          f"| source 없어 제외 {excluded_no_src}곳")
    if kw_debug.get("source_set_empty_fallback"):
        print(f"  ⚠️ source_set 비어 있음 (prefix·crawl_keyword 신호 0건) "
              f"→ text/alias fallback 모드 (옛 데이터 호환). "
              f"새 크롤링이나 image_url 마이그레이션을 권장.")
    if final_rest > 0:
        density_th = kw_debug.get("density_threshold")
        prot_prefix = kw_debug.get("protected_by_prefix", 0)
        prot_name = kw_debug.get("protected_by_name", 0)
        prot_crawl = kw_debug.get("protected_by_crawl_keyword", 0)
        excl_density = kw_debug.get("excluded_by_density", 0)
        excl_cat = kw_debug.get("excluded_by_category", 0)
        print(f"  └─ density<{density_th} 제외 {excl_density} "
              f"| 카테고리 제외 {excl_cat} "
              f"| 보호: prefix {prot_prefix} / 이름 {prot_name} / crawl {prot_crawl}")

    if debug_dump is not None:
        debug_dump["keyword_filter"] = kw_debug

    if not rows:
        return pd.DataFrame()
    df = pd.DataFrame(rows)
    print(f"  DB: {len(df)}건 (식당 {df['Restaurant'].nunique()}개)")
    return df


def load_from_csv(keyword: str) -> pd.DataFrame:
    """
    CSV 로더 — keyword 경계 강제.
    1) 파일명에 keyword가 포함된 CSV만 우선 사용 (예: naver_reviews_김밥.csv, 김밥.csv).
       매치되는 CSV가 하나도 없으면 fallback으로 모든 CSV를 스캔하되,
       각 행의 CrawlKeyword 컬럼이 현재 keyword/aliases와 일치하는 행만 남긴다.
    2) CrawlKeyword 컬럼이 없는 옛 CSV는 review-content alias 매칭으로 통과 (호환).
    """
    terms = get_search_terms(keyword)
    all_csvs = [f for f in sorted(glob.glob("*.csv"))
                if not any(x in f for x in ["_dictionary", "_vectors", "_axes",
                                            "candidate", "summary", "eval_labels"])]
    # 파일명에 keyword 또는 alias가 들어간 CSV만 우선
    scoped = [f for f in all_csvs
              if any(t in os.path.basename(f) for t in terms)]
    target_csvs = scoped if scoped else all_csvs

    frames = []
    for f in target_csvs:
        try:
            tmp = pd.read_csv(f, encoding="utf-8-sig")
            if "Review" in tmp.columns and "Restaurant" in tmp.columns:
                tmp["__source_csv__"] = os.path.basename(f)
                frames.append(tmp)
        except Exception:
            pass

    if not frames:
        return pd.DataFrame()

    df = pd.concat(frames, ignore_index=True)
    df = df.drop_duplicates(subset=["Restaurant", "Date", "Review"], keep="first")

    # CrawlKeyword 컬럼이 있으면 그 값으로 1차 필터 — 다른 keyword 행은 즉시 제거.
    if "CrawlKeyword" in df.columns:
        df["CrawlKeyword"] = df["CrawlKeyword"].fillna("").astype(str)
        has_crawl_meta = df["CrawlKeyword"].str.strip() != ""
        crawl_match = df["CrawlKeyword"].apply(lambda v: any(t == v.strip() for t in terms))
        # CrawlKeyword가 있는 행: 일치해야 통과. 비어있는 행은 호환 대상.
        df = df[(~has_crawl_meta) | crawl_match].copy()

    def has_kw(row):
        text = (str(row.get("Menu", "")) + " " + str(row.get("Review", ""))).lower()
        return any(t.lower() in text for t in terms)

    filtered = df[df.apply(has_kw, axis=1)].copy()
    src_names = sorted(filtered["__source_csv__"].unique().tolist()) if "__source_csv__" in filtered.columns else []
    print(f"  CSV: {len(filtered)}건 (식당 {filtered['Restaurant'].nunique()}개) | sources={src_names}")
    return filtered


# ── 프로필 빌드 (표본 보정 + tanh 정규화) ───────────────────

def build_profiles(df: pd.DataFrame, keyword: str,
                   taste_axes: dict, meta_axes: dict, rules: dict,
                   collect_evidence: bool = True):
    all_axes = {**taste_axes, **meta_axes}
    axes_names = list(all_axes.keys())
    taste_names = list(taste_axes.keys())
    meta_names = list(meta_axes.keys())

    rest_col = "Restaurant" if "Restaurant" in df.columns else "restaurant"
    rev_col = "Review" if "Review" in df.columns else "content"
    rating_col = "Total" if "Total" in df.columns else "rating"

    low_rev = rules.get("low_review_penalty", {"min_reviews": 3})
    min_reviews = int(low_rev.get("min_reviews", 3))

    profiles = {}
    for restaurant in df[rest_col].unique():
        sub = df[df[rest_col] == restaurant]
        kw_count = len(sub)
        if kw_count < 2:
            continue

        sums = {a: 0.0 for a in axes_names}
        sq_sums = {a: 0.0 for a in axes_names}
        hit_counts = {a: 0 for a in axes_names}
        evidence_pool: dict[str, list] = defaultdict(list)
        sc_cnt = 0

        for _, row in sub.iterrows():
            sc = score_review(row, taste_axes, meta_axes, rules,
                              collect_evidence=collect_evidence)
            ev = sc.pop("_evidence", {}) if collect_evidence else {}

            if any(abs(s) > 0.001 for s in sc.values()):
                sc_cnt += 1
            for a in axes_names:
                sums[a] += sc[a]
                sq_sums[a] += sc[a] * sc[a]
                if abs(sc[a]) > 0.001:
                    hit_counts[a] += 1
                if a in ev:
                    evidence_pool[a].extend(ev[a])

        # 평균 점수
        denom = max(sc_cnt, 1)
        raw = {a: sums[a] / denom for a in axes_names}

        # tanh 압축으로 [-1, 1]
        normalized = {a: round(math.tanh(raw[a]), 4) for a in axes_names}

        # 표본 보정: 리뷰 수가 min_reviews보다 적으면 sqrt 스케일
        if kw_count < min_reviews:
            scale = math.sqrt(kw_count / min_reviews)
            for a in axes_names:
                normalized[a] = round(normalized[a] * scale, 4)

        # 축별 confidence: hit_count / kw_count, 분산이 낮을수록 높음
        confidence = {}
        for a in axes_names:
            hit_rate = hit_counts[a] / max(kw_count, 1)
            mean = sums[a] / denom
            var = max(sq_sums[a] / denom - mean * mean, 0.0)
            consistency = 1.0 / (1.0 + var)
            confidence[a] = round(min(hit_rate * consistency, 1.0), 3)

        # 축별 evidence: 절댓값 큰 순 top 3, 중복 제거
        evidence = {}
        for a in axes_names:
            seen = set()
            ranked = sorted(evidence_pool.get(a, []), key=lambda x: -abs(x[0]))
            ev_list = []
            for sc_val, sent in ranked:
                key = sent[:60]
                if key in seen:
                    continue
                seen.add(key)
                ev_list.append(sent)
                if len(ev_list) >= 3:
                    break
            evidence[a] = ev_list

        avg_total = (
            round(float(sub[rating_col].mean()), 2)
            if rating_col in sub.columns and sub[rating_col].notna().any()
            else 0
        )

        profiles[restaurant] = {
            "vector": [normalized[a] for a in axes_names],
            "normalized": normalized,
            "taste_vector": {a: normalized[a] for a in taste_names},
            "meta_vector": {a: normalized[a] for a in meta_names},
            "confidence": confidence,
            "evidence": evidence,
            "raw_sums": {a: round(sums[a], 3) for a in axes_names},
            "hit_counts": hit_counts,
            "total_reviews": kw_count,
            "keyword_reviews": kw_count,
            "scored_reviews": sc_cnt,
            "avg_total": avg_total,
            "avg_taste": 0.0,
        }

    return profiles, axes_names


# ── 저장 ──────────────────────────────────────────────────────

def save_to_db(keyword: str, profiles: dict, all_axes: dict,
               taste_axes: dict, meta_axes: dict, df: pd.DataFrame,
               source_is_csv: bool = False):
    rest_col = "Restaurant" if "Restaurant" in df.columns else "restaurant"
    axes_config = {
        name: {
            "group": info.get("group", "기타"),
            "label": info.get("label", name),
            "positive_keywords": info.get("positive", []),
            "negative_keywords": info.get("negative", []),
            "clip_prompt_pos": info.get("clip_prompt_pos", []),
            "clip_prompt_neg": info.get("clip_prompt_neg", []),
            "is_meta": name in meta_axes,
        }
        for name, info in all_axes.items()
    }

    with get_conn() as conn:
        upsert_axes_config(conn, keyword, axes_config)
        saved = 0
        for rest_name, profile in profiles.items():
            rest_id = upsert_restaurant(conn, {"name": rest_name, "source": "profiler"})
            if source_is_csv:
                for _, row in df[df[rest_col] == rest_name].iterrows():
                    insert_review(conn, rest_id, row.to_dict())
            upsert_taste_profile(conn, rest_id, keyword, profile)
            saved += 1
    print(f"  ✅ DB 저장: {saved}개 식당")


def save_json(keyword: str, profiles: dict, axes_names: list,
              all_axes: dict, taste_axes: dict, meta_axes: dict):
    safe = keyword.replace(" ", "_")
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
                "vector": p["vector"],
                "normalized": p["normalized"],
                "taste_vector": p["taste_vector"],
                "meta_vector": p["meta_vector"],
                "confidence": p["confidence"],
                "evidence": p["evidence"],
                "total_reviews": p["total_reviews"],
                "keyword_reviews": p["keyword_reviews"],
                "scored_reviews": p["scored_reviews"],
                "avg_total": p["avg_total"],
            }
            for name, p in profiles.items()
        },
    }

    with open(f"{safe}_vectors.json", "w", encoding="utf-8") as f:
        json.dump(output, f, ensure_ascii=False, indent=2)
    print(f"  ✅ {safe}_vectors.json")


def _included_entry(name: str, profile: dict, samples: list[dict]) -> dict:
    sample = next((s for s in samples if s.get("name") == name), None)
    if sample is None:
        return {"name": name, "keyword_reviews": profile["keyword_reviews"],
                "density_ratio": None, "included_reason": "density_pass"}
    # 우선순위: prefix > crawl_keyword > name > density
    if sample.get("protected_by_prefix"):
        reason = "protected_by_prefix"
    elif sample.get("protected_by_crawl"):
        reason = "protected_by_crawl_keyword"
    elif sample.get("protected_by_name"):
        reason = "protected_by_name"
    else:
        reason = "density_pass"
    return {
        "name": name,
        "keyword_reviews": profile["keyword_reviews"],
        "density_ratio": round(sample.get("density", 0), 4),
        "included_reason": reason,
    }


def save_debug(keyword: str, profiles: dict, taste_axes: dict, meta_axes: dict,
               keyword_filter: dict | None = None):
    safe = keyword.replace(" ", "_")
    included_restaurants = sorted(profiles.keys())
    included_set = set(included_restaurants)

    excluded_restaurants: list[dict] = []
    samples = (keyword_filter or {}).get("samples", []) if keyword_filter else []
    for s in samples:
        if not s.get("kept") and s.get("name") not in included_set:
            excluded_restaurants.append({
                "name": s.get("name"),
                "category": s.get("category"),
                "total_reviews": s.get("total_reviews"),
                "hit_reviews": s.get("hit_reviews"),
                "density_ratio": s.get("density"),
                "excluded_reason": s.get("excluded_reason"),
            })

    debug = {
        "keyword": keyword,
        "search_terms": (keyword_filter or {}).get("search_terms", [keyword]),
        "density_threshold": (keyword_filter or {}).get("density_threshold"),
        # source boundary 진단 필드
        "source_by_prefix_restaurants":   (keyword_filter or {}).get("source_by_prefix_restaurants", 0),
        "crawl_keyword_restaurants":      (keyword_filter or {}).get("crawl_keyword_restaurants", 0),
        "text_alias_matched_restaurants": (keyword_filter or {}).get("text_alias_matched_restaurants", 0),
        "excluded_no_source_prefix":      (keyword_filter or {}).get("excluded_no_source_prefix", 0),
        "source_set_empty_fallback":      (keyword_filter or {}).get("source_set_empty_fallback", False),
        "candidate_restaurants_count": (keyword_filter or {}).get("candidate_restaurants"),
        "included_restaurants_count": len(included_restaurants),
        "excluded_restaurants_count": ((keyword_filter or {}).get("excluded_by_density", 0)
                                       + (keyword_filter or {}).get("excluded_by_category", 0)),
        "protected_by_prefix_count":      (keyword_filter or {}).get("protected_by_prefix", 0),
        "protected_by_name_count": (keyword_filter or {}).get("protected_by_name", 0),
        "protected_by_crawl_keyword_count": (keyword_filter or {}).get("protected_by_crawl_keyword", 0),
        "included_restaurants": [
            _included_entry(name, p, samples)
            for name, p in profiles.items()
        ],
        "excluded_restaurants": excluded_restaurants,
        "taste_axes": list(taste_axes.keys()),
        "meta_axes": list(meta_axes.keys()),
        "restaurants": {
            name: {
                "keyword_reviews": p["keyword_reviews"],
                "scored_reviews": p["scored_reviews"],
                "avg_total": p["avg_total"],
                "raw_sums": p["raw_sums"],
                "hit_counts": p["hit_counts"],
                "normalized": p["normalized"],
                "confidence": p["confidence"],
                "top_taste_axes": sorted(
                    [(a, v) for a, v in p["taste_vector"].items() if abs(v) > 0.001],
                    key=lambda x: -abs(x[1])
                )[:5],
                "evidence": p["evidence"],
            }
            for name, p in profiles.items()
        },
    }
    path = f"debug_food_profile_{safe}.json"
    with open(path, "w", encoding="utf-8") as f:
        json.dump(debug, f, ensure_ascii=False, indent=2)
    print(f"  🐛 {path}")


# ── 진입점 ────────────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser(description="Food Profiler v6")
    parser.add_argument("keyword", nargs="?", default=None)
    parser.add_argument("--csv", action="store_true", help="CSV에서 리뷰 로드")
    parser.add_argument("--debug", action="store_true", help="디버그 JSON 출력")
    parser.add_argument("--dry-run", action="store_true", help="DB/JSON 저장 생략")
    parser.add_argument("--restaurant", default=None, help="특정 식당만 분석")
    args = parser.parse_args()

    keyword = args.keyword or input("키워드 입력: ").strip()
    if not keyword:
        print("키워드를 입력하세요!")
        return

    print(f"\n{'━' * 50}")
    print(f"  '{keyword}' Food Profiler v6  ({'CSV' if args.csv else 'DB'})"
          f"{'  [DRY-RUN]' if args.dry_run else ''}"
          f"{'  [DEBUG]' if args.debug else ''}")
    print(f"{'━' * 50}")

    taste_axes, meta_axes = compose_axes(keyword)
    all_axes = {**taste_axes, **meta_axes}
    print(f"  맛축 {len(taste_axes)}개 | 메타축 {len(meta_axes)}개")

    stand = load_stand()
    rules = stand["rules"] if stand else {"scoring": {"positive_hit": 1.0, "negative_hit": -0.5}}

    print(f"\n📂 리뷰 로드...")
    debug_dump: dict | None = {} if args.debug else None
    df = load_from_csv(keyword) if args.csv else load_from_db(keyword, debug_dump=debug_dump)
    if df.empty and not args.csv:
        print("  DB에 없음 → CSV 폴백 시도")
        df = load_from_csv(keyword)
    if df.empty:
        print(f"  ❌ '{keyword}' 관련 리뷰 없음")
        return

    if args.restaurant:
        rest_col = "Restaurant" if "Restaurant" in df.columns else "restaurant"
        df = df[df[rest_col] == args.restaurant]
        if df.empty:
            print(f"  ❌ 식당 '{args.restaurant}' 리뷰 없음")
            return
        print(f"  🎯 식당 필터: '{args.restaurant}' → {len(df)}건")

    print(f"\n📊 프로필 생성...")
    profiles, axes_names = build_profiles(df, keyword, taste_axes, meta_axes, rules,
                                          collect_evidence=True)
    print(f"  {len(profiles)}개 식당")
    if not profiles:
        print(f"  ⚠️ 리뷰 2건 미만 식당만 있어 프로필 생성 불가")
        return

    # 미리보기
    for name, p in sorted(profiles.items(), key=lambda x: -x[1]["keyword_reviews"])[:5]:
        print(f"\n  {name}  ({p['keyword_reviews']}건 / 평점 {p['avg_total']})")
        for ax, val in sorted(p["taste_vector"].items(), key=lambda x: -abs(x[1]))[:4]:
            if abs(val) > 0.001:
                bar = ("▲" if val > 0 else "▼") * min(int(abs(val) * 8), 12)
                conf = p["confidence"].get(ax, 0)
                print(f"    {ax:12s} {val:+.3f}  conf={conf:.2f}  {bar}")

    if args.debug:
        kw_filter = (debug_dump or {}).get("keyword_filter") if debug_dump else None
        save_debug(keyword, profiles, taste_axes, meta_axes, keyword_filter=kw_filter)

    if args.dry_run:
        print("\n  [DRY-RUN] DB/JSON 저장 생략")
        return

    print(f"\n💾 저장...")
    save_to_db(keyword, profiles, all_axes, taste_axes, meta_axes, df, source_is_csv=args.csv)
    save_json(keyword, profiles, axes_names, all_axes, taste_axes, meta_axes)
    print(f"\n완료!  {len(profiles)}개 식당 | {len(axes_names)}개 축")


if __name__ == "__main__":
    main()
