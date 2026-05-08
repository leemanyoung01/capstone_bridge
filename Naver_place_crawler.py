"""
Naver_place_crawler.py — 리뷰 크롤러 (PostgreSQL + S3 저장)
============================================================

기능:
  - 네이버 플레이스 리뷰 크롤링
  - 리뷰/식당 정보 PostgreSQL 저장
  - CSV 백업 저장
  - 이미지 로컬 images/ 저장
  - UPLOAD_S3=true이면 이미지 S3 업로드
  - DB/CSV에는 S3 URL 우선 저장

실행:
  python Naver_place_crawler.py

필요 패키지:
  pip install requests pandas psycopg2-binary boto3 python-dotenv
"""

import os
import random
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timezone

import pandas as pd
import requests

try:
    from dotenv import load_dotenv
    load_dotenv()
except ImportError:
    pass

from db import get_conn, upsert_restaurant, insert_review
from s3_uploader import upload_if_enabled, is_enabled as s3_is_enabled, status as s3_status


# ── 정상 리뷰 이미지 도메인 화이트리스트 ─────────────────────
_NAVER_REVIEW_HOSTS = (
    "pup-review-phinf.pstatic.net",
    "ldb-phinf.pstatic.net",
    "search.pstatic.net",
)


def _is_review_image(url: str) -> bool:
    if not url:
        return False
    lo = url.lower()
    return any(h in lo for h in _NAVER_REVIEW_HOSTS)


# ── 크롤링 대상 ───────────────────────────────────────────────
restaurants = {
    "안동할매청국장": "19886347",
    "두부촌": "31234445",
    "원진노기순청국장": "11712457",
    "성북손두부": "13584925",
    "청보리" : "1465991709",
    "타종식당": "1001190962",
    "꾸지뽕민물매운탕&장단콩마을" : "37257347"
}


# ── 기본 설정 ─────────────────────────────────────────────────
DOWNLOAD_IMAGES = True
IMAGE_DIR = "images"
SAVE_CSV = True
CSV_PATH = "naver_reviews.csv"

# 테스트할 때는 50 또는 100 추천. 전체 수집하려면 None.
MAX_REVIEWS_PER_RESTAURANT = 200

DELAY_PAGE_MIN = 3.0
DELAY_PAGE_MAX = 4.5
DELAY_REST_MIN = 8.0
DELAY_REST_MAX = 12.0
MAX_IMAGE_WORKERS = 10


# ── S3 설정 (s3_uploader 모듈에서 환경변수 처리) ──────────────
UPLOAD_S3 = s3_is_enabled()
CRAWL_KEYWORD = os.environ.get("CRAWL_KEYWORD", "default").strip()
S3_BUCKET = os.environ.get("S3_BUCKET", "").strip()  # 표시용만; 실제 처리는 s3_uploader가 함
S3_PREFIX_BASE = os.environ.get("S3_PREFIX", f"reviews/{CRAWL_KEYWORD or 'default'}").strip().strip("/")


# ── GraphQL 쿼리 ──────────────────────────────────────────────
GRAPHQL_QUERY = """query getVisitorReviews($input: VisitorReviewsInput) {
  visitorReviews(input: $input) {
    items {
      id cursor reviewId rating
      author { id nickname imageUrl review { totalCount imageCount avgRating __typename } __typename }
      body thumbnail
      media { type thumbnail thumbnailRatio class videoId videoUrl trailerUrl __typename }
      tags status visitCount viewCount visited created
      reply { body created __typename }
      originType item { name code options __typename }
      language apolloCacheId translatedText businessName
      votedKeywords { code iconUrl iconCode name __typename }
      userIdno receiptInfoUrl
      reactionStat { id typeCount { name count __typename } totalCount __typename }
      nickname visitCategories { code name keywords { code name __typename } __typename }
      representativeVisitDateTime __typename
    }
    starDistribution { score count __typename }
    total __typename
  }
}"""


def get_headers(place_id: str) -> dict:
    cookie = os.environ.get("NAVER_COOKIE", "").strip()

    # .env에 NAVER_COOKIE가 설정되어 있지 않으면 아래 하드코딩된 필수값 사용
    if not cookie:
        cookie = (
            "NNB=ZUXLAEZQ2YRGA; "
            "NID_AUT=bqo3A3hTlJ/DZmMpB9tAR56FzcgrGeMJlKw1EjYIXU2zORMOpPQFL07hsR/ZX2Iy; "
            "NID_SES=AAAB4thobv/kZ4mx3wKXMD9zacHtXg5J3LED26h1q19a+/x9ab9+C4Vs88gkcbdXLTXd5FpNjVCXpp5C9G7PFlHLRbJop0fO+M2o41EB2uQYFlLvMmUt1t5Sfiq0TMthd02UqdPUyYWO4LXe0Jnd6fI5o6tyLCCGC4y9oADVoqVxNBzqOfznyPBoUzdoMRkCUldgPARqknKrPCtnN23Et3N8SU98gmcBdO9et908oCBi1pc3FByvdniS4NuNXU5AZ2gf5WdhN9sngCbEW9RcfcvHNX2ghwDaXGVZovNNdy9EgF0At/MCNWouK/BHJavBI3pch22hDWvcC3Q0ZuqcCGKenmxR2mxF6Ksclh9Xy7w4aZtimQwzwJyQQxRxYAoJnmAPqU9hyZZugYKUxEvmjsS+ntze9SbJnNe0DDsQxhhZvMU73LGyo0iCiPhpYIlVQFM7Hrgutn6T563kW/eS1tPZxdo/wTmlBA555zBMBC4NknMuP/zIR9W2TCQeW6jFthUWL+CUUjZ2aRtE/itF3sjn06WybJM+54R5eIqLLAJyaAqgkuyQiHpx255mxpA37f6BMgaRjK+GVp60CJvoieATbVrC30c+ssnEAHrZe+APxcscqf3xfWLp646qRoQPxlOgDMvflvBZ9edYXFCe0Mf7Kpk=;"
        )

    return {
        "accept": "*/*",
        "accept-language": "ko",
        "content-type": "application/json",
        "origin": "https://pcmap.place.naver.com",
        "referer": f"https://pcmap.place.naver.com/restaurant/{place_id}/review/visitor",
        "user-agent": (
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
            "AppleWebKit/537.36 (KHTML, like Gecko) "
            "Chrome/145.0.0.0 Safari/537.36"
        ),
        "cookie": cookie,
    }


# ── S3 업로드 (s3_uploader.upload_if_enabled 위임) ─────────────

def upload_image_to_s3(local_path: str) -> str:
    """
    s3_uploader.upload_if_enabled의 얇은 래퍼.
    UPLOAD_S3=false / boto3 미설치 / 업로드 실패 → "" 반환 (호출자가 fallback 사용).
    크롤러는 prefix를 keyword 단위로 쪼개기 위해 key를 직접 만들어 넘긴다.
    """
    if not local_path or not os.path.exists(local_path):
        return ""
    filename = os.path.basename(local_path)
    key = f"{S3_PREFIX_BASE}/{filename}" if S3_PREFIX_BASE else filename
    url = upload_if_enabled(local_path, key=key)
    return url or ""


# ── 이미지 다운로드 ───────────────────────────────────────────

def _download_one(url: str, save_path: str) -> bool:
    try:
        resp = requests.get(url, timeout=10)
        if resp.status_code == 200:
            os.makedirs(os.path.dirname(save_path), exist_ok=True)
            with open(save_path, "wb") as f:
                f.write(resp.content)
            return True
    except Exception:
        pass
    return False


def download_images(image_urls: list[str], place_id: str, idx: int) -> list[str]:
    if not DOWNLOAD_IMAGES or not image_urls:
        return []

    paths = []

    with ThreadPoolExecutor(max_workers=MAX_IMAGE_WORKERS) as ex:
        future_to_path = {
            ex.submit(_download_one, url, f"{IMAGE_DIR}/{place_id}_{idx}_{i}.jpg"):
            f"{IMAGE_DIR}/{place_id}_{idx}_{i}.jpg"
            for i, url in enumerate(image_urls)
        }

        for fut in as_completed(future_to_path):
            if fut.result():
                paths.append(future_to_path[fut])

    return paths


# ── 리뷰 파싱 ─────────────────────────────────────────────────

def parse_review(r: dict, place_id: str, name: str, idx: int, crawl_keyword: str = "") -> dict:
    image_urls = []
    seen_bases = set()

    def _try_add(url: str):
        if not url or not _is_review_image(url):
            return

        base = url.split("?")[0]
        if base in seen_bases:
            return

        seen_bases.add(base)
        image_urls.append(url)

    _try_add(r.get("thumbnail"))

    for m in r.get("media", []) or []:
        _try_add(m.get("thumbnail"))

    image_paths = download_images(image_urls, place_id, idx)

    image_s3_urls = []
    if UPLOAD_S3 and image_paths:
        for path in image_paths:
            s3_url = upload_image_to_s3(path)
            if s3_url:
                image_s3_urls.append(s3_url)

    # 화면/DB 표시용은 S3 URL 우선. 실패하면 원본 네이버 URL 사용.
    display_image_urls = image_s3_urls if image_s3_urls else image_urls

    voted_kws = [kw.get("name", "") for kw in r.get("votedKeywords", []) or []]

    visit_cats = []
    for cat in r.get("visitCategories", []) or []:
        kws = [k.get("name", "") for k in cat.get("keywords", []) or []]
        visit_cats.append(
            f"{cat.get('name', '')}({','.join(kws)})"
            if kws
            else cat.get("name", "")
        )

    item = r.get("item")
    tags = r.get("tags", []) or []
    reply = r.get("reply")

    return {
        "ReviewID": r.get("reviewId", r.get("id", "")),
        "Restaurant": name,
        "Review": r.get("body", ""),
        "Total": r.get("rating", 0),
        "Menu": item.get("name", "") if item else "",
        "Date": r.get("created", ""),
        "HasPicture": 1 if image_urls else 0,
        "PlaceID": place_id,
        "Author": r.get("author", {}).get("nickname", ""),
        "VisitCount": r.get("visitCount", 0),

        # 중요:
        # ImageURLs는 화면 표시용으로 S3 URL 우선 저장.
        # OriginalImageURLs는 네이버 원본 URL 백업.
        # ImagePaths는 로컬 CLIP 처리용.
        # ImageS3URLs는 S3 업로드 결과 명시 저장.
        "ImageURLs": " | ".join(display_image_urls),
        "OriginalImageURLs": " | ".join(image_urls),
        "ImagePaths": " | ".join(image_paths),
        "ImageS3URLs": " | ".join(image_s3_urls),
        "ImageCount": len(image_urls),

        "VotedKeywords": ", ".join(voted_kws),
        "VisitCategories": ", ".join(visit_cats),
        "Tags": ", ".join(tags) if isinstance(tags, list) else str(tags),
        "OwnerReply": reply.get("body", "") if reply else "",

        "CrawlKeyword": crawl_keyword,
        "CrawlTimestamp": datetime.now(timezone.utc).isoformat(),
        "IsReviewImage": bool(image_urls),
    }


# ── DB 저장 ───────────────────────────────────────────────────

def save_to_db(all_reviews: list[dict]) -> bool:
    try:
        with get_conn() as conn:
            restaurant_ids: dict[str, int] = {}

            for review in all_reviews:
                name = review["Restaurant"]
                place_id = review["PlaceID"]

                if name not in restaurant_ids:
                    rest_id = upsert_restaurant(conn, {
                        "name": name,
                        "place_id": place_id,
                        "naver_url": f"https://map.naver.com/p/entry/place/{place_id}",
                        "source": "crawler",
                    })
                    restaurant_ids[name] = rest_id

                insert_review(conn, restaurant_ids[name], review)

        print(f"  ✅ DB 저장: {len(all_reviews)}건 (식당 {len(restaurant_ids)}개)")
        return True

    except Exception as e:
        print(f"  ❌ DB 저장 실패: {e}")
        print("     → CSV 저장으로 대체됩니다")
        return False


# ── 크롤링 메인 ───────────────────────────────────────────────

def crawl_reviews(restaurants_dict: dict) -> list[dict]:
    all_reviews = []
    rest_list = list(restaurants_dict.items())

    for ri, (name, place_id) in enumerate(rest_list):
        print(f"\n{'=' * 50}")
        print(f"  [{ri + 1}/{len(rest_list)}] {name}  (ID: {place_id})")

        if MAX_REVIEWS_PER_RESTAURANT:
            print(f"  최대 {MAX_REVIEWS_PER_RESTAURANT}건 수집")

        print(f"{'=' * 50}")

        headers = get_headers(place_id)

        if not headers.get("cookie"):
            print("  ❌ NAVER_COOKIE가 비어 있습니다. .env에 NAVER_COOKIE를 넣어주세요.")
            break

        next_cursor = None
        page = 1
        idx = 0
        retry_cnt = 0

        time.sleep(random.uniform(1, 2))

        while True:
            if MAX_REVIEWS_PER_RESTAURANT:
                remaining = MAX_REVIEWS_PER_RESTAURANT - idx
                if remaining <= 0:
                    print(f"  ⏹️ 최대 리뷰 수 도달 ({MAX_REVIEWS_PER_RESTAURANT}건)")
                    break
                page_size = min(50, remaining)
            else:
                page_size = 50

            variables = {
                "businessId": str(place_id),
                "businessType": "restaurant",
                "item": "0",
                "bookingBusinessId": None,
                "size": page_size,
                "isPhotoUsed": False,
                "includeContent": True,
                "getUserStats": True,
                "includeReceiptPhotos": True,
                "getReactions": True,
                "getTrailer": True,
            }

            if next_cursor:
                variables["after"] = next_cursor

            payload = [{
                "operationName": "getVisitorReviews",
                "variables": {"input": variables},
                "query": GRAPHQL_QUERY,
            }]

            try:
                resp = requests.post(
                    "https://pcmap-api.place.naver.com/graphql",
                    json=payload,
                    headers=headers,
                    timeout=15,
                )
            except Exception as e:
                print(f"  ❌ 네트워크 오류: {e}")
                break

            if resp.status_code == 429:
                retry_cnt += 1
                if retry_cnt > 3:
                    print("  ❌ 429 차단 3회 초과 → 건너뜀")
                    break

                wait = 10 * retry_cnt + random.uniform(5, 10)
                print(f"  ⚠️ 429 → {wait:.0f}초 대기 ({retry_cnt}/3)")
                time.sleep(wait)
                continue

            if resp.status_code != 200:
                print(f"  ❌ HTTP {resp.status_code}")
                if resp.status_code in (401, 403):
                    print("     쿠키 만료 가능성이 높습니다. NAVER_COOKIE를 새로 복사하세요.")
                break

            retry_cnt = 0

            try:
                data = resp.json()
                reviews = data[0]["data"]["visitorReviews"]["items"]
                total = data[0]["data"]["visitorReviews"].get("total", "?")
            except Exception:
                print("  ❌ 응답 파싱 실패")
                break

            if not reviews:
                print("  ✅ 완료 (더 이상 리뷰 없음)")
                break

            img_cnt = 0
            crawl_kw = os.environ.get("CRAWL_KEYWORD", "")

            for r in reviews:
                parsed = parse_review(r, place_id, name, idx, crawl_keyword=crawl_kw)
                all_reviews.append(parsed)

                if parsed["HasPicture"]:
                    img_cnt += 1

                idx += 1

            print(f"  📄 페이지 {page}: {len(reviews)}건 (📷{img_cnt}) | 누적: {idx}/{total}")

            next_cursor = reviews[-1].get("cursor")
            if not next_cursor:
                break

            page += 1
            time.sleep(random.uniform(DELAY_PAGE_MIN, DELAY_PAGE_MAX))

        print(f"  → {name}: {idx}건 수집 완료")

        if ri < len(rest_list) - 1:
            wait = random.uniform(DELAY_REST_MIN, DELAY_REST_MAX)
            print(f"  ⏳ 다음 식당 전 {wait:.1f}초 대기...")
            time.sleep(wait)

    return all_reviews


# ── 진입점 ────────────────────────────────────────────────────

if __name__ == "__main__":
    print("🔍 네이버 플레이스 크롤러 (PostgreSQL + S3 연동)")
    print(f"   대상: {len(restaurants)}개 식당")
    print(f"   식당당 최대: {MAX_REVIEWS_PER_RESTAURANT if MAX_REVIEWS_PER_RESTAURANT else '무제한'}건")
    print(f"   이미지 다운로드: {'ON' if DOWNLOAD_IMAGES else 'OFF'}")
    print(f"   S3 업로드: {'ON' if UPLOAD_S3 else 'OFF'}")

    if UPLOAD_S3:
        print(f"   S3 버킷: {S3_BUCKET or '(없음)'}")
        print(f"   S3 prefix: {S3_PREFIX_BASE}")
        print(f"   S3 status: {s3_status()}")

    if not get_headers("test").get("cookie"):
        print("\n❌ .env에 NAVER_COOKIE가 없습니다.")
        print("   예: NAVER_COOKIE='NAC=...; NNB=...; ...'")
    else:
        reviews = crawl_reviews(restaurants)

        if reviews:
            db_ok = save_to_db(reviews)

            if SAVE_CSV or not db_ok:
                df = pd.DataFrame(reviews)
                df.to_csv(CSV_PATH, index=False, encoding="utf-8-sig")

                total = len(df)
                img = int(df["HasPicture"].sum()) if "HasPicture" in df.columns else 0
                s3_count = int(df["ImageS3URLs"].astype(str).str.len().gt(0).sum()) if "ImageS3URLs" in df.columns else 0

                print(f"\n  ✅ CSV: {CSV_PATH}  ({total}건, 이미지 {img}건 {img / total * 100:.1f}%)")
                print(f"  ✅ S3 URL 포함 리뷰: {s3_count}건")
        else:
            print("\n❌ 수집 실패 → 쿠키를 새로 복사하거나 5분 후 재시도")