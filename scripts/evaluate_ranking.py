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
import time
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

def _axis_discrimination(labels: dict[str, dict[str, float]]) -> dict[str, float]:
    """축별 변별력 = 식당 간 점수 표준편차. 클수록 식당을 잘 가름."""
    vals: dict[str, list[float]] = {}
    for scores in labels.values():
        for ax, v in scores.items():
            vals.setdefault(ax, []).append(float(v))
    disc: dict[str, float] = {}
    for ax, xs in vals.items():
        if len(xs) < 2:
            disc[ax] = 0.0
            continue
        m = sum(xs) / len(xs)
        disc[ax] = (sum((x - m) ** 2 for x in xs) / len(xs)) ** 0.5
    return disc


def generate_scenarios(labels: dict[str, dict[str, float]],
                       n: int = N_SCENARIOS_PER_KEYWORD,
                       valid_axes: set | None = None) -> list[dict]:
    """변별력(분산) 높은 순으로 축 2개씩 조합 → 시나리오.

    valid_axes(= 시스템이 실제로 점수 매길 수 있는 축, label∩system 교집합)를 주면
    그 축들로만 시나리오를 만든다. 시스템에 없는 라벨 축(예: 김밥의 '육질')으로
    평가하면 추천이 0점이라 무의미하므로 반드시 교집합으로 제한해야 한다.
    """
    disc = _axis_discrimination(labels)
    # 변별력 0(모든 식당 동일)인 축은 랭킹에 무의미하므로 제외
    pool = [ax for ax in disc if disc[ax] > 0 and (valid_axes is None or ax in valid_axes)]
    pool.sort(key=lambda a: -disc[a])           # 변별력 큰 순 (전반적만족 등 비변별 축은 뒤로)
    top_axes = pool[:6]
    if len(top_axes) < 2:
        return []                                # 평가 가능한 공유 축 부족
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
    last_err = None
    for attempt in range(3):
        try:
            r = requests.post(f"{api}/api/recommend", json=payload, timeout=60)
            r.raise_for_status()
            data = r.json()
            return [item.get("name", "") for item in (data.get("results") or [])[:limit]]
        except requests.exceptions.RequestException as e:
            last_err = e
            time.sleep(1.0 * (attempt + 1))   # 일시적 지연/타임아웃 재시도
    raise last_err


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
    parser.add_argument("--resume", action="store_true",
                        help="out 파일에 이미 있는 keyword는 건너뜀 (중단 후 이어돌리기)")
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

    out_path = Path(args.out)
    out_path.parent.mkdir(parents=True, exist_ok=True)

    def _save(reps):
        # 기존 파일과 keyword 기준 병합 → 배치로 나눠 돌려도 결과 누적
        merged: dict = {}
        if out_path.exists():
            try:
                prev = json.load(open(out_path, encoding="utf-8")).get("reports", [])
                for r in prev:
                    merged[r.get("keyword")] = r
            except Exception:
                pass
        for r in reps:
            merged[r.get("keyword")] = r
        with open(out_path, "w", encoding="utf-8") as f:
            json.dump({
                "ablation_variants": variants,
                "semantic_weight": args.semantic_weight,
                "reports": list(merged.values()),
            }, f, ensure_ascii=False, indent=2)

    # 시스템(stand) 축 계산용 — label∩system 교집합으로 시나리오 제한
    sys.path.insert(0, str(ROOT))
    import Food_profiler as fp

    done_kws = set()
    if args.resume and out_path.exists():
        try:
            done_kws = {r.get("keyword") for r in
                        json.load(open(out_path, encoding="utf-8")).get("reports", [])}
            print(f"  ⏭️  --resume: 이미 완료된 {len(done_kws)}개 건너뜀")
        except Exception:
            pass

    reports = []
    for kw in args.keywords:
        if kw in done_kws:
            print(f"  ⏭️  '{kw}' 이미 완료 — skip")
            continue
        label_file = labels_dir / f"labels_{kw}.json"
        if not label_file.exists():
            print(f"⚠️ '{kw}' 라벨 없음: {label_file}")
            continue
        with open(label_file, encoding="utf-8") as f:
            labels = json.load(f)["labels"]
        taste, meta = fp.compose_axes(kw)
        sys_axes = set(taste) | set(meta)
        scenarios = generate_scenarios(labels, n=args.n_scenarios, valid_axes=sys_axes)
        if not scenarios:
            print(f"  ⚠️ '{kw}' 평가 가능한 공유 축 부족(label∩system<2) → skip")
            continue
        report = evaluate_keyword(args.api, kw, labels, scenarios=scenarios,
                                  variants=variants,
                                  semantic_weight=args.semantic_weight)
        reports.append(report)
        _save(reports)   # 키워드마다 중간 저장 — 중단/크래시에도 완료분은 보존

    print_ablation_table(reports, variants)
    _save(reports)
    print(f"\n💾 결과 저장: {out_path.relative_to(ROOT)}")


if __name__ == "__main__":
    main()
