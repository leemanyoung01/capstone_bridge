import requests 
import pandas as pd 
import time

restaurant_ids = { "정도시락n밑반찬": 34535,}
all_reviews = []
for name, rid in restaurant_ids.items():
    print(f"크롤링 중: {name}")
    page = 1
    
    while True:
        url = f"https://www.yogiyo.co.kr/api/v1/reviews/{rid}/"
        params = {
            "page": page,
            "page_size": 10,
            "sort": "time",
        }
        headers = {
            "User-Agent": "Mozilla/5.0",
            "X-ApiKey": "iphoneap", # ← Network 탭에서 확인하신 키 유지
            "Referer": "https://www.yogiyo.co.kr/",
        }
        
        resp = requests.get(url, params=params, headers=headers)
        
        if resp.status_code != 200:
            print(f" 끝! (페이지 {page}에서 멈춤) - 상태 코드: {resp.status_code}")
            break
            
        data = resp.json()
        
        if not data:
            break
            
        for r in data:
            # ✅ 수정된 부분: 메뉴 리스트에서 메뉴 이름('name')만 추출해서 문자로 변환
            menu_list = r.get("menu_items", []) if isinstance(r, dict) else []
            menu_names = [m.get("name", str(m)) if isinstance(m, dict) else str(m) for m in menu_list]
            menu_str = ", ".join(menu_names)

            all_reviews.append({
                "RestaurantID": rid,
                "Restaurant": name,
                "UserID": r.get("nickname", "")[:2] + "**" if isinstance(r, dict) else "",
                "Menu": menu_str, # ✅ 수정한 메뉴 문자열 적용
                "Review": r.get("comment", "") if isinstance(r, dict) else "",
                "Total": r.get("rating", 0) if isinstance(r, dict) else 0,
                "Taste": r.get("rating_taste", 0) if isinstance(r, dict) else 0,
                "Quantity": r.get("rating_quantity", 0) if isinstance(r, dict) else 0,
                "Delivery": r.get("rating_delivery", 0) if isinstance(r, dict) else 0,
                "Date": r.get("time", "") if isinstance(r, dict) else "",
                "HasPicture": 1 if isinstance(r, dict) and r.get("review_images") else 0,
            })
            
        page += 1
        time.sleep(1) # 서버 부하 방지
        
    print(f" → {name}: 수집 완료")

# CSV 저장
df = pd.DataFrame(all_reviews)
df.to_csv("yogiyo_reviews.csv", index=False, encoding="utf-8-sig")
print(f"\n완료! 총 {len(df)}건 → yogiyo_reviews.csv 저장됨")