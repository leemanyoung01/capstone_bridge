"""
csv_to_json.py — 기존 CSV → PostgreSQL 적재
=============================================
크롤러 없이 기존 naver_reviews.csv가 있을 때 DB에 올리는 용도.
크롤러를 새로 돌리면 이 스크립트는 불필요.

실행:
  python csv_to_json.py                        # 현재 폴더의 모든 리뷰 CSV
  python csv_to_json.py --file naver_reviews.csv
  python csv_to_json.py --dry-run              # 실제 저장 없이 파싱만 확인
"""

import argparse, glob, os, sys
import pandas as pd
from db import get_conn, upsert_restaurant, insert_review

# CSV 컬럼 → 내부 키 매핑
# ★ "Date"는 변환하지 않음 → insert_review의 _parse_date()가 처리
COLUMN_MAP = {
    "ReviewID":      "review_id",
    "Restaurant":    "restaurant",
    "Review":        "content",
    "Total":         "rating",
    "Menu":          "menu",
    # "Date" 는 그대로 유지 — insert_review에서 _parse_date()로 처리
    "HasPicture":    "has_image",
    "PlaceID":       "place_id",
    "Author":        "author",
    "VisitCount":    "visit_count",
    "ImageURLs":     "image_urls",
    "ImagePaths":    "image_paths",
    "VotedKeywords": "voted_keywords",
    "OwnerReply":    "owner_reply",
}


def load_csvs(pattern="*.csv", specific=None) -> pd.DataFrame:
    files = [specific] if specific else sorted(glob.glob(pattern))
    # 벡터/사전 CSV 제외
    files = [f for f in files if not any(
        x in f for x in ["_dictionary", "_vectors", "_axes", "candidate", "summary"])]
    frames = []
    for f in files:
        try:
            df = pd.read_csv(f, encoding="utf-8-sig")
            if "Review" in df.columns or "content" in df.columns:
                frames.append(df)
                print(f"  ✓ {f}: {len(df)}건")
        except Exception as e:
            print(f"  ❌ {f}: {e}")
    if not frames:
        return pd.DataFrame()

    df = pd.concat(frames, ignore_index=True)
    df = df.rename(columns={k: v for k, v in COLUMN_MAP.items() if k in df.columns})

    # 필수 컬럼 보완
    defaults = {
        "rating": 0, "visit_count": 0, "has_image": 0,
        "place_id": "", "image_urls": "", "image_paths": "",
        "voted_keywords": "", "owner_reply": "", "menu": "", "author": "",
    }
    for col, default in defaults.items():
        if col not in df.columns:
            df[col] = default

    df["rating"]      = pd.to_numeric(df["rating"],      errors="coerce").fillna(0)
    df["visit_count"] = pd.to_numeric(df["visit_count"], errors="coerce").fillna(0).astype(int)
    df["has_image"]   = df["has_image"].astype(bool)

    # 중복 제거
    if "review_id" in df.columns and df["review_id"].nunique() > 1:
        df = df.drop_duplicates(subset=["review_id"], keep="first")
    elif "restaurant" in df.columns and "content" in df.columns:
        df = df.drop_duplicates(subset=["restaurant", "content"], keep="first")

    rest_count = df["restaurant"].nunique() if "restaurant" in df.columns else "?"
    print(f"\n  전체 {len(df)}건 | 식당 {rest_count}개")
    return df


def save_to_db(df: pd.DataFrame, dry_run=False):
    rest_col = "restaurant" if "restaurant" in df.columns else "Restaurant"
    pid_col  = "place_id"   if "place_id"   in df.columns else "PlaceID"

    n_rest = n_rev = n_skip = 0
    rest_ids: dict[str, int] = {}

    if dry_run:
        print(f"  [DRY-RUN] {len(df)}건 파싱 완료. 실제 저장 없음.")
        return

    with get_conn() as conn:
        for _, row in df.iterrows():
            name     = str(row.get(rest_col, "")).strip()
            place_id = str(row.get(pid_col, "")).strip()
            if not name:
                continue

            if name not in rest_ids:
                rid = upsert_restaurant(conn, {
                    "name":      name,
                    "place_id":  place_id,
                    "naver_url": f"https://map.naver.com/p/entry/place/{place_id}" if place_id else "",
                    "source":    "csv",
                })
                rest_ids[name] = rid
                n_rest += 1

            # row.to_dict() 그대로 전달 — insert_review가 Date/reviewed_at 모두 처리
            result = insert_review(conn, rest_ids[name], row.to_dict())
            if result is None:
                n_skip += 1
            else:
                n_rev += 1

    print(f"  ✅ 식당 {n_rest}개 | 리뷰 {n_rev}건 저장 | {n_skip}건 중복 스킵")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="CSV → PostgreSQL 적재")
    parser.add_argument("--file",    "-f", default=None)
    parser.add_argument("--pattern", "-p", default="*.csv")
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()

    print(f"\n{'━'*50}")
    print(f"  CSV → PostgreSQL 적재{'  [DRY-RUN]' if args.dry_run else ''}")
    print(f"{'━'*50}")

    print(f"\n📂 CSV 로드...")
    df = load_csvs(pattern=args.pattern, specific=args.file)
    if df.empty:
        print("  처리할 데이터 없음")
        sys.exit(1)

    print(f"\n💾 DB 저장...")
    save_to_db(df, dry_run=args.dry_run)
    print(f"\n완료!")