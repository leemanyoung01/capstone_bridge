"""
Naver_place_crawler_text_only.py — 네이버 리뷰 텍스트 전용 크롤러
==============================================================

목적:
  - 기존 restaurants / reviews / review_images 데이터는 삭제하거나 덮어쓰지 않음
  - 새로 수집되는 리뷰는 텍스트만 DB에 저장
  - 이미지 다운로드 X
  - S3 업로드 X
  - review_images에 새 이미지 row 추가 X
  - Food_profiler 텍스트 벡터 보강용

실행 예:
  python Naver_place_crawler_text_only.py --keyword 떡볶이 --limit 1000
  python Naver_place_crawler_text_only.py 떡볶이 --limit 1000

실행 후:
  python Food_profiler.py 떡볶이 --debug
"""

import argparse
import os
import random
import time
from datetime import datetime, timezone

import pandas as pd
import requests

try:
    from dotenv import load_dotenv
    load_dotenv()
except ImportError:
    pass

from db import get_conn, upsert_restaurant, insert_review


# ─────────────────────────────────────────────
# 크롤링 대상
# 필요할 때 keyword별로 이 dict만 바꿔서 사용
# ─────────────────────────────────────────────
restaurants = {
    "가부 보문점": "1023504775",
    "경신반점": "1434029205",
    "공푸 성신여대본점": "35168517",
    "더웍": "1385218273",
    "로터스가든": "1231327622",
    "만리향 안암동2가": "37425678",
    "만추 현대백화점 미아점": "1570187531",
    "수저가": "1644340985",
    "일류반점": "1613060605",
    "임금님자장면": "18316143",
    "직화짬뽕전문점 장위점": "1807461104",
    "짬뽕선수": "1353004075",
    "청해루": "1808727757",
}


# ─────────────────────────────────────────────
# 기본 설정
# ─────────────────────────────────────────────
TEXT_ONLY_CRAWL = True

# text-only에서는 이미지 다운로드/S3 업로드를 절대 하지 않음
DOWNLOAD_IMAGES = False
UPLOAD_S3 = False

SAVE_CSV = True
CSV_PATH = "naver_reviews_text_only.csv"

# 식당당 최대 리뷰 수. None이면 가능한 끝까지.
DELAY_PAGE_MIN = 0.7
DELAY_PAGE_MAX = 1.2
DELAY_REST_MIN = 2.0
DELAY_REST_MAX = 3.5
MAX_REVIEWS_PER_RESTAURANT = 2000


CRAWL_KEYWORD = os.environ.get("CRAWL_KEYWORD", "default").strip()


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


def parse_cli_and_env() -> argparse.Namespace:
    global CRAWL_KEYWORD, MAX_REVIEWS_PER_RESTAURANT

    parser = argparse.ArgumentParser(description="네이버 플레이스 리뷰 텍스트 전용 크롤러")
    parser.add_argument(
        "keyword_positional",
        nargs="?",
        default=None,
        help="크롤 keyword. 예: python Naver_place_crawler_text_only.py 떡볶이",
    )
    parser.add_argument(
        "--keyword",
        default=None,
        help="크롤 keyword. .env의 CRAWL_KEYWORD보다 우선.",
    )
    parser.add_argument(
        "--limit",
        type=int,
        default=None,
        help="식당당 최대 리뷰 수. None이면 코드 기본값 사용.",
    )

    args = parser.parse_args()

    kw_arg = args.keyword or args.keyword_positional
    if kw_arg:
        kw = kw_arg.strip()
        CRAWL_KEYWORD = kw
        os.environ["CRAWL_KEYWORD"] = kw

    if args.limit is not None:
        MAX_REVIEWS_PER_RESTAURANT = args.limit

    return args


def get_headers(place_id: str) -> dict:
    cookie = os.environ.get("NAVER_COOKIE", "").strip()

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
        "cookie": '''NAC=YL19BMQKiMhK; NNB=YFLRAYGZENQWM; NFS=2; NID_AUT=8rpJgQ97MpUohDOG1qAhmtuzKrCUYHFp5kLEYoHA3uX9+FBJrymzIMXBk77SUIyj; cto_bundle=DOpvjF9rT0t6MWxGYXElMkZma2RpajFHM0paUGxFJTJGTDd2TlJVUUdYUnFUJTJGUHpwWUpValJYR3pWOTRGVEtCYTdtSHJhTmt3WHFZcFhmVTViWHExMVlVMjdOJTJCbGUzSXhOSXBnc3ZlaUppb05saEZqaFk3WkhNckxKVWszbjNUZGNOJTJCcTU1TDROdXFQViUyRkwxeG1IUDBzQlJkMDhYeXclM0QlM0Q; bnb_tooltip_shown_finance_v1=true; ASID=dfc29f200000019cf58edbe60000001d; NV_WETR_LOCATION_RGN_M="MDkyOTAxMTQ="; NV_WETR_LAST_ACCESS_RGN_M="MDkyOTAxMTQ="; PLACE_LANGUAGE=ko; tooltipDisplayed=true; NACT=1; SRT30=1777304956; SRT5=1777304956; _naver_usersession_=CZHiouVFBPUxebCN4eKHL6Id; page_uid=jP+aQwqpsW7sk9GosUN-072220; NID_SES=AAABoh/WW4HoAcJYpPM/yKl7kBfcGRvyYgA9rZgWCv472b989BoH/CvM2tZ/KBO4GfJNd1HOgcUSfr5e13dwxGPznWi9eK05zRXloyPjNY1LW4BUMV6Vu6gI9qgzA8y9CxG82h3kUwxe2xKqOQAAT8ElRd+NJY7V3nUqPA633ZQ7sF0pmoa2bTc+38V5FHvWVLha7p33njLBeBI3BgxNmO16d0R2ZQ6yN1CnAUelUSK2yfuUcoc1H0YPgURJYVysqddvfVnzd9bdXZwz1ySU9gFqUOUfDftu5d8Wb1q03ZnsAkK28vvVy5y/AEZbwElMeWQFIrIdlIh3nQFVQ1PYvV8QjnWfok93hfGRtJbxYHEo7r+x53sayMClOY9/4pPilEt87aDxpBLQoJsAeI4ty1y5w/MFzg+Iv7s8LNPiOXs2XSQdXzhrNiYVuIwh5ykAT0CDU8H7LtLBAWgfF9h9nKvomvgH9GXxv+4VvpcGFlx2OLAkn2uXU/eb1QigM+FtkmNzYYnTU8/Ghg6+ARU5Q24DM3+kZWiR1vQ7n12O+cTTu/RC4+9FmwQL8vzKMJp8cr1jMg==; BUC=fbLEIlEQKqFtaIVmvuP7k33xu0eAu_ypR7TxwFOP-7Q=''',
    }


def sanitize_value(value):
    """PostgreSQL TEXT 컬럼에 들어가면 안 되는 NUL 문자 제거."""
    if isinstance(value, str):
        return value.replace("\x00", "")
    if isinstance(value, dict):
        return {k: sanitize_value(v) for k, v in value.items()}
    if isinstance(value, list):
        return [sanitize_value(v) for v in value]
    if isinstance(value, tuple):
        return tuple(sanitize_value(v) for v in value)
    return value


def parse_review_text_only(
    r: dict,
    place_id: str,
    name: str,
    idx: int,
    crawl_keyword: str = "",
) -> dict:
    """
    이미지 관련 필드를 모두 비우는 text-only parse.
    insert_review()가 ImageURLs가 비어 있으면 review_images를 추가하지 않는다.
    """
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

    record = {
        "ReviewID": r.get("reviewId", r.get("id", "")),
        "Restaurant": name,
        "Review": r.get("body", ""),
        "Total": r.get("rating", 0),
        "Menu": item.get("name", "") if item else "",
        "Date": r.get("created", ""),
        "HasPicture": 0,
        "PlaceID": place_id,
        "Author": r.get("author", {}).get("nickname", ""),
        "VisitCount": r.get("visitCount", 0),

        # text-only 핵심: 이미지 관련 필드 완전 비움
        "ImageURLs": "",
        "OriginalImageURLs": "",
        "ImagePaths": "",
        "ImageS3URLs": "",
        "ImageCount": 0,

        "VotedKeywords": ", ".join(voted_kws),
        "VisitCategories": ", ".join(visit_cats),
        "Tags": ", ".join(tags) if isinstance(tags, list) else str(tags),
        "OwnerReply": reply.get("body", "") if reply else "",

        "CrawlKeyword": crawl_keyword,
        "CrawlTimestamp": datetime.now(timezone.utc).isoformat(),
        "IsReviewImage": False,
    }

    return sanitize_value(record)


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
                        "source": "crawler_text_only",
                    })
                    restaurant_ids[name] = rest_id

                insert_review(conn, restaurant_ids[name], review)

        print(f"  DB 저장 완료: {len(all_reviews)}건 (식당 {len(restaurant_ids)}개)")
        return True

    except Exception as e:
        print(f"  DB 저장 실패: {type(e).__name__}: {e}")
        print("  CSV 저장으로 대체됩니다.")
        return False


def crawl_reviews(restaurants_dict: dict) -> list[dict]:
    all_reviews = []
    rest_list = list(restaurants_dict.items())

    for ri, (name, place_id) in enumerate(rest_list):
        print(f"\n{'=' * 50}")
        print(f"  [{ri + 1}/{len(rest_list)}] {name}  (ID: {place_id})")
        print(f"  최대 리뷰 수: {MAX_REVIEWS_PER_RESTAURANT if MAX_REVIEWS_PER_RESTAURANT else '무제한'}")
        print(f"{'=' * 50}")

        headers = get_headers(place_id)

        if not headers.get("cookie"):
            print("  NAVER_COOKIE가 비어 있습니다. .env에 NAVER_COOKIE를 넣어주세요.")
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
                    print(f"  최대 리뷰 수 도달 ({MAX_REVIEWS_PER_RESTAURANT}건)")
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

                # 사진 리뷰만 필터링하지 않음.
                # 단 parse 단계에서 이미지 URL은 저장하지 않는다.
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
                print(f"  네트워크 오류: {type(e).__name__}: {e}")
                break

            if resp.status_code == 429:
                retry_cnt += 1
                if retry_cnt > 3:
                    print("  429 차단 3회 초과 → 건너뜀")
                    break

                wait = 10 * retry_cnt + random.uniform(5, 10)
                print(f"  429 → {wait:.0f}초 대기 ({retry_cnt}/3)")
                time.sleep(wait)
                continue

            if resp.status_code != 200:
                print(f"  HTTP {resp.status_code}")
                if resp.status_code in (401, 403):
                    print("  쿠키 만료 가능성이 높습니다. NAVER_COOKIE를 새로 복사하세요.")
                break

            retry_cnt = 0

            try:
                data = resp.json()
                reviews = data[0]["data"]["visitorReviews"]["items"]
                total = data[0]["data"]["visitorReviews"].get("total", "?")
            except Exception:
                print("  응답 파싱 실패")
                break

            if not reviews:
                print("  완료: 더 이상 리뷰 없음")
                break

            crawl_kw = os.environ.get("CRAWL_KEYWORD", "")

            for r in reviews:
                parsed = parse_review_text_only(
                    r,
                    place_id,
                    name,
                    idx,
                    crawl_keyword=crawl_kw,
                )
                all_reviews.append(parsed)
                idx += 1

            print(f"  페이지 {page}: {len(reviews)}건 | 누적: {idx}/{total}")

            next_cursor = reviews[-1].get("cursor")
            if not next_cursor:
                break

            page += 1
            time.sleep(random.uniform(DELAY_PAGE_MIN, DELAY_PAGE_MAX))

        print(f"  → {name}: {idx}건 수집 완료")

        if ri < len(rest_list) - 1:
            wait = random.uniform(DELAY_REST_MIN, DELAY_REST_MAX)
            print(f"  다음 식당 전 {wait:.1f}초 대기...")
            time.sleep(wait)

    return all_reviews


if __name__ == "__main__":
    parse_cli_and_env()

    print("네이버 플레이스 텍스트 전용 크롤러")
    print(f"  crawl_keyword: {CRAWL_KEYWORD or '(없음)'}")
    print(f"  대상 식당: {len(restaurants)}개")
    print(f"  식당당 최대: {MAX_REVIEWS_PER_RESTAURANT if MAX_REVIEWS_PER_RESTAURANT else '무제한'}건")
    print("  이미지 다운로드: OFF")
    print("  S3 업로드: OFF")
    print("  review_images 신규 저장: OFF")

    if not get_headers("test").get("cookie"):
        print("\n.env에 NAVER_COOKIE가 없습니다.")
        print("예: NAVER_COOKIE='NAC=...; NNB=...; ...'")
        raise SystemExit(1)

    reviews = crawl_reviews(restaurants)

    if reviews:
        db_ok = save_to_db(reviews)

        if SAVE_CSV or not db_ok:
            df = pd.DataFrame(reviews)
            df.to_csv(CSV_PATH, index=False, encoding="utf-8-sig")

            total = len(df)
            print(f"\nCSV 저장: {CSV_PATH} ({total}건)")
            print("이미지 포함 리뷰 수: 0건")
            print("S3 URL 포함 리뷰 수: 0건")
    else:
        print("\n수집 실패 또는 수집된 리뷰 없음")