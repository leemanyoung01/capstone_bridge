"""
migrate_images_to_s3.py — 기존 review_images의 외부 URL을 S3 URL로 사후 마이그레이션
========================================================================================
⚠️ 이 스크립트는 자동 실행되지 않습니다. 사용자가 직접 명시적으로 실행할 때만 동작합니다.
⚠️ --apply 플래그가 없으면 항상 dry-run (실제 UPDATE 안 함).

동작:
  1. review_images 중 image_path(로컬 파일)가 존재하고 image_url이
     S3 URL이 아닌 행을 찾는다.
  2. (옵션) keyword 필터: --keyword 버거  → 해당 키워드의 식당 리뷰만 대상
  3. 각 행의 로컬 파일을 S3로 업로드
  4. 성공한 파일만 review_images.image_url 을 S3 URL로 UPDATE
     실패한 파일은 외부 URL 그대로 유지 (로그만 출력)
  5. 요약: 후보 수, 업로드 성공/실패, UPDATE 행 수

옵션:
  --keyword 버거       : restaurants의 keyword와 매칭 (메뉴/리뷰 본문 ILIKE 기반)
  --limit  100         : 최대 처리 개수
  --apply              : 실제 UPDATE 실행 (없으면 dry-run)
  --dry-run            : 명시적 dry-run (default와 동일)

실행 예시:
  python migrate_images_to_s3.py --keyword 버거 --limit 50          # dry-run
  python migrate_images_to_s3.py --keyword 버거 --limit 50 --apply  # 실제 UPDATE
"""

from __future__ import annotations

import argparse
import os
import sys
from typing import Optional

from db import get_conn
from s3_uploader import is_enabled, upload_if_enabled, status as s3_status

S3_PREFIX_BASE = os.environ.get("S3_PREFIX", "reviews/migrate").strip().strip("/")


def _is_s3_url(url: str) -> bool:
    return bool(url) and ("amazonaws.com" in url or "s3." in url)


def _build_key(image_path: str, keyword: Optional[str]) -> str:
    fname = os.path.basename(image_path)
    sub = keyword or "default"
    base = S3_PREFIX_BASE if S3_PREFIX_BASE else "reviews"
    return f"{base}/{sub}/{fname}"


def find_candidates(conn, keyword: Optional[str], limit: int) -> list[dict]:
    """업로드 후보 행을 SELECT. keyword가 있으면 reviews.content/menu ILIKE로 필터."""
    where_clauses = ["ri.image_path IS NOT NULL", "ri.image_path <> ''",
                     "(ri.image_url IS NULL OR ri.image_url NOT LIKE %s)"]
    params: list = ["%amazonaws.com%"]

    if keyword:
        where_clauses.append("(r.content ILIKE %s OR r.menu ILIKE %s)")
        params += [f"%{keyword}%", f"%{keyword}%"]

    sql = f"""
        SELECT ri.image_id, ri.review_id, ri.image_url, ri.image_path,
               r.restaurant_id, rst.name AS restaurant_name
        FROM review_images ri
        JOIN reviews r       ON r.review_id      = ri.review_id
        JOIN restaurants rst ON rst.restaurant_id = r.restaurant_id
        WHERE {' AND '.join(where_clauses)}
        ORDER BY ri.image_id
        LIMIT %s
    """
    params.append(limit)
    with conn.cursor() as cur:
        cur.execute(sql, params)
        return [dict(row) for row in cur.fetchall()]


def update_image_url(conn, image_id: int, new_url: str) -> int:
    with conn.cursor() as cur:
        cur.execute(
            "UPDATE review_images SET image_url = %s WHERE image_id = %s",
            (new_url, image_id),
        )
        return cur.rowcount


def main():
    parser = argparse.ArgumentParser(description="review_images 외부 URL → S3 URL 마이그레이션")
    parser.add_argument("--keyword", default=None, help="대상 키워드 (예: 버거)")
    parser.add_argument("--limit", type=int, default=100, help="최대 처리 개수 (default 100)")
    parser.add_argument("--apply", action="store_true", help="실제 UPDATE 실행")
    parser.add_argument("--dry-run", action="store_true", help="실제 UPDATE 안 함 (default)")
    args = parser.parse_args()

    apply = bool(args.apply) and not args.dry_run

    print(f"\n{'━' * 55}")
    print(f"  migrate_images_to_s3  keyword={args.keyword}  limit={args.limit}  apply={apply}")
    print(f"  S3 status: {s3_status()}")
    print(f"{'━' * 55}\n")

    if not is_enabled():
        print("❌ UPLOAD_S3 != true. 환경변수 UPLOAD_S3=true 설정 후 다시 실행하세요.")
        return 1

    with get_conn() as conn:
        rows = find_candidates(conn, args.keyword, args.limit)
        if not rows:
            print("ℹ️  처리할 후보 행이 없습니다 (이미 S3 URL이거나 image_path 없음).")
            return 0

        print(f"📋 후보 {len(rows)}개\n")
        for r in rows[:5]:
            print(f"  · image_id={r['image_id']}  rest={r['restaurant_name']}  "
                  f"path={r['image_path']}  current_url={ (r['image_url'] or '')[:50] }...")
        if len(rows) > 5:
            print(f"  ... (외 {len(rows) - 5}개)\n")

        if not apply:
            print("\n🔍 DRY-RUN: 실제 UPDATE 없이 종료합니다. --apply 로 실제 실행하세요.")
            return 0

        # ── 실제 적용 단계 ──
        success, fail, updated = 0, 0, 0
        for row in rows:
            local = row["image_path"]
            if not local or not os.path.exists(local):
                print(f"  ⏭️  skip (file missing): {local}")
                fail += 1
                continue

            key = _build_key(local, args.keyword)
            s3_url = upload_if_enabled(local, key=key)
            if not s3_url:
                print(f"  ⚠️  upload failed: image_id={row['image_id']}")
                fail += 1
                continue

            try:
                n = update_image_url(conn, row["image_id"], s3_url)
                updated += n
                success += 1
                print(f"  ✅ image_id={row['image_id']} → {s3_url[:60]}...")
            except Exception as e:
                fail += 1
                print(f"  ❌ DB UPDATE 실패 image_id={row['image_id']}: {type(e).__name__}")

        print(f"\n  요약: 업로드 성공 {success} / 실패 {fail} / DB UPDATE {updated}행")

    return 0


if __name__ == "__main__":
    sys.exit(main())
