"""
scripts/evaluate_ranking.py — 4-way Ablation 평가 파이프라인
================================================================

4개 모델 variant의 추천 정확도를 NDCG@5, MRR, Recall@5, Precision@5 로 비교.

Variants:
  [1] text_only   : lex 매칭만 (use_image=false, use_semantic=false)
  [2] +CLIP image : lex + CLIP 이미지 fusion (use_image=true, use_semantic=false)
  [3] +BERT       : lex + KoSBERT semantic (use_image=false, use_semantic=true)
  [4] full        : lex + CLIP + BERT (use_image=true, use_semantic=true)

흐름:
  1. eval/labels_<keyword>.json 로드
  2. 시나리오 자동 생성
  3. 각 시나리오 × 4 variant 호출 → ranking
  4. 메트릭 계산 후 ablation 표 출력

사용:
  python scripts/evaluate_ranking.py 짬뽕
  python scripts/evaluate_ranking.py 짬뽕 피자 버거 치킨
  python scripts/evaluate_ranking.py 짬뽕 --semantic-weight 0.3
  python scripts/evaluate_ranking.py 짬뽕 --variants text_only,full   # 일부만
"""
from __future__ import annotations

import argparse
import json
import math
import sys
from itertools import combinations
from pathlib import Path

import requests

ROOT = Path(__file__).resolve().parents[1]
DEFAULT_API = "http://localhost:5050"
DEFAULT_LABELS_DIR = ROOT / "eval"
DEFAULT_REPORT_DIR = ROOT / "eval"
TOP_K = 5
N_SCENARIOS_PER_KEYWORD = 10


# 4-way variant 정의 — (use_image, use_semantic, 라벨)
VARIANTS = {
    "text_only":   (False, False, "lex만"),
    "text+image":  (True,  False, "+CLIP"),
    "text+sem":    (False, True,  "+BERT"),
    "full":        (True,  True,  "+CLIP+BERT"),
}


# ── 시나리오 생성 ────────────────────────────────────────────

def generate_scenarios(labels: dict[str, dict[str, float]],
                       n: int = N_SCENARIOS_PER_KEYWORD) -> list[dict]:
    """라벨 데이터에서 자주 등장하는 축 2개씩 조합 → 시나리오."""
    axis_freq: dict[str, int] = {}
    for scores in labels.values():
        for ax in scores:
            axis_freq[ax] = axis_freq.get(ax, 0) + 1
    sorted_axes = [a for a, _ in sorted(axis_freq.items(), key=lambda x: -x[1])]
    top_axes = sorted_axes[:6]
    pairs = list(combinations(top_axes, 2))[:n]
    return [
        {"id": f"S{i+1:02d}", "name": f"{a1} + {a2}", "prefs": {a1: 9, a2: 6}}
        for i, (a1, a2) in enumerate(pairs)
    ]


# ── Ground Truth ranking ─────────────────────────────────────

def ground_truth_ranking(scenario: dict,
                         labels: dict[str, dict[str, float]]) -> list[tuple[str, float]]:
    pref = scenario["prefs"]
    scored = []
    for rest, ax_scores in labels.items():
        score = sum(ax_scores.get(ax, 0.0) * w for ax, w in pref.items())
        scored.append((rest, score))
    scored.sort(key=lambda x: -x[1])
    return scored


# ── System ranking via /api/recommend ────────────────────────

def system_ranking(api: str, keyword: str, prefs: dict, *,
                   use_image: bool,
                   use_semantic: bool,
                   semantic_weight: float = 0.5,
                   limit: int = 20) -> list[str]:
    payload = {
        "_keyword": keyword,
        "text_preferences": prefs,
        "use_image": use_image,
        "use_semantic": use_semantic,
        "semantic_weight": semantic_weight,
        "use_image_fusion": False,   # 사용자 이미지 픽은 평가에서 사용 안 함
    }
    r = requests.post(f"{api}/api/recommend", json=payload, timeout=15)
    r.raise_for_status()
    data = r.json()
    return [item.get("name", "") for item in (data.get("results") or [])[:limit]]


# ── 메트릭 ───────────────────────────────────────────────────

def dcg(relevances: list[float]) -> float:
    return sum(rel / math.log2(i + 2) for i, rel in enumerate(relevances))


def ndcg_at_k(predicted: list[str], gt_rel: dict[str, float], k: int = TOP_K) -> float:
    pred_rels = [gt_rel.get(n, 0.0) for n in predicted[:k]]
    ideal_rels = sorted(gt_rel.values(), reverse=True)[:k]
    if not ideal_rels or sum(ideal_rels) == 0:
        return 0.0
    return dcg(pred_rels) / dcg(ideal_rels)


def mrr(predicted: list[str], relevant: set[str]) -> float:
    for i, name in enumerate(predicted, 1):
        if name in relevant:
            return 1.0 / i
    return 0.0


def recall_at_k(predicted: list[str], relevant: set[str], k: int = TOP_K) -> float:
    if not relevant:
        return 0.0
    hit = sum(1 for n in predicted[:k] if n in relevant)
    return hit / min(len(relevant), k)


def precision_at_k(predicted: list[str], relevant: set[str], k: int = TOP_K) -> float:
    if k == 0:
        return 0.0
    return sum(1 for n in predicted[:k] if n in relevant) / k


# ── 평가 ─────────────────────────────────────────────────────

def evaluate_keyword(api: str, keyword: str, labels: dict, *,
                     scenarios: list[dict],
                     variants: list[str],
                     semantic_weight: float = 0.5) -> dict:
    print(f"\n{'━'*75}")
    print(f"  📊 평가: '{keyword}'  시나리오 {len(scenarios)}개  variants={variants}")
    print(f"{'━'*75}")

    metrics_per_variant: dict[str, dict[str, list[float]]] = {
        v: {"ndcg": [], "mrr": [], "recall": [], "precision": []} for v in variants
    }
    scenario_details = []
    rank_changes: dict[str, list[dict]] = {v: [] for v in variants}  # 식당별 ranking 변화

    for sc in scenarios:
        gt_rank = ground_truth_ranking(sc, labels)
        gt_rel = {n: round(s / (gt_rank[0][1] + 1e-9), 4) for n, s in gt_rank if s > 0}
        gt_relevant = {n for n, s in gt_rank[:TOP_K] if s > 0}
        sc_row = {"id": sc["id"], "name": sc["name"], "prefs": sc["prefs"],
                  "gt_top5": [n for n, _ in gt_rank[:TOP_K]], "variants": {}}

        line_parts = [f"  {sc['id']} {sc['name'][:28]:30s}"]
        for v in variants:
            use_image, use_semantic, _ = VARIANTS[v]
            try:
                pred = system_ranking(api, keyword, sc["prefs"],
                                      use_image=use_image, use_semantic=use_semantic,
                                      semantic_weight=semantic_weight, limit=20)
            except Exception as e:
                print(f"  ⚠️ {sc['id']} {v} API 실패: {e}")
                continue

            n_score = ndcg_at_k(pred, gt_rel)
            m_score = mrr(pred, gt_relevant)
            r_score = recall_at_k(pred, gt_relevant)
            p_score = precision_at_k(pred, gt_relevant)

            metrics_per_variant[v]["ndcg"].append(n_score)
            metrics_per_variant[v]["mrr"].append(m_score)
            metrics_per_variant[v]["recall"].append(r_score)
            metrics_per_variant[v]["precision"].append(p_score)

            sc_row["variants"][v] = {
                "top5": pred[:TOP_K], "ndcg": n_score, "mrr": m_score,
                "recall": r_score, "precision": p_score,
            }
            line_parts.append(f"{v}={n_score:.3f}")
            rank_changes[v].append({"scenario": sc["id"], "pred_top5": pred[:TOP_K]})

        print(" | ".join(line_parts))
        scenario_details.append(sc_row)

    # 집계
    def _avg(lst): return round(sum(lst) / len(lst), 4) if lst else 0.0

    summary = {}
    for v in variants:
        summary[v] = {m: _avg(metrics_per_variant[v][m])
                      for m in ("ndcg", "mrr", "recall", "precision")}

    return {
        "keyword": keyword,
        "n_scenarios": len(scenarios),
        "variants": variants,
        "semantic_weight": semantic_weight,
        "summary": summary,
        "scenarios": scenario_details,
    }


def print_ablation_table(reports: list[dict], variants: list[str]):
    print(f"\n{'═'*82}")
    print(f"  📋 Ablation 비교 표  (variant 평균, k={TOP_K})")
    print(f"{'═'*82}")

    # 헤더
    hdr = f"  {'keyword':10s} | {'metric':10s} |"
    for v in variants:
        hdr += f" {VARIANTS[v][2]:>12s} |"
    print(hdr)
    print(f"  {'-'*10} | {'-'*10} |" + ("".join([f" {'-'*12} |" for _ in variants])))

    for rep in reports:
        kw = rep["keyword"]
        for m in ("ndcg", "mrr", "recall", "precision"):
            row = f"  {kw:10s} | {m:10s} |"
            base = rep["summary"][variants[0]][m]
            for v in variants:
                val = rep["summary"][v][m]
                if v == variants[0]:
                    row += f" {val:>12.3f} |"
                else:
                    diff = val - base
                    sign = "+" if diff >= 0 else ""
                    row += f" {val:>5.3f} ({sign}{diff:>5.3f}) |"
            print(row)
        print(f"  {'-'*10} | {'-'*10} |" + ("".join([f" {'-'*12} |" for _ in variants])))


def main():
    parser = argparse.ArgumentParser(description="4-way Ablation 평가 (text/+CLIP/+BERT/full)")
    parser.add_argument("keywords", nargs="+")
    parser.add_argument("--api", default=DEFAULT_API)
    parser.add_argument("--semantic-weight", type=float, default=0.5)
    parser.add_argument("--n-scenarios", type=int, default=N_SCENARIOS_PER_KEYWORD)
    parser.add_argument("--variants", default="text_only,text+image,text+sem,full",
                        help=f"콤마구분. 사용 가능: {list(VARIANTS.keys())}")
    parser.add_argument("--labels-dir", default=str(DEFAULT_LABELS_DIR))
    parser.add_argument("--out", default=str(DEFAULT_REPORT_DIR / "evaluation_results.json"))
    args = parser.parse_args()

    variants = [v.strip() for v in args.variants.split(",") if v.strip() in VARIANTS]
    if not variants:
        sys.exit(f"❌ 유효한 variant 없음. 선택: {list(VARIANTS.keys())}")

    labels_dir = Path(args.labels_dir)

    # health
    try:
        requests.get(f"{args.api}/api/health", timeout=3).raise_for_status()
    except Exception as e:
        sys.exit(f"❌ 백엔드({args.api}) 미응답: {e}\n   → 다른 터미널에서 python app.py 먼저")

    reports = []
    for kw in args.keywords:
        label_file = labels_dir / f"labels_{kw}.json"
        if not label_file.exists():
            print(f"⚠️ '{kw}' 라벨 없음: {label_file}")
            continue
        with open(label_file, encoding="utf-8") as f:
            labels = json.load(f)["labels"]
        scenarios = generate_scenarios(labels, n=args.n_scenarios)
        report = evaluate_keyword(args.api, kw, labels, scenarios=scenarios,
                                  variants=variants,
                                  semantic_weight=args.semantic_weight)
        reports.append(report)

    print_ablation_table(reports, variants)

    out_path = Path(args.out)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    with open(out_path, "w", encoding="utf-8") as f:
        json.dump({
            "ablation_variants": variants,
            "semantic_weight": args.semantic_weight,
            "reports": reports,
        }, f, ensure_ascii=False, indent=2)
    print(f"\n💾 결과 저장: {out_path.relative_to(ROOT)}")


if __name__ == "__main__":
    main()
