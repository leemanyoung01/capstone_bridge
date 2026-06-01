"""
restore_all.py — CSV 백업들을 한 번에 DB로 복구 (+ Food_profiler + late fusion)
────────────────────────────────────────────────────────────────
naver_reviews_<keyword>_<ts>.csv 들을 스캔해서, DB에 덜 들어간 키워드만
자동으로 골라 복구한다. NUL(0x00)은 db.insert_review가 자동 제거.

text_augment_pipeline 의 검증된 stage 함수를 그대로 재사용한다.

사용:
  python restore_all.py                  # DB에 덜 들어간 키워드만 자동 복구
  python restore_all.py 회 파스타 버거    # 지정 키워드만
  python restore_all.py --force-all      # CSV 있는 전 키워드 강제 복구
  python restore_all.py --db-only        # CSV→DB 복구만 (프로파일/fusion 스킵)
  python restore_all.py --skip-fusion    # fusion만 스킵
"""
from __future__ import annotations

import argparse
import glob
import os
import re

import pandas as pd

from db import get_conn
import text_augment_pipeline as tap

CSV_RE = re.compile(r"naver_reviews_(.+)_\d{8}_\d{6}\.csv$")


def find_latest_csvs() -> dict[str, str]:
    """키워드별 가장 최근 CSV 1개만."""
    csvs: dict[str, str] = {}
    for path in glob.glob("naver_reviews_*.csv"):
        m = CSV_RE.match(os.path.basename(path))
        if not m:
            continue
        kw = m.group(1)
        if kw not in csvs or path > csvs[kw]:
            csvs[kw] = path
    return csvs


def db_counts() -> dict[str, int]:
    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(
                "SELECT crawl_keyword, COUNT(*) AS n FROM reviews "
                "WHERE crawl_keyword IS NOT NULL GROUP BY crawl_keyword"
            )
            return {r["crawl_keyword"]: r["n"] for r in cur.fetchall()}


def main():
    ap = argparse.ArgumentParser(description="CSV 백업 일괄 DB 복구")
    ap.add_argument("keywords", nargs="*", help="대상 키워드 (생략시 자동 판별)")
    ap.add_argument("--force-all", action="store_true",
                    help="이미 DB에 있어도 CSV 있는 전 키워드 복구")
    ap.add_argument("--db-only", action="store_true",
                    help="CSV→DB 복구만 (Food_profiler/fusion 스킵)")
    ap.add_argument("--skip-fusion", action="store_true",
                    help="fusion 단계만 스킵")
    ap.add_argument("--threshold", type=float, default=0.95,
                    help="자동 판별 시 DB/CSV 비율이 이 값 미만이면 복구 대상")
    args = ap.parse_args()

    csvs = find_latest_csvs()
    if not csvs:
        print("❌ naver_reviews_*.csv 파일이 없습니다.")
        return
    counts = db_counts()

    if args.keywords:
        targets = [k for k in args.keywords if k in csvs]
        missing = set(args.keywords) - set(csvs)
        if missing:
            print(f"⚠️ CSV 없는 키워드 무시: {sorted(missing)}")
    elif args.force_all:
        targets = sorted(csvs)
    else:
        targets = []
        for kw, path in sorted(csvs.items()):
            csv_n = len(pd.read_csv(path, dtype=str, keep_default_na=False))
            if counts.get(kw, 0) < csv_n * args.threshold:
                targets.append(kw)

    if not targets:
        print("✅ 복구할 키워드 없음 (전부 DB에 충분히 들어가 있음)")
        return

    print(f"대상 {len(targets)}개: {', '.join(targets)}")
    done, failed = [], []
    for i, kw in enumerate(targets, 1):
        print(f"\n\n########## [{i}/{len(targets)}] restore '{kw}'  ({csvs[kw]}) ##########")
        try:
            tap.stage_restore_csv(csvs[kw])
            if not args.db_only:
                profiles, _, _ = tap.stage_profile(kw)
                if not args.skip_fusion:
                    tap.stage_fusion(kw, profiles)
            done.append(kw)
        except SystemExit as e:        # stage_* 내부 sys.exit 방어
            print(f"  ❌ '{kw}' 중단: {e}")
            failed.append(kw)
        except Exception as e:
            print(f"  ❌ '{kw}' 오류: {e}")
            failed.append(kw)

    print(f"\n{'━'*55}")
    print(f"  복구 완료 — 성공 {len(done)} / 실패 {len(failed)}")
    if done:
        print(f"    ✅ {', '.join(done)}")
    if failed:
        print(f"    ❌ {', '.join(failed)}")
    print(f"  다음: python semantic_text_profiler.py --all")
    print(f"{'━'*55}")


if __name__ == "__main__":
    main()
