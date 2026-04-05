"""
식당 정보 수집기 (restaurant_db.py)
====================================
naver_reviews.csv의 PlaceID에서 자동으로 naver_url 생성.
API 키 없어도 URL은 자동 생성됨.

실행: python restaurant_db.py
"""

import json
import os
import time
import pandas as pd
import glob

DB_FILE = "restaurant_db.json"

# 네이버 검색 API (선택 — 없어도 naver_url은 자동 생성됨)
CLIENT_ID = "C8yr6klv7BWC5txnb1fL"
CLIENT_SECRET = "JP9p8MvCiI"


def load_db():
    if os.path.exists(DB_FILE):
        with open(DB_FILE, "r", encoding="utf-8") as f:
            return json.load(f)
    return {}


def save_db(db):
    with open(DB_FILE, "w", encoding="utf-8") as f:
        json.dump(db, f, ensure_ascii=False, indent=2)


def search_naver_api(name, area=""):
    """네이버 지역검색 API로 주소/전화번호 수집 (API 키 있을 때만)"""
    if "여기에" in CLIENT_ID:
        return None
    import requests
    try:
        resp = requests.get(
            "https://openapi.naver.com/v1/search/local.json",
            headers={"X-Naver-Client-Id": CLIENT_ID, "X-Naver-Client-Secret": CLIENT_SECRET},
            params={"query": f"{area} {name}".strip(), "display": 1, "sort": "comment"},
            timeout=10,
        )
        if resp.status_code != 200:
            return None
        items = resp.json().get("items", [])
        if not items:
            return None
        item = items[0]
        return {
            "address": item.get("address", ""),
            "road_address": item.get("roadAddress", ""),
            "phone": item.get("telephone", ""),
            "category": item.get("category", ""),
        }
    except:
        return None


def build_db():
    """
    1) naver_reviews.csv에서 PlaceID → naver_url 자동 생성
    2) API 키 있으면 주소/전화번호도 자동 수집
    3) vectors.json에서 식당 목록 보충
    """
    db = load_db()
    updated = 0

    # ── CSV에서 PlaceID 추출 → naver_url 자동 생성 ──
    csv_files = glob.glob("*reviews*.csv") + glob.glob("naver_*.csv")
    for csv_file in set(csv_files):
        try:
            df = pd.read_csv(csv_file, encoding="utf-8-sig")
        except:
            continue

        if "PlaceID" not in df.columns or "Restaurant" not in df.columns:
            continue

        pairs = df[["Restaurant", "PlaceID"]].drop_duplicates()
        print(f"\n📂 {csv_file} → {len(pairs)}개 식당")

        for _, row in pairs.iterrows():
            name = str(row["Restaurant"])
            pid = str(row["PlaceID"])

            if name in db and db[name].get("naver_url"):
                print(f"  ⏭️ {name} (이미 있음)")
                continue

            # ★ PlaceID → naver_url 자동 생성 (API 불필요)
            naver_url = f"https://map.naver.com/p/entry/place/{pid}"

            if name not in db:
                db[name] = {}

            db[name]["name"] = name
            db[name]["place_id"] = pid
            db[name]["naver_url"] = naver_url

            # API 키 있으면 주소도 수집
            api_info = search_naver_api(name)
            if api_info:
                db[name]["address"] = api_info.get("address", "")
                db[name]["road_address"] = api_info.get("road_address", "")
                db[name]["phone"] = api_info.get("phone", "")
                db[name]["category"] = api_info.get("category", "")
                addr = api_info.get("road_address") or api_info.get("address") or ""
                print(f"  ✅ {name} → 📍{addr} (API)")
                time.sleep(0.3)
            else:
                db[name].setdefault("address", "")
                db[name].setdefault("road_address", "")
                db[name].setdefault("phone", "")
                db[name].setdefault("category", "")
                print(f"  ✅ {name} → 🗺️ {naver_url}")

            db[name].setdefault("lat", 0.0)
            db[name].setdefault("lng", 0.0)
            db[name]["source"] = "auto"
            db[name]["updated"] = time.strftime("%Y-%m-%d")
            updated += 1

    # ── vectors.json에서 식당 보충 ──
    for vf in glob.glob("*_vectors.json"):
        if "multimodal" in vf:
            continue
        try:
            with open(vf, "r", encoding="utf-8") as f:
                data = json.load(f)
            for name in data.get("restaurants", {}).keys():
                if name in db:
                    continue
                db[name] = {
                    "name": name, "naver_url": "", "address": "",
                    "road_address": "", "phone": "",
                    "category": data.get("keyword", ""),
                    "lat": 0.0, "lng": 0.0,
                    "source": "vectors",
                    "updated": time.strftime("%Y-%m-%d"),
                }
                updated += 1
        except:
            pass

    save_db(db)
    return db, updated


if __name__ == "__main__":
    print("🗺️ 식당 정보 수집기")
    db, updated = build_db()

    print(f"\n{'='*50}")
    print(f"  ✅ {len(db)}개 식당 ({updated}개 갱신)")
    print(f"  → {DB_FILE}")
    print(f"{'='*50}")

    for name, info in db.items():
        url = info.get("naver_url", "")
        addr = info.get("road_address") or info.get("address") or ""
        print(f"  {name}")
        if addr:
            print(f"    📍 {addr}")
        if url:
            print(f"    🗺️  {url}")