"""
text_augment_pipeline.py
────────────────────────────────────────────────────────────────
기존 식당의 텍스트 리뷰만 추가 수집해서 evidence 밀도와 text_vector를 보강한다.
이미지 다운로드/CLIP 추론은 스킵하고, 마지막에 late fusion만 다시 돌려 fused_vector를 갱신.

기존 Naver_place_crawler / Food_profiler / Multimodal_profiler 파일은 무수정.
monkeypatch + 모듈 함수 직접 호출 방식.

사용 예:
    python text_augment_pipeline.py 닭         # 전체 식당 텍스트 보강
    python text_augment_pipeline.py 닭 --limit 100
    python text_augment_pipeline.py 닭 --restaurants "삼성통닭 본점,금산닭집"
    python text_augment_pipeline.py 닭 --skip-crawl   # 크롤은 이미 됐고 프로파일러만
    python text_augment_pipeline.py 닭 --skip-fusion  # fused_vector 갱신 스킵
"""
from __future__ import annotations

import argparse
import os
import sys
from datetime import datetime

import pandas as pd

import Naver_place_crawler as crawler
import Food_profiler as fp
import Multimodal_profiler as mp
from db import get_conn, get_profiles_by_keyword


def stage_crawl(keyword: str, restaurants: dict, limit: int | None) -> int:
    """텍스트 only 크롤. DOWNLOAD_IMAGES/UPLOAD_S3을 모듈 globals에서 끔."""
    print(f"\n{'=' * 60}\n  STAGE 1: 텍스트 only 크롤  '{keyword}'\n{'=' * 60}")

    # monkeypatch: 이미지 다운로드 + S3 업로드 비활성화
    crawler.DOWNLOAD_IMAGES = False
    crawler.UPLOAD_S3 = False
    if limit is not None:
        # --limit 0 또는 음수 → 무제한 (None 처리)
        crawler.MAX_REVIEWS_PER_RESTAURANT = None if limit <= 0 else limit

    os.environ["CRAWL_KEYWORD"] = keyword
    if not os.environ.get("S3_PREFIX"):
        crawler.S3_PREFIX_BASE = f"reviews/{keyword}"
    crawler.CRAWL_KEYWORD = keyword

    print(f"   대상 식당: {len(restaurants)}개")
    print(f"   식당당 최대: {crawler.MAX_REVIEWS_PER_RESTAURANT or '무제한'}건")
    print(f"   이미지 다운로드: OFF | S3 업로드: OFF (URL 메타데이터만 DB 저장)")

    if not crawler.get_headers("test").get("cookie"):
        sys.exit("❌ .env에 NAVER_COOKIE가 없습니다.")

    reviews = crawler.crawl_reviews(restaurants)
    if not reviews:
        print("❌ 수집된 리뷰 없음")
        return 0

    # ── 안전망: DB save 시도 전에 CSV 백업 먼저 ──
    # 19025건 NUL 사고 재발 방지. DB가 실패해도 CSV는 남게.
    ts = datetime.now().strftime("%Y%m%d_%H%M%S")
    backup_csv = f"naver_reviews_{keyword}_{ts}.csv"
    try:
        pd.DataFrame(reviews).to_csv(backup_csv, index=False, encoding="utf-8-sig")
        print(f"  💾 CSV 백업: {backup_csv} ({len(reviews)}건)")
    except Exception as e:
        print(f"  ⚠️ CSV 백업 실패: {e}")

    db_ok = crawler.save_to_db(reviews)
    if not db_ok:
        print(f"  ⚠️ DB 저장 실패했지만 CSV는 살아있음 → {backup_csv}")
        print(f"     복구: python text_augment_pipeline.py {keyword} --restore-csv {backup_csv} --skip-crawl")
    return len(reviews)


def stage_restore_csv(csv_path: str) -> int:
    """CSV 백업 파일에서 DB로 복구. NUL bytes는 db.insert_review가 자동 제거."""
    print(f"\n{'=' * 60}\n  STAGE 0: CSV → DB 복구  {csv_path}\n{'=' * 60}")
    if not os.path.exists(csv_path):
        sys.exit(f"❌ CSV 파일 없음: {csv_path}")

    df = pd.read_csv(csv_path, encoding="utf-8-sig", dtype=str, keep_default_na=False)
    print(f"   CSV 로드: {len(df)}건")

    reviews = df.to_dict(orient="records")
    db_ok = crawler.save_to_db(reviews)
    if not db_ok:
        sys.exit("❌ DB 복구 실패 (NUL sanitizer가 적용됐는데도 실패 — 다른 원인)")
    return len(reviews)


def stage_profile(keyword: str) -> tuple[dict, dict, dict]:
    """Food_profiler 재실행. text_vector + evidence_json 갱신."""
    print(f"\n{'=' * 60}\n  STAGE 2: Food_profiler 재실행  '{keyword}'\n{'=' * 60}")

    taste_axes, meta_axes = fp.compose_axes(keyword)
    all_axes = {**taste_axes, **meta_axes}
    print(f"   맛축 {len(taste_axes)}개 | 메타축 {len(meta_axes)}개")

    stand = fp.load_stand()
    rules = stand["rules"] if stand else {"scoring": {"positive_hit": 1.0, "negative_hit": -0.5}}

    df = fp.load_from_db(keyword)
    if df.empty:
        sys.exit(f"❌ '{keyword}' 리뷰가 DB에 없음 (크롤 먼저 돌리세요)")
    print(f"   DB 리뷰: {len(df)}건")

    profiles, _ = fp.build_profiles(df, keyword, taste_axes, meta_axes, rules,
                                    collect_evidence=True)
    print(f"   프로파일: {len(profiles)}개 식당")

    fp.save_to_db(keyword, profiles, all_axes, taste_axes, meta_axes, df,
                  source_is_csv=False)
    return profiles, taste_axes, meta_axes


def stage_fusion(keyword: str, profiles: dict) -> None:
    """CLIP 스킵. 기존 image_vector + 새 text_vector로 late fusion만."""
    print(f"\n{'=' * 60}\n  STAGE 3: Late Fusion (CLIP 스킵)  '{keyword}'\n{'=' * 60}")

    text_vecs = {name: p["normalized"] for name, p in profiles.items()}

    with get_conn() as conn:
        rows = get_profiles_by_keyword(conn, keyword)
    img_vecs = {r["name"]: (r.get("image_vector") or {}) for r in rows}

    n_with_img = sum(1 for v in img_vecs.values() if v)
    print(f"   기존 image_vector 보유 식당: {n_with_img}/{len(img_vecs)}")

    fused, weights = mp.late_fusion_dynamic(text_vecs, img_vecs, dynamic=True)
    print(f"   fused_vector 생성: {len(fused)}개 (이미지 보정 적용 {n_with_img}개)")

    # rep_images는 비우고 update_rep_images=False로 기존 대표이미지 유지
    mp.save_to_db(keyword, fused, text_vecs, img_vecs, [], weights, mode="clip",
                  update_rep_images=False)
    print(f"   ✅ fused_vector 갱신 완료 (representative_images 미변경)")


def _resolve_restaurants(filter_names: list[str] | None) -> dict:
    """크롤러의 restaurants dict에서 선택. None이면 전체."""
    base = crawler.restaurants
    if not filter_names:
        return base
    want = {n.strip() for n in filter_names if n.strip()}
    picked = {k: v for k, v in base.items() if k in want}
    missing = want - set(picked.keys())
    if missing:
        print(f"⚠️  Naver_place_crawler.restaurants에 없는 식당 무시: {missing}")
    if not picked:
        sys.exit("❌ 선택된 식당이 없음")
    return picked


def main():
    parser = argparse.ArgumentParser(
        description="텍스트 only 크롤 → Food_profiler → Late fusion 보강 파이프라인")
    parser.add_argument("keyword", help="크롤/프로파일 키워드 (예: 닭, 족발)")
    parser.add_argument("--restaurants", default=None,
                        help="콤마구분 식당명 리스트 (생략시 crawler.restaurants 전체)")
    parser.add_argument("--limit", type=int, default=None,
                        help="식당당 최대 리뷰. 0 또는 음수 = 무제한. 생략시 crawler 기본값(200) 유지")
    parser.add_argument("--skip-crawl", action="store_true",
                        help="크롤 스킵 (이미 DB에 텍스트가 있을 때)")
    parser.add_argument("--skip-fusion", action="store_true",
                        help="fused_vector 갱신 스킵 (text_vector + evidence만 갱신)")
    parser.add_argument("--restore-csv", default=None,
                        help="CSV 백업 파일에서 DB로 복구 (크롤 대신). 예: naver_reviews_버거_20260101_120000.csv")
    args = parser.parse_args()

    filter_names = args.restaurants.split(",") if args.restaurants else None

    if args.restore_csv:
        n = stage_restore_csv(args.restore_csv)
        print(f"\n   → {n}건 CSV→DB 복구 시도 (중복은 ON CONFLICT로 자동 스킵)")
    elif not args.skip_crawl:
        restaurants = _resolve_restaurants(filter_names)
        n = stage_crawl(args.keyword, restaurants, args.limit)
        print(f"\n   → {n}건 수집 (중복은 ON CONFLICT로 자동 스킵)")
    else:
        print("⏭️  STAGE 1 (크롤) 스킵")

    profiles, _, _ = stage_profile(args.keyword)

    if not args.skip_fusion:
        stage_fusion(args.keyword, profiles)
    else:
        print("⏭️  STAGE 3 (fusion) 스킵 — fused_vector는 갱신되지 않음")

    print(f"\n{'━' * 60}")
    print(f"  완료. app.py의 ALL_DATA 캐시 무효화를 위해 백엔드 재시작 권장.")
    print(f"{'━' * 60}")


if __name__ == "__main__":
    main()
