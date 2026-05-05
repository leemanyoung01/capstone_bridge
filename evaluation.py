"""
evaluation.py — 추천 품질 평가 (콘솔 + JSON 저장)
====================================================
정답 라벨이 있는 CSV가 있을 때만 의미 있음. 추천 목록 전체에 대한 ranking 평가이며,
개별 식당 카드에 Precision/Recall을 직접 붙이는 용도가 아님.

라벨 종류:
  - human-label  : 사람이 직접 라벨링한 relevance (ex. eval_labels_human.csv)
  - pseudo-label : make_eval_labels_from_vectors.py가 만든 모델 벡터 기반

label_type 자동 감지:
  1) --label-type 인자 우선
  2) 파일명에 'human' 포함 → human-label
  3) 그 외 → pseudo-label

평가 데이터 형식 (UTF-8-SIG CSV):
  keyword,user_pref_json,restaurant,relevance

실행:
  python evaluation.py --labels eval_labels.csv --k 5
  python evaluation.py --labels eval_labels_human.csv --k 5 --label-type human
  python evaluation.py --labels eval_labels.csv --k 5 --output evaluation_results.json
"""

import argparse
import json
import math
import os
import sys
from collections import defaultdict

import pandas as pd


def precision_at_k(predicted: list[str], relevant: set[str], k: int) -> float:
    if k <= 0 or not predicted:
        return 0.0
    top = predicted[:k]
    return sum(1 for p in top if p in relevant) / k


def recall_at_k(predicted: list[str], relevant: set[str], k: int) -> float:
    if not relevant:
        return 0.0
    top = predicted[:k]
    return sum(1 for p in top if p in relevant) / len(relevant)


def f1_at_k(predicted: list[str], relevant: set[str], k: int) -> float:
    p = precision_at_k(predicted, relevant, k)
    r = recall_at_k(predicted, relevant, k)
    if p + r == 0:
        return 0.0
    return 2 * p * r / (p + r)


def ndcg_at_k(predicted: list[str], relevant: set[str], k: int) -> float:
    dcg = 0.0
    for i, name in enumerate(predicted[:k], start=1):
        if name in relevant:
            dcg += 1.0 / math.log2(i + 1)
    idcg = sum(1.0 / math.log2(i + 1) for i in range(1, min(k, len(relevant)) + 1))
    return dcg / idcg if idcg > 0 else 0.0


def ndcg_at_k_graded(predicted: list[str], grade_map: dict[str, int], k: int) -> float:
    """
    graded NDCG. grade_map: {restaurant_name: 0~3}.
    relevance_grade가 있는 human-label에서만 의미 있음.
    """
    if not grade_map:
        return 0.0
    dcg = 0.0
    for i, name in enumerate(predicted[:k], start=1):
        g = int(grade_map.get(name, 0))
        if g > 0:
            dcg += (2 ** g - 1) / math.log2(i + 1)
    ideal_grades = sorted(grade_map.values(), reverse=True)[:k]
    idcg = sum((2 ** g - 1) / math.log2(i + 1) for i, g in enumerate(ideal_grades, start=1) if g > 0)
    return dcg / idcg if idcg > 0 else 0.0


def fetch_predictions(api_base: str, keyword: str, prefs: dict) -> list[dict]:
    import requests
    r = requests.post(
        f"{api_base}/api/recommend",
        json={"_keyword": keyword, "text_preferences": prefs},
        timeout=10,
    )
    r.raise_for_status()
    data = r.json()
    return data.get("results", [])


def _detect_label_type(labels_path: str, override: str | None) -> str:
    if override:
        # 'human' 또는 'human-label' 모두 허용
        v = override.strip().lower()
        if v in ("human", "human-label"): return "human-label"
        if v in ("pseudo", "pseudo-label"): return "pseudo-label"
        return override
    base = os.path.basename(labels_path).lower()
    return "human-label" if "human" in base else "pseudo-label"


_DESCRIPTIONS = {
    "human-label": (
        "사람이 직접 라벨링한 relevance 데이터 기반 추천 목록 순위 평가입니다."
    ),
    "pseudo-label": (
        "모델 벡터 기반 시연용 pseudo-label 평가입니다. "
        "최종 성능 평가는 사용자 라벨링 데이터 확보 후 재평가할 예정입니다."
    ),
}


def evaluate(labels_path: str, api_base: str, k: int,
             output_path: str | None, label_type_override: str | None) -> dict:
    df = pd.read_csv(labels_path, encoding="utf-8-sig")
    required = {"keyword", "user_pref_json", "restaurant", "relevance"}
    missing = required - set(df.columns)
    if missing:
        print(f"❌ 필수 컬럼 없음: {missing}")
        sys.exit(1)

    label_type = _detect_label_type(labels_path, label_type_override)
    has_graded = "relevance_grade" in df.columns

    # (keyword, user_pref_json) 기준 그룹화
    groups: dict = defaultdict(lambda: {"prefs": None, "relevant": set(), "all": set(), "grades": {}})
    for _, row in df.iterrows():
        key = (row["keyword"], row["user_pref_json"])
        groups[key]["prefs"] = json.loads(row["user_pref_json"])
        groups[key]["all"].add(row["restaurant"])
        if int(row["relevance"]) == 1:
            groups[key]["relevant"].add(row["restaurant"])
        if has_graded:
            try:
                g = int(row["relevance_grade"])
            except (ValueError, TypeError):
                g = 0
            if g > 0:
                groups[key]["grades"][row["restaurant"]] = g

    metrics: dict[str, list[float]] = defaultdict(list)
    failed_queries = 0
    per_query_records: list[dict] = []

    for (keyword, pref_str), payload in groups.items():
        prefs = payload["prefs"]
        relevant = payload["relevant"]
        grade_map = payload.get("grades") or {}
        try:
            results = fetch_predictions(api_base, keyword, prefs)
        except Exception as e:
            print(f"  ⚠️ {keyword} 추천 실패: {type(e).__name__}: {e}")
            failed_queries += 1
            continue

        predicted = [it.get("name") for it in results]
        metrics["precision_at_k"].append(precision_at_k(predicted, relevant, k))
        metrics["recall_at_k"].append(recall_at_k(predicted, relevant, k))
        metrics["f1_at_k"].append(f1_at_k(predicted, relevant, k))
        metrics["ndcg_at_k"].append(ndcg_at_k(predicted, relevant, k))
        if has_graded and grade_map:
            metrics["ndcg_graded_at_k"].append(ndcg_at_k_graded(predicted, grade_map, k))

        # 디버깅용 per-query 기록
        per_records = []
        for rank_i, item in enumerate(results, start=1):
            name = item.get("name")
            per_records.append({
                "rank":             rank_i,
                "restaurant_name":  name,
                "score":            float(item.get("similarity", 0.0)),
                "is_relevant":      bool(name in relevant),
                "relevance_grade":  int(grade_map.get(name, 1 if name in relevant else 0)),
            })
        per_query_records.append({
            "keyword":         keyword,
            "user_pref_json":  pref_str,
            "relevant_set":    sorted(relevant),
            "predicted":       per_records,
        })

    # ── 평균 + 콘솔 출력 ──
    print(f"\n{'━' * 50}")
    print(f"  Evaluation @ k={k}  (queries={len(metrics['precision_at_k'])})  label={label_type}")
    print(f"{'━' * 50}")
    avg: dict[str, float] = {}
    metric_keys = ["precision_at_k", "recall_at_k", "f1_at_k", "ndcg_at_k"]
    if metrics.get("ndcg_graded_at_k"):
        metric_keys.append("ndcg_graded_at_k")
    for name in metric_keys:
        vals = metrics.get(name) or []
        avg[name] = round(sum(vals) / len(vals), 4) if vals else 0.0
        print(f"  {name:18s} = {avg[name]:.4f}")

    # 호환용 별칭 (precision@k 등 기존 키)
    metrics_compat = dict(avg)
    metrics_compat.update({
        "precision@k": avg["precision_at_k"],
        "recall@k":    avg["recall_at_k"],
        "f1@k":        avg["f1_at_k"],
        "ndcg@k":      avg["ndcg_at_k"],
    })

    result = {
        "ok":              True,
        "k":               k,
        "queries":         len(metrics["precision_at_k"]),    # 기존 호환
        "query_count":     len(metrics["precision_at_k"]),    # 신규 별칭
        "failed_queries":  failed_queries,
        "label_type":      label_type,
        "description":     _DESCRIPTIONS.get(label_type, ""),
        "note":            _DESCRIPTIONS.get(label_type, ""),
        "metrics":         metrics_compat,
        "per_query_records": per_query_records,
    }

    if output_path:
        with open(output_path, "w", encoding="utf-8") as f:
            json.dump(result, f, ensure_ascii=False, indent=2)
        print(f"\n  💾 저장: {output_path}")

    return result


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--labels", default="eval_labels.csv")
    parser.add_argument("--api", default="http://127.0.0.1:5000")
    parser.add_argument("--k", type=int, default=5)
    parser.add_argument("--output", default="evaluation_results.json")
    parser.add_argument("--label-type", choices=["pseudo-label", "human-label"], default=None)
    args = parser.parse_args()
    evaluate(args.labels, args.api, args.k, args.output, args.label_type)
