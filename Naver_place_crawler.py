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

# ── 크롤링 대상 (place/ 뒤 숫자) ──
restaurants = {
    "김북순큰남비집 신사본점": "11722762","청담골": "11477316","수묵": "1355794902","돌산등대집": "1964934333",
    "호남식당": "20870591","풍년집 선릉점": "30916873","냉삼식당 신사점": "38276485","고봉당 논현역점": "2007145517",
    "약수동해물텀벙": "12885813","성수AGU": "1978223431","김영희아구찜&코다리냉면 반포원베일리 직영점": "1619999128","찬란한아구 강남본점": "1506437174",
    "전주청국장집": "11678577","에덴식당": "36898413","별미볶음점": "32817544","24시서울밥집": "1566032438",
    "마음은콩밭에": "19927989","시골야채된장": "11709266","다래식당": "13575119","팔도밀방 강남본점": "1281140539",
    "탐라순대국감자탕 양재역직영점": "1113983640","신동궁 감자탕 뼈숯불구이 선릉직영점": "18468954","탕탕집 논현본점": "37867681","농민백암순대 본점": "13149768",
    "순리대로 강남도곡본점": "1851351813","기절초풍왕순대": "18709584","청담동순도리 청담점": "1691368632","청담 순두부 본점": "19873395",
    "김북순큰남비집 신사본점": "11722762","이문설렁탕": "11711983","마포양지설렁탕": "37365482","부대찌개대사관 시청점": "2018894329"
}

DOWNLOAD_IMAGES = False #False 써서 text만 다운 ㄱ
IMAGE_DIR = "images"

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
        # ★★★ 쿠키: 브라우저 F12 > Network > graphql > Headers > cookie 복사 ★★★
        # ★★★ 반드시 한 줄로! 앞에 줄바꿈 넣지 말 것! ★★★ #쿠키 꼭 넣으세요!!!!!
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


def parse_review(r, place_id, name, idx):
    image_urls = []
    thumb = r.get("thumbnail")
    if thumb:
        image_urls.append(thumb)
    for m in r.get("media", []) or []:
        if m.get("thumbnail") and m["thumbnail"] not in image_urls:
            image_urls.append(m["thumbnail"])

    image_paths = []
    if DOWNLOAD_IMAGES and image_urls:
        for i, url in enumerate(image_urls):
            path = f"{IMAGE_DIR}/{place_id}_{idx}_{i}.jpg"
            if download_image(url, path):
                image_paths.append(path)

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

        # ★ 식당 시작 전 랜덤 대기 (봇 탐지 회피)
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

            # ★ 429 자동 재시도 (최대 3회, 대기시간 점점 증가)
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
                if resp.status_code == 401 or resp.status_code == 403:
                    print(f"     → 쿠키 만료! 브라우저에서 새로 복사하세요")
                break

            # 성공 → retry 카운터 리셋
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

            # ★ 페이지 간 랜덤 대기 (3~5초)
            time.sleep(random.uniform(1, 1.5))

        print(f"  → {name}: {idx}건 수집 완료")

        # ★ 식당 간 대기 (5~8초)
        if ri < len(restaurant_list) - 1:
            wait = random.uniform(2, 3)
            print(f"  ⏳ 다음 식당 전 {wait:.1f}초 대기...")
            time.sleep(wait)

    return all_reviews


if __name__ == "__main__":
    print("🔍 네이버 플레이스 리뷰+이미지 크롤러")
    print(f"   대상: {len(restaurants)}개 식당")
    print(f"   429 에러시 자동 재시도 (최대 3회)")
    print()

    # ★ 쿠키 확인
    test_headers = get_headers("test")
    if "여기에" in test_headers.get("cookie", "여기에"):
        print("❌ 쿠키를 설정해주세요!")
        print("   1. 네이버 지도에서 아무 식당 리뷰 페이지 열기")
        print("   2. F12 → Network → graphql 필터 → 리뷰 스크롤")
        print("   3. 잡힌 요청 → Headers → cookie 값 전체 복사")
        print('   4. get_headers()의 "cookie": "여기에..." 부분에 붙여넣기')
        print("   ※ 반드시 한 줄로! 줄바꿈 넣으면 에러남")
    else:
        reviews = crawl_reviews(restaurants)

        if reviews:
            df = pd.DataFrame(reviews)
            df.to_csv("naver_reviews.csv", index=False, encoding="utf-8-sig")
            total = len(df)
            img = df["HasPicture"].sum()
            print(f"\n{'='*50}")
            print(f"  ✅ 총 {total}건 저장")
            print(f"     이미지 포함: {img}건 ({img/total*100:.1f}%)")
            print(f"     → naver_reviews.csv")
            print(f"{'='*50}")
        else:
            print("\n❌ 수집 실패")
            print("   → 5분 기다렸다가 다시 실행")
            print("   → 그래도 안 되면 쿠키 새로 복사")