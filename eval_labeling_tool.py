"""
eval_labeling_tool.py — 사람이 직접 relevance를 입력해 human-label CSV를 만드는 도구
======================================================================================
⚠️ 자동 라벨링 모드는 없습니다. 모든 relevance는 stdin으로 사람이 입력해야 합니다.
   완전 자동 라벨이 필요하면 make_eval_labels_from_vectors.py (pseudo-label) 를 쓰세요.

사용:
  python eval_labeling_tool.py --keyword 버거 --k 5
  python eval_labeling_tool.py --keyword 회 --k 5
  python eval_labeling_tool.py --keyword 샐러드 --k 5
  python eval_labeling_tool.py --keyword 버거 --scenario-file my_scenarios.json
  python eval_labeling_tool.py --keyword 버거 --api http://127.0.0.1:5000

전제:
  - app.py가 실행 중이어야 함 (/api/health, /api/config, /api/recommend 사용)
  - DB는 그대로 두고 추천 결과만 읽음 (DB 수정 없음)
  - .env, AWS 키, DB 비밀번호는 절대 출력/수정하지 않음

라벨링 입력:
  0 = 관련 없음
  1 = 약간 관련
  2 = 관련 있음
  3 = 매우 관련 있음
  (Enter)        = skip — 라벨 안 남김
  b              = 직전 식당 다시 라벨
  q              = 도구 종료, 지금까지 라벨만 저장

출력:
  eval_labels_human.csv  (UTF-8-SIG)
"""

from __future__ import annotations

import argparse
import csv
import json
import os
import sys
from typing import Optional

import requests

DEFAULT_API = "http://127.0.0.1:5000"
DEFAULT_OUTPUT = "eval_labels_human.csv"
RELEVANT_THRESHOLD = 2     # grade >= 2 → relevance=1 (binary 변환)
DEFAULT_SCENARIO_LIMIT = 4

CSV_FIELDS = [
    "keyword", "query_id", "user_pref_json", "restaurant",
    "relevance", "relevance_grade", "label_type", "notes",
]


# ── HTTP ───────────────────────────────────────────────────────

def _get(api: str, path: str, **params) -> dict:
    r = requests.get(f"{api}{path}", params=params or None, timeout=10)
    r.raise_for_status()
    return r.json()


def _post(api: str, path: str, payload: dict) -> dict:
    r = requests.post(f"{api}{path}", json=payload, timeout=15)
    r.raise_for_status()
    return r.json()


def check_server(api: str) -> bool:
    try:
        h = _get(api, "/api/health")
        if not h.get("ok"):
            print(f"❌ /api/health ok != true: {h}")
            return False
        return True
    except Exception as e:
        print(f"❌ {api} 에 연결할 수 없습니다: {type(e).__name__}: {e}")
        print("   → 다른 터미널에서 'python app.py' 를 먼저 실행하세요.")
        return False


# ── 로컬 stand 폴백 (app.py 미실행 보호용은 아니지만, food_specific 직접 조회용) ─

_STAND_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "stand")


def load_food_specific(keyword: str) -> list[str]:
    """
    stand_axes.json에서 keyword 또는 alias_to_category로 매핑된 food_specific axes의 raw key list.
    """
    path = os.path.join(_STAND_DIR, "stand_axes.json")
    if not os.path.exists(path):
        return []
    with open(path, "r", encoding="utf-8") as f:
        data = json.load(f)
    food_specific = data.get("food_specific_axes", {}) or {}
    a2c = data.get("alias_to_category", {}) or {}

    cat = keyword if keyword in food_specific else a2c.get(keyword)
    if not cat or cat not in food_specific:
        return []
    return [k for k in food_specific[cat].keys() if not k.startswith("_")]


# ── 시나리오 ───────────────────────────────────────────────────

def build_default_scenarios(keyword: str, taste_axes: list[str], meta_axes: list[str],
                             axis_label_map: dict, food_specific_keys: list[str]) -> list[dict]:
    """
    keyword / taste / meta / food_specific을 보고 여러 시나리오를 자동 구성.
    raw axis key를 user_pref로, 화면 안내는 axis_label_map의 라벨을 사용.
    실제 사용 가능한 축만 살림 (taste_axes에 없거나 food_specific에 없으면 skip).
    """
    available = set(taste_axes) | set(meta_axes) | set(food_specific_keys)

    def L(k: str) -> str:
        return axis_label_map.get(k) or k

    scenarios: list[dict] = []

    # S1: 특화 2개 + 공용 1개 (예: 버거 → 패티퀄리티 + 번식감 + 촉촉함)
    common_pool = ["촉촉함", "감칠맛", "고소함", "담백함", "바삭함", "쫄깃함"]
    common_first = next((c for c in common_pool if c in taste_axes), None)
    if len(food_specific_keys) >= 2:
        prefs = {food_specific_keys[0]: 1, food_specific_keys[1]: 1}
        title_parts = [L(food_specific_keys[0]), L(food_specific_keys[1])]
        if common_first:
            prefs[common_first] = 1
            title_parts.append(L(common_first))
        scenarios.append({
            "id": "S1", "title": f"특화 중심 ({' · '.join(title_parts)})",
            "prefs": prefs,
        })

    # S2: 특화 보조 (3,4번째 특화)
    if len(food_specific_keys) >= 4:
        prefs = {food_specific_keys[2]: 1, food_specific_keys[3]: 1}
        scenarios.append({
            "id": "S2", "title": f"특화 보조 ({L(food_specific_keys[2])} · {L(food_specific_keys[3])})",
            "prefs": prefs,
        })
    elif len(food_specific_keys) == 3:
        prefs = {food_specific_keys[2]: 1}
        if "감칠맛" in taste_axes:
            prefs["감칠맛"] = 1
        scenarios.append({
            "id": "S2", "title": f"특화 보조 ({L(food_specific_keys[2])})",
            "prefs": prefs,
        })

    # S3: 매운맛 + 담백 + 가성비
    s3 = {}
    for k in ("매운맛", "담백함", "가성비"):
        if k in available:
            s3[k] = 1
    if len(s3) >= 2:
        scenarios.append({
            "id": "S3", "title": f"공용 맛 + 메타 ({' · '.join(L(k) for k in s3)})",
            "prefs": s3,
        })

    # S4: 식감 중심
    s4 = {}
    for k in ("바삭함", "촉촉함", "쫄깃함"):
        if k in available:
            s4[k] = 1
    if len(s4) >= 2:
        scenarios.append({
            "id": "S4", "title": f"식감 중심 ({' · '.join(L(k) for k in s4)})",
            "prefs": s4,
        })

    # S5: 풍미 균형
    s5 = {}
    for k in ("감칠맛", "고소함"):
        if k in available:
            s5[k] = 1
    if len(s5) >= 2:
        scenarios.append({
            "id": "S5", "title": f"풍미 균형 ({' · '.join(L(k) for k in s5)})",
            "prefs": s5,
        })

    # 중복 prefs(키 동일) 제거
    seen = set()
    deduped = []
    for s in scenarios:
        key = tuple(sorted((k, float(v)) for k, v in s["prefs"].items()))
        if key in seen:
            continue
        seen.add(key)
        deduped.append(s)

    return deduped[:DEFAULT_SCENARIO_LIMIT]


def load_scenario_file(path: str) -> list[dict]:
    with open(path, "r", encoding="utf-8") as f:
        raw = json.load(f)
    if not isinstance(raw, list):
        raise ValueError("scenario-file은 list여야 합니다.")
    out = []
    for i, item in enumerate(raw, start=1):
        prefs = item.get("prefs") or item.get("preferences") or {}
        if not isinstance(prefs, dict) or not prefs:
            continue
        out.append({
            "id":    item.get("id") or f"U{i}",
            "title": item.get("title") or f"User scenario {i}",
            "prefs": {k: float(v) for k, v in prefs.items()},
        })
    return out


# ── CSV ────────────────────────────────────────────────────────

def prompt_existing_csv(path: str) -> str:
    """
    기존 파일이 있으면 [a]ppend / [o]verwrite / [c]ancel 선택.
    반환: "append" | "overwrite" | "cancel"
    """
    if not os.path.exists(path):
        return "overwrite"   # 새로 만듦
    print(f"\n⚠️ 기존 파일이 있습니다: {path}")
    while True:
        ans = input("  [a]ppend / [o]verwrite / [c]ancel ? ").strip().lower()
        if ans in ("a", "append"): return "append"
        if ans in ("o", "overwrite"): return "overwrite"
        if ans in ("c", "cancel", "q"): return "cancel"


def write_rows(path: str, rows: list[dict], mode: str):
    """mode: 'append' or 'overwrite'."""
    if not rows:
        return
    is_new = (mode == "overwrite") or not os.path.exists(path)
    open_mode = "w" if is_new else "a"
    with open(path, open_mode, encoding="utf-8-sig", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=CSV_FIELDS)
        if is_new:
            writer.writeheader()
        for r in rows:
            writer.writerow({k: r.get(k, "") for k in CSV_FIELDS})


# ── 라벨링 루프 ────────────────────────────────────────────────

_HELP_LINE = ("입력: 0=관련없음 1=약간관련 2=관련있음 3=매우관련 / "
              "Enter=skip / b=직전 다시 / q=종료")


def label_scenario(api: str, keyword: str, scenario: dict, k: int,
                   axis_label_map: dict) -> tuple[list[dict], bool]:
    """
    단일 시나리오 라벨링. 반환: (rows, abort?)  abort=True면 도구 전체 종료.
    """
    prefs = scenario["prefs"]
    qid = scenario["id"]
    title = scenario["title"]
    pref_json = json.dumps(prefs, ensure_ascii=False, sort_keys=True)

    print(f"\n{'━' * 60}")
    print(f"  📌 [{qid}] {title}")
    print(f"     prefs: {pref_json}")
    print(f"{'━' * 60}")

    try:
        data = _post(api, "/api/recommend",
                     {"_keyword": keyword, "text_preferences": prefs})
    except Exception as e:
        print(f"  ❌ 추천 호출 실패: {type(e).__name__}: {e}")
        return [], False

    results = (data.get("results") or [])[:k]
    if not results:
        print("  ⚠️ 추천 결과 없음 → 시나리오 skip")
        return [], False

    print(f"\n  Top-{k} 추천 (사람이 0~3 입력):")
    print(f"  {_HELP_LINE}\n")

    rows: list[dict] = []
    i = 0
    while i < len(results):
        r = results[i]
        rank = r.get("rank") or (i + 1)
        match = r.get("match_percent")
        if match is None:
            match = round(float(r.get("similarity", 0)) * 100)
        top_axes_keys = r.get("top_axes") or r.get("reasons") or []
        top_axes_lbls = r.get("top_axes_labels") or [
            (axis_label_map.get(a) or a) for a in top_axes_keys
        ]
        rep_url = (r.get("representative_image") or {}).get("image_src", "")
        evidence = (r.get("evidence") or [""])[0]
        if evidence and len(evidence) > 80:
            evidence = evidence[:78] + "…"

        print(f"  [{rank}] {r.get('name', '(이름 없음)')}  · 매칭 {match}%")
        print(f"      축: {' / '.join(top_axes_lbls[:3]) if top_axes_lbls else '-'}")
        if evidence:
            print(f"      \"{evidence}\"")
        if rep_url:
            print(f"      img: {rep_url}")

        ans = input(f"      relevance grade [0-3 / Enter / b / q] > ").strip().lower()
        if ans == "q":
            return rows, True
        if ans == "b":
            if i > 0:
                # 마지막 라벨 제거 후 한 칸 뒤로
                if rows and rows[-1]["restaurant"] == results[i - 1].get("name"):
                    rows.pop()
                i -= 1
                print("      ↩️  직전 식당으로 돌아갑니다.")
                continue
            print("      ⚠️ 직전 식당이 없습니다.")
            continue
        if ans == "":
            print("      ⏭️  skip (라벨 미저장)")
            i += 1
            continue
        if ans not in ("0", "1", "2", "3"):
            print("      ⚠️ 0~3 또는 Enter/b/q 만 허용. 다시 입력하세요.")
            continue

        grade = int(ans)
        rel = 1 if grade >= RELEVANT_THRESHOLD else 0
        rows.append({
            "keyword":         keyword,
            "query_id":        qid,
            "user_pref_json":  pref_json,
            "restaurant":      r.get("name", ""),
            "relevance":       rel,
            "relevance_grade": grade,
            "label_type":      "human-label",
            "notes":           "",
        })
        i += 1

    return rows, False


# ── 메인 ──────────────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser(description="human-label 평가 CSV 생성기")
    parser.add_argument("--keyword", required=True, help="대상 음식 keyword (예: 버거, 회, 샐러드)")
    parser.add_argument("--k", type=int, default=5, help="시나리오당 라벨링할 Top-K (default 5)")
    parser.add_argument("--api", default=DEFAULT_API, help="app.py API base URL")
    parser.add_argument("--output", default=DEFAULT_OUTPUT, help="출력 CSV (default eval_labels_human.csv)")
    parser.add_argument("--scenario-file", default=None,
                        help="(선택) 사용자가 만든 시나리오 JSON 경로")
    parser.add_argument("--limit", type=int, default=DEFAULT_SCENARIO_LIMIT,
                        help="자동 시나리오 최대 개수 (default 4)")
    args = parser.parse_args()

    print(f"\n{'=' * 60}")
    print(f"  human-label 라벨링 도구  keyword={args.keyword}  k={args.k}")
    print(f"{'=' * 60}")

    # 1. 서버 살아있는지
    if not check_server(args.api):
        return 2

    # 2. 축 정보 로드
    try:
        cfg = _get(args.api, "/api/config", keyword=args.keyword)
    except Exception as e:
        print(f"❌ /api/config 실패: {type(e).__name__}: {e}")
        return 2
    if cfg.get("error"):
        print(f"❌ {cfg['error']}")
        return 2

    taste_axes = cfg.get("taste_axes") or []
    meta_axes = cfg.get("meta_axes") or []
    axis_label_map = cfg.get("axis_label_map") or {}
    food_specific = load_food_specific(args.keyword)

    print(f"  taste_axes: {len(taste_axes)}개 / meta_axes: {len(meta_axes)}개 / 특화: {len(food_specific)}개")
    if food_specific:
        print(f"  특화 축: {[axis_label_map.get(k, k) for k in food_specific]}")

    # 3. 시나리오 구성
    if args.scenario_file:
        try:
            scenarios = load_scenario_file(args.scenario_file)
            print(f"  📂 사용자 시나리오 파일: {args.scenario_file} ({len(scenarios)}개)")
        except Exception as e:
            print(f"❌ 시나리오 파일 읽기 실패: {type(e).__name__}: {e}")
            return 2
    else:
        scenarios = build_default_scenarios(
            args.keyword, taste_axes, meta_axes, axis_label_map, food_specific,
        )[:args.limit]
    if not scenarios:
        print("❌ 만들 수 있는 시나리오가 없습니다 (taste/meta/특화 모두 비어 있음).")
        return 2

    print(f"\n  📝 시나리오 {len(scenarios)}개 — 시나리오당 Top-{args.k} 라벨링")
    for s in scenarios:
        print(f"     - [{s['id']}] {s['title']}")

    # 4. 기존 CSV 처리
    mode = prompt_existing_csv(args.output)
    if mode == "cancel":
        print("취소했습니다.")
        return 1

    # 5. 라벨링 진행
    all_rows: list[dict] = []
    aborted = False
    try:
        for s in scenarios:
            rows, abort = label_scenario(args.api, args.keyword, s, args.k, axis_label_map)
            all_rows.extend(rows)
            if abort:
                aborted = True
                print("\n  🛑 사용자 종료 (q). 지금까지의 라벨만 저장합니다.")
                break
    except KeyboardInterrupt:
        aborted = True
        print("\n\n  🛑 Ctrl+C — 지금까지의 라벨만 저장합니다.")

    # 6. 저장
    if not all_rows:
        print("\n  ⚠️ 저장할 라벨이 없습니다.")
        return 0

    write_rows(args.output, all_rows, mode if mode == "overwrite" else "append")
    print(f"\n  ✅ {len(all_rows)}행 저장 → {args.output}  (mode={mode}, label_type=human-label)")

    # 7. 다음 단계 안내
    print(f"\n  다음 단계:")
    print(f"    python evaluation.py --labels {args.output} --k {args.k} --label-type human")
    print(f"    → /api/evaluation 갱신 후 프론트 EvaluationCard에서 '사용자 라벨 기반' 으로 표시됩니다.\n")

    return 0


if __name__ == "__main__":
    sys.exit(main())
