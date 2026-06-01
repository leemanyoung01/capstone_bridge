"""
scripts/make_eval_labels.py — 자동 라벨 생성기
==================================================

네이버 리뷰의 voted_keywords (사용자가 직접 클릭한 한정 vocab) 를 활용해
각 식당 × 축의 ground truth relevance score를 자동 생성한다.

데이터 풍부도: voted_keywords 보유율 85~97% (rating은 0이라 못 씀)

흐름:
  1. reviews 테이블에서 voted_keywords 집계 (식당별)
  2. VK_TO_AXIS 매핑으로 voted_keywords → 우리 시스템의 축으로 변환
  3. 키워드 내에서 정규화 (max=1.0) 후 JSON 저장

출력:
  eval/labels_<keyword>.json
  {
    "restaurant": {
      "전반적만족": 0.94,
      "양많음":     0.61,
      "재료신선도": 0.40,
      ...
    },
    ...
  }

사용:
  python scripts/make_eval_labels.py 짬뽕
  python scripts/make_eval_labels.py 짬뽕 피자 버거 치킨
  python scripts/make_eval_labels.py --all
"""
from __future__ import annotations

import argparse
import json
import os
import sys
from collections import Counter, defaultdict
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from db import get_conn   # noqa: E402


# ── voted_keywords → 축 매핑 사전 ─────────────────────────────
# 네이버가 제공하는 한정된 voted_keywords vocab (51개)를 우리 축으로 변환.
# 매장/서비스 관련 (친절/주차/뷰 등)은 None — 추천 평가에 무관하니 스킵.
VK_TO_AXIS: dict[str, list[tuple[str, float]] | None] = {
    # 명확히 매핑되는 것들
    "음식이 맛있어요":     [("전반적만족", 1.0)],
    "재료가 신선해요":     [("재료신선도", 1.0), ("야채신선도", 1.0), ("신선도", 1.0)],
    "양이 많아요":         [("양많음", 1.0)],
    "가성비가 좋아요":     [("가성비", 1.0)],
    "혼밥하기 좋아요":     [("혼밥하기좋은", 1.0)],
    "건강한 맛이에요":     [("담백함", 0.7)],
    "현지 맛에 가까워요":  [("전반적만족", 0.3)],
    "직접 잘 구워줘요":    [("굽기상태", 1.0), ("굽기정도", 1.0)],
    "고기 질이 좋아요":    [("육질", 1.0), ("육질염지", 1.0), ("패티퀄리티", 1.0)],
    "잡내가 적어요":       [("잡내제거", 1.0)],
    "기본 안주가 좋아요":  [("반찬쌈", 0.8), ("사이드구성", 0.8)],
    "반찬이 잘 나와요":    [("반찬쌈", 1.0), ("사이드구성", 1.0)],
    "디저트가 맛있어요":   [("디저트", 1.0)],
    "음료가 맛있어요":     [("온도", 0.3)],   # 음료 카테고리에만 약하게
    "향신료가 강하지 않아요": [("담백함", 0.5)],
    "비싼 만큼 가치있어요": [("가성비", 0.4)],
    "메뉴 구성이 알차요":   [("사이드구성", 0.4), ("세트구성", 0.4), ("토핑풍부", 0.3)],
    "특별한 메뉴가 있어요": [("토핑풍부", 0.3), ("세트구성", 0.3)],

    # 매장/서비스 — 추천 평가 무관
    "친절해요":              None,
    "인테리어가 멋져요":     None,
    "매장이 청결해요":       None,
    "매장이 넓어요":         None,
    "단체모임 하기 좋아요":  None,
    "특별한 날 가기 좋아요": None,
    "아늑해요":              None,
    "대화하기 좋아요":       None,
    "뷰가 좋아요":           None,
    "야외공간이 멋져요":     None,
    "차분한 분위기예요":     None,
    "음악이 좋아요":         None,
    "음식이 빨리 나와요":    None,
    "화장실이 깨끗해요":     None,
    "주차하기 편해요":       None,
    "아이와 가기 좋아요":    None,
    "컨셉이 독특해요":       None,
    "사진이 잘 나와요":      None,
    "좌석이 편해요":         None,
    "술이 다양해요":         None,
    "포장이 깔끔해요":       None,
    "오래 머무르기 좋아요":  None,
}


def aggregate_vk_per_restaurant(conn, keyword: str) -> dict[str, Counter]:
    """식당별 voted_keywords 빈도 집계."""
    with conn.cursor() as cur:
        cur.execute("""
            SELECT r.name, rev.voted_keywords
              FROM reviews rev
              JOIN restaurants r ON r.restaurant_id = rev.restaurant_id
             WHERE rev.crawl_keyword = %s
               AND rev.voted_keywords IS NOT NULL
               AND rev.voted_keywords != ''
        """, (keyword,))
        rows = cur.fetchall()

    per_rest: dict[str, Counter] = defaultdict(Counter)
    for row in rows:
        name = row["name"]
        for tok in (row["voted_keywords"] or "").split(","):
            tok = tok.strip()
            if tok:
                per_rest[name][tok] += 1
    return per_rest


def vk_counts_to_axis_scores(vk_counts: Counter) -> dict[str, float]:
    """식당의 voted_keywords 카운트 → 축별 raw score."""
    axis_raw: dict[str, float] = defaultdict(float)
    for tok, n in vk_counts.items():
        mapping = VK_TO_AXIS.get(tok)
        if not mapping:
            continue
        for axis_name, weight in mapping:
            axis_raw[axis_name] += n * weight
    return dict(axis_raw)


def normalize_per_keyword(rest_to_scores: dict[str, dict[str, float]]) -> dict[str, dict[str, float]]:
    """축별 max=1.0으로 정규화 (식당끼리 상대 비교 가능하게)."""
    # 축별 최대값 찾기
    axis_max: dict[str, float] = defaultdict(float)
    for scores in rest_to_scores.values():
        for ax, v in scores.items():
            if v > axis_max[ax]:
                axis_max[ax] = v

    out: dict[str, dict[str, float]] = {}
    for rest, scores in rest_to_scores.items():
        out[rest] = {
            ax: round(v / axis_max[ax], 4)
            for ax, v in scores.items()
            if axis_max[ax] > 0
        }
    return out


def make_labels_for_keyword(keyword: str, out_dir: Path) -> dict:
    print(f"\n=== '{keyword}' 자동 라벨 생성 ===")
    with get_conn() as conn:
        per_rest = aggregate_vk_per_restaurant(conn, keyword)

    if not per_rest:
        print(f"  ⚠️ '{keyword}' voted_keywords 없음 — skip")
        return {}

    rest_raw_scores = {r: vk_counts_to_axis_scores(c) for r, c in per_rest.items()}
    labels = normalize_per_keyword(rest_raw_scores)

    # 빈 식당 제거
    labels = {r: s for r, s in labels.items() if s}

    # 통계
    total_vk = sum(sum(c.values()) for c in per_rest.values())
    n_axes_used = len({a for s in labels.values() for a in s.keys()})
    print(f"  ✓ 식당 {len(labels)}개, 총 voted_keywords {total_vk}건")
    print(f"  ✓ 활용된 축 {n_axes_used}개")

    # top 3 식당 미리보기
    print(f"  ─ 미리보기 (top 3 식당, voted_keywords 많은 순) ─")
    top_rests = sorted(per_rest.items(), key=lambda x: -sum(x[1].values()))[:3]
    for rest, _ in top_rests:
        if rest not in labels:
            continue
        sorted_ax = sorted(labels[rest].items(), key=lambda x: -x[1])[:5]
        print(f"    {rest}")
        for ax, sc in sorted_ax:
            print(f"      {ax:14s} {sc:.3f}")

    # 저장
    out_dir.mkdir(parents=True, exist_ok=True)
    out_file = out_dir / f"labels_{keyword}.json"
    with open(out_file, "w", encoding="utf-8") as f:
        json.dump({
            "keyword": keyword,
            "label_source": "naver_voted_keywords",
            "n_restaurants": len(labels),
            "n_axes_used": n_axes_used,
            "labels": labels,
        }, f, ensure_ascii=False, indent=2)
    print(f"  💾 저장: {out_file.relative_to(ROOT)}")
    return labels


def main():
    parser = argparse.ArgumentParser(description="네이버 voted_keywords 기반 자동 라벨 생성")
    parser.add_argument("keywords", nargs="*", help="대상 keyword들 (예: 짬뽕 피자)")
    parser.add_argument("--all", action="store_true", help="DB의 모든 keyword 처리")
    parser.add_argument("--out", default="eval", help="출력 디렉토리 (기본 ./eval)")
    args = parser.parse_args()

    out_dir = ROOT / args.out

    if args.all:
        with get_conn() as conn, conn.cursor() as cur:
            cur.execute(
                "SELECT DISTINCT crawl_keyword FROM reviews "
                "WHERE crawl_keyword IS NOT NULL ORDER BY crawl_keyword"
            )
            kws = [r["crawl_keyword"] for r in cur.fetchall()]
        print(f"전체 keyword {len(kws)}개 처리: {kws}")
    elif args.keywords:
        kws = args.keywords
    else:
        sys.exit("사용법: python scripts/make_eval_labels.py 짬뽕 피자  (또는 --all)")

    for kw in kws:
        make_labels_for_keyword(kw, out_dir)

    print(f"\n완료. 저장 위치: {out_dir.relative_to(ROOT)}/")


if __name__ == "__main__":
    main()
