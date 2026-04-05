"""
Stand Builder — 검수된 후보에서 Stand JSON 생성/갱신
====================================================
stand_candidate_miner.py가 뽑은 candidate_terms.csv를 사람이 검수한 뒤,
이 스크립트로 stand/ 폴더의 JSON 파일을 생성/갱신한다.

사용법:
  1. python stand_candidate_miner.py 짜장면  → candidate_terms.csv 생성
  2. CSV 열어서 keep=O, assign_to_axis, polarity 기입
  3. python stand_builder.py                 → stand/ JSON 갱신

또는 처음부터 수동으로 stand/ JSON을 직접 편집해도 됨.
이 스크립트는 CSV 검수 결과를 기존 Stand에 merge하는 보조 도구.

실행: python stand_builder.py
      python stand_builder.py candidate_terms.csv
"""

import json
import os
import sys
import pandas as pd
from collections import defaultdict
from copy import deepcopy

STAND_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "stand")


def load_existing_stand():
    """기존 stand/ JSON 로드 (없으면 빈 구조 반환)"""
    result = {}
    for name in ["stand_axes.json", "stand_lexicon.json", "stand_rules.json"]:
        path = os.path.join(STAND_DIR, name)
        if os.path.exists(path):
            with open(path, "r", encoding="utf-8") as f:
                result[name] = json.load(f)
            print(f"  ✅ 기존 {name} 로드")
        else:
            result[name] = {}
            print(f"  ⚠️ {name} 없음 → 새로 생성")
    return result


def read_reviewed_csv(csv_path):
    """
    검수된 candidate_terms.csv 읽기.
    필수 열: term, keep, assign_to_axis, polarity
    keep=O인 행만 사용.
    """
    df = pd.read_csv(csv_path, encoding="utf-8-sig")

    required = ["term", "keep", "assign_to_axis", "polarity"]
    missing = [c for c in required if c not in df.columns]
    if missing:
        print(f"  ❌ CSV에 필수 열 누락: {missing}")
        print(f"  필요 열: {required}")
        return None

    # keep=O (또는 o, ㅇ) 인 행만
    df["keep"] = df["keep"].astype(str).str.strip().str.upper()
    kept = df[df["keep"].isin(["O", "ㅇ", "YES", "Y", "1"])].copy()

    if len(kept) == 0:
        print("  ⚠️ keep=O인 항목이 없습니다. CSV를 먼저 검수해주세요.")
        return None

    print(f"  검수 완료 항목: {len(kept)}개 (전체 {len(df)}개 중)")
    return kept


def merge_into_lexicon(existing_lexicon, reviewed_df, section="shared"):
    """검수된 키워드를 lexicon의 해당 섹션에 병합"""
    lexicon = deepcopy(existing_lexicon)

    if section not in lexicon:
        lexicon[section] = {}

    for _, row in reviewed_df.iterrows():
        axis = str(row["assign_to_axis"]).strip()
        term = str(row["term"]).strip()
        polarity = str(row["polarity"]).strip().lower()

        if not axis or not term or axis == "nan":
            continue

        if axis not in lexicon[section]:
            lexicon[section][axis] = {
                "positive": [], "negative": [],
                "clip_prompt_pos": [], "clip_prompt_neg": [],
            }

        target_key = "positive" if polarity in ["positive", "pos", "p", "+"] else "negative"

        if term not in lexicon[section][axis][target_key]:
            lexicon[section][axis][target_key].append(term)

    return lexicon


def merge_into_axes(existing_axes, reviewed_df, axis_type="taste_axes"):
    """검수된 축을 axes에 병합 (새 축이 있으면 추가)"""
    axes = deepcopy(existing_axes)

    if axis_type not in axes:
        axes[axis_type] = {}

    for _, row in reviewed_df.iterrows():
        axis = str(row["assign_to_axis"]).strip()
        if not axis or axis == "nan":
            continue

        category = str(row.get("category", "")).strip()

        if axis not in axes[axis_type]:
            # 카테고리에서 group 추정
            group = "맛"
            if "식감" in category:
                group = "식감"
            elif "온도" in category:
                group = "기타"

            axes[axis_type][axis] = {"group": group}

    return axes


def save_stand(stand_data):
    """stand/ 폴더에 JSON 저장"""
    os.makedirs(STAND_DIR, exist_ok=True)

    for name, data in stand_data.items():
        path = os.path.join(STAND_DIR, name)
        with open(path, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=2)
        print(f"  ✅ {path} 저장")


def show_summary(lexicon, section="shared"):
    """병합 결과 요약"""
    sec = lexicon.get(section, {})
    for axis, info in sorted(sec.items()):
        pos = info.get("positive", [])
        neg = info.get("negative", [])
        print(f"    {axis:12s}  pos:{len(pos)}개  neg:{len(neg)}개")


def main():
    print(f"\n{'━'*50}")
    print(f"  🏗️ Stand Builder")
    print(f"{'━'*50}")

    # CSV 파일 찾기
    if len(sys.argv) > 1:
        csv_path = sys.argv[1]
    else:
        # 자동 탐색
        candidates = [f for f in os.listdir(".") if f.endswith("candidate_terms.csv")]
        if not candidates:
            print("  ❌ candidate_terms.csv 파일이 없습니다.")
            print("  → 먼저 python stand_candidate_miner.py 실행")
            print("  → 또는 stand/ 폴더의 JSON을 직접 편집")
            return
        csv_path = candidates[0]
        print(f"  CSV 발견: {csv_path}")

    # 기존 Stand 로드
    print(f"\n📂 기존 Stand 로드...")
    existing = load_existing_stand()

    # 검수된 CSV 읽기
    print(f"\n📋 검수 CSV 읽기: {csv_path}")
    reviewed = read_reviewed_csv(csv_path)
    if reviewed is None:
        return

    # 병합
    print(f"\n🔀 Stand에 병합 중...")

    # lexicon 병합
    updated_lexicon = merge_into_lexicon(
        existing.get("stand_lexicon.json", {}),
        reviewed,
        section="shared",
    )

    # axes 병합
    updated_axes = merge_into_axes(
        existing.get("stand_axes.json", {}),
        reviewed,
        axis_type="taste_axes",
    )

    # 저장
    print(f"\n💾 Stand 저장...")
    save_data = {
        "stand_axes.json": updated_axes,
        "stand_lexicon.json": updated_lexicon,
    }

    # rules는 기존 것 유지 (rules는 자동 갱신 대상 아님)
    if existing.get("stand_rules.json"):
        save_data["stand_rules.json"] = existing["stand_rules.json"]

    save_stand(save_data)

    # 요약
    print(f"\n📊 병합 결과 (shared 섹션):")
    show_summary(updated_lexicon, "shared")

    print(f"\n{'━'*50}")
    print(f"  완료! stand/ 폴더의 JSON이 갱신되었습니다.")
    print(f"  → 다음: python Food_profiler.py <키워드>")
    print(f"{'━'*50}")


if __name__ == "__main__":
    main()
