"""
네이버 플레이스 리뷰 크롤러 (텍스트 + 이미지 동시 수집)
=======================================================
실행: python naver_place_crawler.py
설치: pip install requests pandas
"""

import requests
import pandas as pd
import time
import os
import random
from concurrent.futures import ThreadPoolExecutor, as_completed   # ← 병렬 다운로드용

# ── 크롤링 대상 (place/ 뒤 숫자) ──
restaurants = {
    "김태완스시 성신여대점": "21816603"
}

# ── 설정 변수 (여기서 속도 조절 가능) ──
DOWNLOAD_IMAGES = True          # True로 바꾸면 이미지까지 다운로드
IMAGE_DIR = "images"

# 안전하면서 가장 빠른 속도 설정 (429 방지)
DELAY_PAGE_MIN = 3.0             # ← 2.0 → 3.0
DELAY_PAGE_MAX = 4.5
DELAY_RESTAURANT_MIN = 8.0       # ← 4.5 → 8.0
DELAY_RESTAURANT_MAX = 12.0
MAX_IMAGE_WORKERS = 10       # 이미지 동시 다운로드 수 (15~22 추천)

GRAPHQL_QUERY = """query getVisitorReviews($input: VisitorReviewsInput) {
  visitorReviews(input: $input) {
    items {
      id
      cursor
      reviewId
      rating
      author { id nickname imageUrl review { totalCount imageCount avgRating __typename } __typename }
      body
      thumbnail
      media { type thumbnail thumbnailRatio class videoId videoUrl trailerUrl __typename }
      tags
      status
      visitCount
      viewCount
      visited
      created
      reply { body created __typename }
      originType
      item { name code options __typename }
      language
      apolloCacheId
      translatedText
      businessName
      votedKeywords { code iconUrl iconCode name __typename }
      userIdno
      receiptInfoUrl
      reactionStat { id typeCount { name count __typename } totalCount __typename }
      nickname
      visitCategories { code name keywords { code name __typename } __typename }
      representativeVisitDateTime
      __typename
    }
    starDistribution { score count __typename }
    total
    __typename
  }
}"""


def get_headers(place_id):
    return {
        "accept": "*/*",
        "accept-language": "ko",
        "content-type": "application/json",
        "origin": "https://pcmap.place.naver.com",
        "referer": f"https://pcmap.place.naver.com/restaurant/{place_id}/review/visitor",
        "user-agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/145.0.0.0 Safari/537.36",
        "cookie": "NAC=YL19BMQKiMhK; NNB=YFLRAYGZENQWM; NFS=2; NID_AUT=8rpJgQ97MpUohDOG1qAhmtuzKrCUYHFp5kLEYoHA3uX9+FBJrymzIMXBk77SUIyj; NID_JKL=WAT/BqDCM+Igdcw6Evi4HqHjD7xBpt2t4inOv7moYss=; tooltipDisplayed=true; cto_bundle=DOpvjF9rT0t6MWxGYXElMkZma2RpajFHM0paUGxFJTJGTDd2TlJVUUdYUnFUJTJGUHpwWUpValJYR3pWOTRGVEtCYTdtSHJhTmt3WHFZcFhmVTViWHExMVlVMjdOJTJCbGUzSXhOSXBnc3ZlaUppb05saEZqaFk3WkhNckxKVWszbjNUZGNOJTJCcTU1TDROdXFQViUyRkwxeG1IUDBzQlJkMDhYeXclM0QlM0Q; bnb_tooltip_shown_finance_v1=true; PLACE_LANGUAGE=ko; NACT=1; SRT30=1773646258; page_uid=jkPx3lqos5yFfqcaLlZ-185615; NID_SES=AAABjQhcHWV4j/ZiqrQklT1tXiHEr9VA7W26UEAPLhR0T2u9h8uGioMSf51M/0ILe9GSkac4LyYD0Q6WxbAZPL7ARv0BwH7iE2CY5qN/WRg0VzLrfbVC0E3URQZt3QHXd4wNlF/rqO2xOiB//uZKVdDl14rkfyI3ArY3w5a+Pl79CSSp14dASWVpQ+jPjz5HzqaVdN8CFzTOT99vzPR0oYAZhzBiG4CPd37o1nR1ZYUMtbtX2/GmWU9Ey8vH9NPreeCOMEeDu0GJwj2LqFP6AUl0E+dUKynTppAf/+4ZSlV56Z5gozTzQZe+Acv42kpA65AoNpgTuyBPJ8kdfvKNV9IrdPNXCBmr/g6O7FjSuvQ3heObxHugLTEcXYIgEG5bLxDOR8iAbnkrzShIRCpvC4UaNLATtrdrUpXKaw5lJelN+Ph4Tzo79YlXLCzcA3VDdT2anBUu73JGPhbYigpm8kuyFF1ePTbPoiWm/iXQYCBblmm7G/YQjfsBEk04zkFobXXnOX97v1Vl4WFiE68D+GctxRY=; ASID=dfc29f200000019cf58edbe60000001d; SRT5=1773647750; BUC=3LVBNvj2gFf6A4XKdexjWtJRIjEKK_mGS97Pdv4SGr4=",
    }


def download_image(url, save_path):
    try:
        resp = requests.get(url, timeout=10)
        if resp.status_code == 200:
            os.makedirs(os.path.dirname(save_path), exist_ok=True)
            with open(save_path, "wb") as f:
                f.write(resp.content)
            return True
    except:
        pass
    return False


# ★★★ 이미지 병렬 다운로드 (최대 속도 + 안전) ★★★
def download_images_concurrently(image_urls, place_id, idx):
    if not DOWNLOAD_IMAGES or not image_urls:
        return []
    
    paths = []
    with ThreadPoolExecutor(max_workers=MAX_IMAGE_WORKERS) as executor:
        future_to_url = {}
        for i, url in enumerate(image_urls):
            path = f"{IMAGE_DIR}/{place_id}_{idx}_{i}.jpg"
            future = executor.submit(download_image, url, path)
            future_to_url[future] = path
        
        for future in as_completed(future_to_url):
            if future.result():
                paths.append(future_to_url[future])
    return paths


def parse_review(r, place_id, name, idx):
    image_urls = []
    thumb = r.get("thumbnail")
    if thumb:
        image_urls.append(thumb)
    for m in r.get("media", []) or []:
        if m.get("thumbnail") and m["thumbnail"] not in image_urls:
            image_urls.append(m["thumbnail"])

    # 병렬 다운로드 적용
    image_paths = download_images_concurrently(image_urls, place_id, idx)

    voted_kws = [kw.get("name", "") for kw in r.get("votedKeywords", []) or []]
    visit_cats = []
    for cat in r.get("visitCategories", []) or []:
        kws = [k.get("name", "") for k in cat.get("keywords", []) or []]
        visit_cats.append(f"{cat.get('name','')}({','.join(kws)})" if kws else cat.get("name", ""))

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
        "ImageURLs": " | ".join(image_urls),
        "ImagePaths": " | ".join(image_paths),
        "ImageCount": len(image_urls),
        "VotedKeywords": ", ".join(voted_kws),
        "VisitCategories": ", ".join(visit_cats),
        "Tags": ", ".join(tags) if isinstance(tags, list) else str(tags),
        "OwnerReply": reply.get("body", "") if reply else "",
    }


def crawl_reviews(restaurants_dict):
    all_reviews = []
    restaurant_list = list(restaurants_dict.items())

    for ri, (name, place_id) in enumerate(restaurant_list):
        print(f"\n{'='*50}")
        print(f"  [{ri+1}/{len(restaurant_list)}] 크롤링: {name} (ID: {place_id})")
        print(f"{'='*50}")

        headers = get_headers(place_id)
        next_cursor = None
        page = 1
        idx = 0
        retry_count = 0

        # 식당 시작 전 대기
        wait = random.uniform(1, 2)
        print(f"  ⏳ {wait:.1f}초 대기...")
        time.sleep(wait)

        while True:
            variables = {
                "businessId": str(place_id),
                "businessType": "restaurant",
                "item": "0",
                "bookingBusinessId": None,
                "size": 10,
                "isPhotoUsed": False,
                "includeContent": True,
                "getUserStats": True,
                "includeReceiptPhotos": True,
                "getReactions": True,
                "getTrailer": True,
            }
            if next_cursor:
                variables["after"] = next_cursor

            payload = [{"operationName": "getVisitorReviews",
                        "variables": {"input": variables},
                        "query": GRAPHQL_QUERY}]

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
                retry_count += 1
                if retry_count > 3:
                    print(f"  ❌ 429 차단 3회 초과 → 이 식당 건너뜀")
                    break
                wait = 10 * retry_count + random.uniform(5, 10)
                print(f"  ⚠️ 429 Rate Limit → {wait:.0f}초 대기 후 재시도 ({retry_count}/3)")
                time.sleep(wait)
                continue

            if resp.status_code != 200:
                print(f"  ❌ HTTP {resp.status_code}")
                if resp.status_code in [401, 403]:
                    print(f"     → 쿠키 만료! 브라우저에서 새로 복사하세요")
                break

            retry_count = 0

            try:
                data = resp.json()
                reviews = data[0]["data"]["visitorReviews"]["items"]
                total = data[0]["data"]["visitorReviews"].get("total", "?")
            except:
                print(f"  ❌ 응답 파싱 실패")
                break

            if not reviews:
                print(f"  ✅ 완료! (더 이상 리뷰 없음)")
                break

            img_cnt = 0
            for r in reviews:
                parsed = parse_review(r, place_id, name, idx)
                all_reviews.append(parsed)
                if parsed["HasPicture"]:
                    img_cnt += 1
                idx += 1

            print(f"  📄 페이지 {page}: {len(reviews)}건 (📷{img_cnt}) | 누적: {idx}/{total}")
            next_cursor = reviews[-1].get("cursor")
            if not next_cursor:
                break

            page += 1
            # 페이지 간 안전 대기 (설정 변수 적용)
            time.sleep(random.uniform(DELAY_PAGE_MIN, DELAY_PAGE_MAX))

        print(f"  → {name}: {idx}건 수집 완료")

        # 식당 간 대기
        if ri < len(restaurant_list) - 1:
            wait = random.uniform(DELAY_RESTAURANT_MIN, DELAY_RESTAURANT_MAX)
            print(f"  ⏳ 다음 식당 전 {wait:.1f}초 대기...")
            time.sleep(wait)

    return all_reviews


if __name__ == "__main__":
    print("🔍 네이버 플레이스 리뷰+이미지 크롤러 (최종 수정 버전)")
    print(f"   대상: {len(restaurants)}개 식당")
    print(f"   이미지 병렬 다운로드: {'ON' if DOWNLOAD_IMAGES else 'OFF'} (MAX {MAX_IMAGE_WORKERS}개)")
    print(f"   페이지 대기: {DELAY_PAGE_MIN}~{DELAY_PAGE_MAX}초 | 식당 대기: {DELAY_RESTAURANT_MIN}~{DELAY_RESTAURANT_MAX}초")
    print()

    # 쿠키 확인
    test_headers = get_headers("test")
    if "여기에" in test_headers.get("cookie", "여기에"):
        print("❌ 쿠키를 설정해주세요! (위 get_headers() 함수 참조)")
    else:
        reviews = crawl_reviews(restaurants)

        if reviews:
            df = pd.DataFrame(reviews)
            df.to_csv("naver_reviews.csv", index=False, encoding="utf-8-sig")
            total = len(df)
            img = df["HasPicture"].sum()
            print(f"\n{'='*50}")
            print(f"  ✅ 총 {total}건 저장 완료!")
            print(f"     이미지 포함: {img}건 ({img/total*100:.1f}%)")
            print(f"     → naver_reviews.csv")
            print(f"{'='*50}")
        else:
            print("\n❌ 수집 실패 → 5분 후 다시 시도하거나 쿠키를 새로 복사하세요.")