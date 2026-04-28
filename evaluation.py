"""
evaluation.py — 추천 품질 평가용 스크립트 (콘솔 + JSON 저장)
==============================================================
사용자 화면에는 노출되지 않는 별도 평가 도구.
정답 라벨이 있는 CSV가 있을 때만 의미 있음.

평가 데이터 형식 (eval_labels.csv, UTF-8-SIG):
  keyword,user_pref_json,restaurant,relevance
  버거,"{""촉촉함"":1,""담백함"":1}",버거스타,1
  버거,"{""촉촉함"":1,""담백함"":1}",버거다이브,0

⚠️  make_eval_labels_from_vectors.py가 만든 CSV는 모델 벡터 기반 pseudo-label입니다.
    실제 성능 평가는 사람이 직접 라벨링한 relevance 데이터가 필요합니다.

실행:
  python evaluation.py --labels eval_labels.csv --k 5
  python evaluation.py --labels eval_labels.csv --k 5 --output evaluation_results.json
  python evaluation.py --labels eval_labels.csv --api http://127.0.0.1:5000
"""

import argparse
import json
import math
import sys
from collections import defaultdict

import pandas as pd


def precision_at_k(predicted: list[str], relevant: set[str], k: int) -> float:
    if k <= 0:
        return 0.0
    top = predicted[:k]
    if not top:
        return 0.0
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


def fetch_predictions(api_base: str, keyword: str, prefs: dict) -> list[str]:
    import requests
    r = requests.post(
        f"{api_base}/api/recommend",
        json={"_keyword": keyword, "text_preferences": prefs},
        timeout=10,
    )
    r.raise_for_status()
    data = r.json()
    return [it["name"] for it in data.get("results", [])]


def evaluate(labels_path: str, api_base: str, k: int, output_path: str | None) -> dict:
    df = pd.read_csv(labels_path, encoding="utf-8-sig")
    required = {"keyword", "user_pref_json", "restaurant", "relevance"}
    missing = required - set(df.columns)
    if missing:
        print(f"❌ 필수 컬럼 없음: {missing}")
        sys.exit(1)

    # (keyword, user_pref_json) 기준 그룹화
    groups: dict = defaultdict(lambda: {"prefs": None, "relevant": set(), "all": set()})
    for _, row in df.iterrows():
        key = (row["keyword"], row["user_pref_json"])
        groups[key]["prefs"] = json.loads(row["user_pref_json"])
        groups[key]["all"].add(row["restaurant"])
        if int(row["relevance"]) == 1:
            groups[key]["relevant"].add(row["restaurant"])

    metrics: dict[str, list[float]] = defaultdict(list)
    failed_queries = 0
    for (keyword, _), payload in groups.items():
        prefs = payload["prefs"]
        relevant = payload["relevant"]
        try:
            predicted = fetch_predictions(api_base, keyword, prefs)
        except Exception as e:
            print(f"  ⚠️ {keyword} 추천 실패: {e}")
            failed_queries += 1
            continue
        metrics["precision@k"].append(precision_at_k(predicted, relevant, k))
        metrics["recall@k"].append(recall_at_k(predicted, relevant, k))
        metrics["f1@k"].append(f1_at_k(predicted, relevant, k))
        metrics["ndcg@k"].append(ndcg_at_k(predicted, relevant, k))

    print(f"\n{'━' * 50}")
    print(f"  Evaluation @ k={k}  (queries={len(metrics['precision@k'])})")
    print(f"{'━' * 50}")
    avg = {}
    for name, vals in metrics.items():
        if vals:
            avg[name] = round(sum(vals) / len(vals), 4)
            print(f"  {name:15s} = {avg[name]:.4f}")
        else:
            avg[name] = 0.0

    result = {
        "ok": True,
        "k": k,
        "queries": len(metrics["precision@k"]),
        "failed_queries": failed_queries,
        "label_type": "pseudo-label",
        "note": "모델 벡터 기반 시연용 평가입니다. 실제 성능 평가는 사람이 직접 라벨링한 relevance 데이터가 필요합니다.",
        "metrics": avg,
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
    args = parser.parse_args()
    evaluate(args.labels, args.api, args.k, args.output)
