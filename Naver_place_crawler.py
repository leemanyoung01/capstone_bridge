"""
Naver_place_crawler.py — 리뷰 크롤러 (PostgreSQL 직접 저장)
============================================================
변경점:
  - 크롤링 결과를 CSV와 DB 양쪽에 저장
  - DB 저장 실패 시 CSV로만 저장 (폴백)
  - DOWNLOAD_IMAGES=True 시 이미지도 병렬 다운로드
  - MAX_REVIEWS_PER_RESTAURANT: 식당당 최대 리뷰 수 제한
  - 이미지 중복 저장 버그 수정 (URL 쿼리 파라미터 기준 중복 제거)
  - 페이지당 50개로 변경

실행: python Naver_place_crawler.py
설치: pip install requests pandas psycopg2-binary
"""

import requests
import pandas as pd
import time, os, random, json
from datetime import datetime
from concurrent.futures import ThreadPoolExecutor, as_completed

from db import get_conn, upsert_restaurant, insert_review

# 정상 리뷰 이미지 도메인 화이트리스트 (네이버 리뷰 CDN)
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
    "스터닝버거": "1793528701","버거킹 부천시청역점": "2093976893","크라이치즈버거 신중동점": "2058075950",
    "우스매쉬 부천점": "2080832942","르버거": "2043097186",
}

# ── 설정 ─────────────────────────────────────────────────────
DOWNLOAD_IMAGES               = True
IMAGE_DIR                     = "images"
SAVE_CSV                      = True
CSV_PATH                      = "naver_reviews.csv"

MAX_REVIEWS_PER_RESTAURANT    = None   # 식당당 최대 리뷰 수 (None으로 바꾸면 무제한)

DELAY_PAGE_MIN                = 3.0
DELAY_PAGE_MAX                = 4.5
DELAY_REST_MIN                = 8.0
DELAY_REST_MAX                = 12.0
MAX_IMAGE_WORKERS             = 10

# ── GraphQL 쿼리 (변경 금지) ──────────────────────────────────
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
    return {
        "accept": "*/*",
        "accept-language": "ko",
        "content-type": "application/json",
        "origin": "https://pcmap.place.naver.com",
        "referer": f"https://pcmap.place.naver.com/restaurant/{place_id}/review/visitor",
        "user-agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                      "(KHTML, like Gecko) Chrome/145.0.0.0 Safari/537.36",
        # ★ 브라우저 DevTools에서 최신 쿠키로 교체하세요 (F12 > Network > graphql > Headers > cookie 복사) ★
        "cookie": '''NAC=YL19BMQKiMhK; NNB=YFLRAYGZENQWM; NFS=2; NID_AUT=8rpJgQ97MpUohDOG1qAhmtuzKrCUYHFp5kLEYoHA3uX9+FBJrymzIMXBk77SUIyj; cto_bundle=DOpvjF9rT0t6MWxGYXElMkZma2RpajFHM0paUGxFJTJGTDd2TlJVUUdYUnFUJTJGUHpwWUpValJYR3pWOTRGVEtCYTdtSHJhTmt3WHFZcFhmVTViWHExMVlVMjdOJTJCbGUzSXhOSXBnc3ZlaUppb05saEZqaFk3WkhNckxKVWszbjNUZGNOJTJCcTU1TDROdXFQViUyRkwxeG1IUDBzQlJkMDhYeXclM0QlM0Q; bnb_tooltip_shown_finance_v1=true; ASID=dfc29f200000019cf58edbe60000001d; NV_WETR_LOCATION_RGN_M="MDkyOTAxMTQ="; NV_WETR_LAST_ACCESS_RGN_M="MDkyOTAxMTQ="; PLACE_LANGUAGE=ko; tooltipDisplayed=true; NACT=1; SRT30=1777304956; SRT5=1777304956; _naver_usersession_=CZHiouVFBPUxebCN4eKHL6Id; page_uid=jP+aQwqpsW7sk9GosUN-072220; NID_SES=AAABoh/WW4HoAcJYpPM/yKl7kBfcGRvyYgA9rZgWCv472b989BoH/CvM2tZ/KBO4GfJNd1HOgcUSfr5e13dwxGPznWi9eK05zRXloyPjNY1LW4BUMV6Vu6gI9qgzA8y9CxG82h3kUwxe2xKqOQAAT8ElRd+NJY7V3nUqPA633ZQ7sF0pmoa2bTc+38V5FHvWVLha7p33njLBeBI3BgxNmO16d0R2ZQ6yN1CnAUelUSK2yfuUcoc1H0YPgURJYVysqddvfVnzd9bdXZwz1ySU9gFqUOUfDftu5d8Wb1q03ZnsAkK28vvVy5y/AEZbwElMeWQFIrIdlIh3nQFVQ1PYvV8QjnWfok93hfGRtJbxYHEo7r+x53sayMClOY9/4pPilEt87aDxpBLQoJsAeI4ty1y5w/MFzg+Iv7s8LNPiOXs2XSQdXzhrNiYVuIwh5ykAT0CDU8H7LtLBAWgfF9h9nKvomvgH9GXxv+4VvpcGFlx2OLAkn2uXU/eb1QigM+FtkmNzYYnTU8/Ghg6+ARU5Q24DM3+kZWiR1vQ7n12O+cTTu/RC4+9FmwQL8vzKMJp8cr1jMg==; BUC=fbLEIlEQKqFtaIVmvuP7k33xu0eAu_ypR7TxwFOP-7Q='''
    }


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
    # ★ 중복 이미지 수정: URL의 ? 이전 경로만 비교해서 중복 제거
    # ★ 화이트리스트 검사로 광고/프로필 이미지 배제
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

    voted_kws  = [kw.get("name","") for kw in r.get("votedKeywords",[]) or []]
    visit_cats = []
    for cat in r.get("visitCategories",[]) or []:
        kws = [k.get("name","") for k in cat.get("keywords",[]) or []]
        visit_cats.append(f"{cat.get('name','')}({','.join(kws)})" if kws else cat.get("name",""))

    item  = r.get("item")
    tags  = r.get("tags",[]) or []
    reply = r.get("reply")

    return {
        "ReviewID":        r.get("reviewId", r.get("id","")),
        "Restaurant":      name,
        "Review":          r.get("body",""),
        "Total":           r.get("rating", 0),
        "Menu":            item.get("name","") if item else "",
        "Date":            r.get("created",""),
        "HasPicture":      1 if image_urls else 0,
        "PlaceID":         place_id,
        "Author":          r.get("author",{}).get("nickname",""),
        "VisitCount":      r.get("visitCount", 0),
        "ImageURLs":       " | ".join(image_urls),
        "ImagePaths":      " | ".join(image_paths),
        "ImageCount":      len(image_urls),
        "VotedKeywords":   ", ".join(voted_kws),
        "VisitCategories": ", ".join(visit_cats),
        "Tags":            ", ".join(tags) if isinstance(tags, list) else str(tags),
        "OwnerReply":      reply.get("body","") if reply else "",
        # ── 신규 메타 (DB schema에 컬럼이 없으면 insert_review에서 무시됨)
        "CrawlKeyword":    crawl_keyword,
        "CrawlTimestamp":  datetime.utcnow().isoformat(),
        "IsReviewImage":   bool(image_urls),
    }


# ── DB 저장 ───────────────────────────────────────────────────

def save_to_db(all_reviews: list[dict]) -> bool:
    """파싱된 리뷰 목록을 PostgreSQL에 저장. 실패 시 False 반환."""
    try:
        with get_conn() as conn:
            restaurant_ids: dict[str, int] = {}

            for review in all_reviews:
                name     = review["Restaurant"]
                place_id = review["PlaceID"]

                if name not in restaurant_ids:
                    rest_id = upsert_restaurant(conn, {
                        "name":      name,
                        "place_id":  place_id,
                        "naver_url": f"https://map.naver.com/p/entry/place/{place_id}",
                        "source":    "crawler",
                    })
                    restaurant_ids[name] = rest_id

                insert_review(conn, restaurant_ids[name], review)

        print(f"  ✅ DB 저장: {len(all_reviews)}건 (식당 {len(restaurant_ids)}개)")
        return True

    except Exception as e:
        print(f"  ❌ DB 저장 실패: {e}")
        print(f"     → CSV 저장으로 대체됩니다")
        return False


# ── 크롤링 메인 ───────────────────────────────────────────────

def crawl_reviews(restaurants_dict: dict) -> list[dict]:
    all_reviews = []
    rest_list   = list(restaurants_dict.items())

    for ri, (name, place_id) in enumerate(rest_list):
        print(f"\n{'='*50}")
        print(f"  [{ri+1}/{len(rest_list)}] {name}  (ID: {place_id})")
        if MAX_REVIEWS_PER_RESTAURANT:
            print(f"  최대 {MAX_REVIEWS_PER_RESTAURANT}건 수집")
        print(f"{'='*50}")

        headers     = get_headers(place_id)
        next_cursor = None
        page        = 1
        idx         = 0
        retry_cnt   = 0

        time.sleep(random.uniform(1, 2))

        while True:
            # ★ 남은 수집량 계산해서 마지막 페이지 size 자동 조정
            if MAX_REVIEWS_PER_RESTAURANT:
                remaining = MAX_REVIEWS_PER_RESTAURANT - idx
                if remaining <= 0:
                    print(f"  ⏹️ 최대 리뷰 수 도달 ({MAX_REVIEWS_PER_RESTAURANT}건)")
                    break
                page_size = min(50, remaining)
            else:
                page_size = 50   # ★ 기본 50개

            variables = {
                "businessId": str(place_id), "businessType": "restaurant",
                "item": "0", "bookingBusinessId": None, "size": page_size,
                "isPhotoUsed": False, "includeContent": True,
                "getUserStats": True, "includeReceiptPhotos": True,
                "getReactions": True, "getTrailer": True,
            }
            if next_cursor:
                variables["after"] = next_cursor

            payload = [{"operationName":"getVisitorReviews",
                        "variables":{"input": variables},
                        "query": GRAPHQL_QUERY}]
            try:
                resp = requests.post(
                    "https://pcmap-api.place.naver.com/graphql",
                    json=payload, headers=headers, timeout=15,
                )
            except Exception as e:
                print(f"  ❌ 네트워크 오류: {e}"); break

            if resp.status_code == 429:
                retry_cnt += 1
                if retry_cnt > 3:
                    print(f"  ❌ 429 차단 3회 초과 → 건너뜀"); break
                wait = 10*retry_cnt + random.uniform(5, 10)
                print(f"  ⚠️ 429 → {wait:.0f}초 대기 ({retry_cnt}/3)")
                time.sleep(wait); continue

            if resp.status_code != 200:
                print(f"  ❌ HTTP {resp.status_code}")
                if resp.status_code in (401, 403):
                    print(f"     쿠키 만료! 브라우저에서 새로 복사하세요")
                break

            retry_cnt = 0

            try:
                data    = resp.json()
                reviews = data[0]["data"]["visitorReviews"]["items"]
                total   = data[0]["data"]["visitorReviews"].get("total","?")
            except Exception:
                print(f"  ❌ 응답 파싱 실패"); break

            if not reviews:
                print(f"  ✅ 완료 (더 이상 리뷰 없음)"); break

            img_cnt = 0
            crawl_kw = os.environ.get("CRAWL_KEYWORD", "")
            for r in reviews:
                parsed = parse_review(r, place_id, name, idx, crawl_keyword=crawl_kw)
                all_reviews.append(parsed)
                if parsed["HasPicture"]: img_cnt += 1
                idx += 1

            print(f"  📄 페이지 {page}: {len(reviews)}건 (📷{img_cnt}) | 누적: {idx}/{total}")
            next_cursor = reviews[-1].get("cursor")
            if not next_cursor: break

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
    print("🔍 네이버 플레이스 크롤러 (PostgreSQL 연동)")
    print(f"   대상: {len(restaurants)}개 식당")
    print(f"   식당당 최대: {MAX_REVIEWS_PER_RESTAURANT if MAX_REVIEWS_PER_RESTAURANT else '무제한'}건")
    print(f"   이미지 다운로드: {'ON' if DOWNLOAD_IMAGES else 'OFF'}")

    if "여기에_최신_쿠키_붙여넣기" in get_headers("test").get("cookie",""):
        print("\n❌ get_headers() 함수의 cookie를 브라우저에서 복사해 붙여넣으세요!")
    else:
        reviews = crawl_reviews(restaurants)

        if reviews:
            # 1) DB 저장
            db_ok = save_to_db(reviews)

            # 2) CSV 저장 (항상 또는 DB 실패 시)
            if SAVE_CSV or not db_ok:
                df = pd.DataFrame(reviews)
                df.to_csv(CSV_PATH, index=False, encoding="utf-8-sig")
                total = len(df); img = int(df["HasPicture"].sum())
                print(f"\n  ✅ CSV: {CSV_PATH}  ({total}건, 이미지 {img}건 {img/total*100:.1f}%)")
        else:
            print("\n❌ 수집 실패 → 쿠키를 새로 복사하거나 5분 후 재시도")