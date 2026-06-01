"""
make_eval_labels_from_vectors.py — pseudo-label 기반 시연용 eval_labels.csv 생성기
====================================================================================
목적:
  - {keyword}_multimodal_vectors.json 또는 {keyword}_vectors.json을 읽어
    cosine 유사도로 식당 순위를 매기고, 상위 2개를 relevance=1, 하위 2개를 0으로
    라벨링하여 evaluation.py가 읽을 수 있는 시연용 CSV를 생성합니다.

⚠️  주의:
  이것은 "사람이 직접 만든 정답지"가 아니라 "모델 벡터 기반 pseudo-label"입니다.
  파이프라인 검증·시연 용도로만 사용하세요. 진짜 성능 평가는 사람이 직접 라벨링한
  relevance 데이터(eval_labels_human.csv 등)로 별도 수행해야 합니다.

실행:
  python make_eval_labels_from_vectors.py --keyword 버거
  python make_eval_labels_from_vectors.py --keyword 버거 --output eval_labels.csv
"""

import argparse
import csv
import json
import math
import os
import sys

# 시연용 preference 세트 — 축이 없으면 자동으로 무시
PREF_SETS: list[dict] = [
    {"번식감": 1, "온도": 1, "촉촉함": 1},
    {"패티퀄리티": 1, "야채신선도": 1},
    {"담백함": 1, "촉촉함": 1},
    {"감칠맛": 1, "소스조화": 1},
]


def _cosine(a: list[float], b: list[float]) -> float:
    if len(a) != len(b):
        return 0.0
    na = math.sqrt(sum(x * x for x in a))
    nb = math.sqrt(sum(x * x for x in b))
    if na == 0 or nb == 0:
        return 0.0
    return sum(x * y for x, y in zip(a, b)) / (na * nb)


def _load_vectors(keyword: str) -> tuple[dict[str, dict], str]:
    """
    fused_vector를 우선 사용, 없으면 normalized/taste_vector 폴백.
    반환: ({restaurant: {axis: score}}, source_path)
    """
    safe = keyword.replace(" ", "_")
    mm_path = f"{safe}_multimodal_vectors.json"
    tv_path = f"{safe}_vectors.json"

    if os.path.exists(mm_path):
        with open(mm_path, "r", encoding="utf-8") as f:
            data = json.load(f)
        out: dict[str, dict] = {}
        for rest, info in (data.get("restaurants") or {}).items():
            if not isinstance(info, dict):
                continue
            vec = info.get("fused_vector") or info.get("text_only_vector") or {}
            if vec:
                out[rest] = {k: float(v) for k, v in vec.items() if not str(k).startswith("_")}
        if out:
            return out, mm_path

    if os.path.exists(tv_path):
        with open(tv_path, "r", encoding="utf-8") as f:
            data = json.load(f)
        out = {}
        for rest, info in (data.get("restaurants") or {}).items():
            if not isinstance(info, dict):
                continue
            vec = info.get("normalized") or info.get("taste_vector") or {}
            if vec:
                out[rest] = {k: float(v) for k, v in vec.items() if not str(k).startswith("_")}
        if out:
            return out, tv_path

    return {}, ""


def _filter_prefs(prefs: dict, available_axes: set[str]) -> tuple[dict, list[str]]:
    """JSON에 없는 축은 제거하고 누락된 축 목록을 반환."""
    kept = {ax: v for ax, v in prefs.items() if ax in available_axes}
    missing = [ax for ax in prefs if ax not in available_axes]
    return kept, missing


def make_labels(keyword: str, output: str) -> int:
    rest_vecs, source = _load_vectors(keyword)
    if not rest_vecs:
        print(f"❌ '{keyword}' 벡터 파일을 찾을 수 없습니다.")
        print(f"   먼저 실행하세요: python Food_profiler.py {keyword} --debug")
        return 1

    available_axes = set()
    for vec in rest_vecs.values():
        available_axes.update(vec.keys())
    all_axes = sorted(available_axes)

    print(f"📂 source: {source}")
    print(f"   식당 {len(rest_vecs)}개 / 축 {len(all_axes)}개")

    rows: list[dict] = []
    used_sets = 0
    for prefs in PREF_SETS:
        kept, missing = _filter_prefs(prefs, available_axes)
        if missing:
            print(f"   ⚠️ 누락 축 무시: {missing} (preference={prefs})")
        if not kept:
            print(f"   ⏭️  사용 가능한 축이 없어 skip: {prefs}")
            continue

        user_vec = [float(kept.get(ax, 0)) for ax in all_axes]
        scored: list[tuple[str, float]] = []
        for rest, vec in rest_vecs.items():
            r = [float(vec.get(ax, 0)) for ax in all_axes]
            scored.append((rest, _cosine(user_vec, r)))
        scored.sort(key=lambda x: -x[1])

        if len(scored) < 4:
            print(f"   ⏭️  식당 수가 4개 미만이라 skip: {len(scored)}개")
            # 그래도 가능한 만큼은 적어주기
            top_n = max(1, min(2, len(scored) // 2))
        else:
            top_n = 2

        bot_n = top_n
        top = [r for r, _ in scored[:top_n]]
        bot = [r for r, _ in scored[-bot_n:]]
        # 상·하위가 겹치면(식당 수가 적을 때) 하위를 비워둠
        bot = [r for r in bot if r not in top]

        # 원래 prefs(누락 포함)을 user_pref_json으로 — 진짜 의도가 보이도록
        pref_json = json.dumps(kept, ensure_ascii=False, sort_keys=True)
        for rest in top:
            rows.append({"keyword": keyword, "user_pref_json": pref_json,
                         "restaurant": rest, "relevance": 1})
        for rest in bot:
            rows.append({"keyword": keyword, "user_pref_json": pref_json,
                         "restaurant": rest, "relevance": 0})
        used_sets += 1

    if not rows:
        print("❌ 생성할 라벨이 없습니다 (preference set이 모두 skip됨).")
        return 2

    with open(output, "w", encoding="utf-8-sig", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=["keyword", "user_pref_json", "restaurant", "relevance"])
        writer.writeheader()
        writer.writerows(rows)

    print(f"✅ {output} 생성: {len(rows)}행 ({used_sets}개 preference set)")
    print("   ⚠️  pseudo-label 기반 시연용입니다. 진짜 평가는 사람이 라벨링해야 합니다.")
    return 0


def main():
    parser = argparse.ArgumentParser(description="pseudo-label 기반 eval_labels.csv 생성기")
    parser.add_argument("--keyword", default="버거", help="대상 키워드 (default: 버거)")
    parser.add_argument("--output", default="eval_labels.csv", help="출력 CSV 경로")
    args = parser.parse_args()
    sys.exit(make_labels(args.keyword, args.output))


if __name__ == "__main__":
    main()
