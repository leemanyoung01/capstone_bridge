"""
pipeline_test.py — 전체 파이프라인 로컬 테스트
================================================
크롤링 없이 더미 데이터로 DB → Food_profiler → app.py 까지
한 번에 검증하는 스크립트.

실행:
  python pipeline_test.py              # 전체 테스트
  python pipeline_test.py --step db   # DB 연결만
  python pipeline_test.py --step data # 더미 데이터 삽입
  python pipeline_test.py --step prof # 프로파일러
  python pipeline_test.py --step api  # API 응답

전제: PostgreSQL이 실행 중이고 schema.sql이 적용되어 있어야 합니다.
  psql -U postgres -c "CREATE DATABASE tastebridge;"
  psql -U postgres -d tastebridge -f schema.sql
"""

import sys, os, json, time, random, string
import requests as http_requests

from dotenv import load_dotenv
load_dotenv(override=True)

KEYWORD = os.environ.get("CRAWL_KEYWORD") or os.environ.get("DEFAULT_KEYWORD", "삼겹살")
TEST_PREFIX = "_TEST_"        # 테스트 데이터 식별자 (정리용)
API_BASE    = "http://127.0.0.1:5000"

STEP = None
if "--step" in sys.argv:
    idx = sys.argv.index("--step")
    if idx+1 < len(sys.argv):
        STEP = sys.argv[idx+1]

# ── 색상 출력 헬퍼 ────────────────────────────────────────────
GREEN  = "\033[92m"
RED    = "\033[91m"
YELLOW = "\033[93m"
CYAN   = "\033[96m"
RESET  = "\033[0m"
BOLD   = "\033[1m"

def ok(msg):    print(f"  {GREEN}✓{RESET} {msg}")
def fail(msg):  print(f"  {RED}✗{RESET} {msg}")
def info(msg):  print(f"  {CYAN}→{RESET} {msg}")
def section(title):
    print(f"\n{BOLD}{'─'*55}{RESET}")
    print(f"{BOLD}  {title}{RESET}")
    print(f"{BOLD}{'─'*55}{RESET}")


# ══════════════════════════════════════════════════════════════
# STEP 1: DB 연결 확인
# ══════════════════════════════════════════════════════════════

def test_db_connection():
    section("STEP 1 — DB 연결")
    from db import get_conn, DATABASE_URL
    info(f"DATABASE_URL = {DATABASE_URL.split('@')[-1]}")
    try:
        with get_conn() as conn:
            with conn.cursor() as cur:
                cur.execute("SELECT version()")
                ver = cur.fetchone()["version"]
                ok(f"연결 성공: {ver[:40]}")

            # 테이블 존재 확인
            with conn.cursor() as cur:
                cur.execute("""
                    SELECT table_name FROM information_schema.tables
                    WHERE table_schema = 'public'
                    ORDER BY table_name
                """)
                tables = [r["table_name"] for r in cur.fetchall()]

            required = {"restaurants","reviews","review_images",
                        "restaurant_vectors","axes_config","representative_images"}
            missing  = required - set(tables)
            if missing:
                fail(f"테이블 없음: {missing}")
                print(f"\n  psql -U postgres -d tastebridge -f schema.sql")
                return False
            else:
                ok(f"테이블 확인: {sorted(tables)}")
                return True
    except Exception as e:
        fail(f"DB 연결 실패: {e}")
        print(f"\n  1) PostgreSQL 실행 확인: pg_ctl status")
        print(f"  2) DATABASE_URL 환경변수 확인")
        print(f"  3) DB 생성: createdb tastebridge")
        return False


# ══════════════════════════════════════════════════════════════
# STEP 2: 더미 데이터 삽입
# ══════════════════════════════════════════════════════════════

DUMMY_RESTAURANTS = [
    {"name": f"{TEST_PREFIX}황금돼지집",  "place_id": "1111111111", "category": "삼겹살"},
    {"name": f"{TEST_PREFIX}참숯불고기",  "place_id": "2222222222", "category": "삼겹살"},
    {"name": f"{TEST_PREFIX}제주흑돼지",  "place_id": "3333333333", "category": "삼겹살"},
]

DUMMY_REVIEWS = [
    # 황금돼지집 — 매운맛 부각
    ("삼겹살이 정말 맛있어요! 고기가 두툼하고 불맛이 살아있어요. 또 올게요.", 5.0, "삼겹살"),
    ("겉은 바삭하고 속은 육즙이 풍부해요. 짱이에요.", 5.0, "삼겹살"),
    ("양도 넉넉하고 가성비 최고입니다. 재주문할게요.", 4.5, "삼겹살"),
    ("불맛이 강하고 담백해서 좋아요. 느끼하지 않아요.", 4.5, "삼겹살"),
    ("고소하고 쫄깃한 식감. 삼겹살 집 중에 제일 나아요.", 5.0, "삼겹살"),
    # 참숯불고기
    ("숯불 향이 진하고 고기가 촉촉해요. 맛있네요.", 4.5, "삼겹살"),
    ("조금 기름진 편이지만 맛은 좋아요.", 4.0, "삼겹살"),
    ("재주문 의사 있어요. 직원분들이 친절해요.", 4.5, "삼겹살"),
    ("담백하고 깔끔한 맛이에요. 느끼하지 않고 좋아요.", 5.0, "삼겹살"),
    ("고기가 두툼해서 좋은데 조금 비싼 편이에요.", 4.0, "삼겹살"),
    # 제주흑돼지
    ("흑돼지라 그런지 더 고소하고 맛있어요! 최고!", 5.0, "삼겹살"),
    ("육즙이 넘쳐요. 촉촉하고 쫄깃한 식감 최고!", 5.0, "삼겹살"),
    ("가격이 조금 있지만 그 값어치를 해요.", 4.5, "흑돼지 삼겹살"),
    ("겉바속촉이에요. 강추합니다!", 5.0, "삼겹살"),
    ("양이 조금 적은 편이지만 맛은 최고예요.", 4.0, "삼겹살"),
]

def test_insert_dummy():
    section("STEP 2 — 더미 데이터 삽입")
    from db import get_conn, upsert_restaurant, insert_review

    try:
        with get_conn() as conn:
            rest_ids = {}
            for r in DUMMY_RESTAURANTS:
                rid = upsert_restaurant(conn, {
                    **r,
                    "naver_url":    f"https://map.naver.com/p/entry/place/{r['place_id']}",
                    "road_address": f"경기도 성남시 분당구 테스트로 {random.randint(1,100)}",
                    "phone":        f"031-{random.randint(100,999)}-{random.randint(1000,9999)}",
                    "source":       "test",
                })
                rest_ids[r["name"]] = rid
                ok(f"식당 upsert: {r['name']} (id={rid})")

            # 리뷰 배분 (식당당 5건)
            names = list(rest_ids.keys())
            rev_cnt = 0
            for i, (content, rating, menu) in enumerate(DUMMY_REVIEWS):
                name  = names[i // 5]
                rid   = rest_ids[name]
                rv_id = "".join(random.choices(string.digits, k=10))
                result = insert_review(conn, rid, {
                    "ReviewID":    rv_id,
                    "Review":      content,
                    "Total":       rating,
                    "Menu":        menu,
                    "Author":      f"테스터{i}",
                    "VisitCount":  random.randint(1,10),
                    "HasPicture":  random.choice([0,0,0,1]),
                    "Date":        f"2024-{random.randint(1,12):02d}-{random.randint(1,28):02d}",
                    "ImageURLs":   "",
                    "ImagePaths":  "",
                    "VotedKeywords": "",
                    "OwnerReply":  "",
                })
                if result: rev_cnt += 1

        ok(f"식당 {len(DUMMY_RESTAURANTS)}개, 리뷰 {rev_cnt}건 삽입 완료")
        return True
    except Exception as e:
        fail(f"데이터 삽입 실패: {e}")
        import traceback; traceback.print_exc()
        return False


# ══════════════════════════════════════════════════════════════
# STEP 3: Food_profiler 실행
# ══════════════════════════════════════════════════════════════

def test_profiler():
    section("STEP 3 — Food_profiler")

    # Stand JSON 없으면 임시 생성
    _ensure_stand()

    from Food_profiler import (
        compose_axes, load_stand, build_profiles, save_to_db, save_json
    )
    from db import get_conn, get_reviews_for_profiler

    stand = load_stand()
    rules = stand["rules"] if stand else {"scoring":{"positive_hit":1.0,"negative_hit":-0.5}}

    taste_axes, meta_axes = compose_axes(KEYWORD)
    ok(f"맛축 {len(taste_axes)}개 | 메타축 {len(meta_axes)}개")

    # DB에서 리뷰 조회 (테스트 식당만)
    with get_conn() as conn:
        rows = get_reviews_for_profiler(conn, KEYWORD, [KEYWORD, "흑돼지"])
    info(f"DB 리뷰: {len(rows)}건")
    if len(rows) == 0:
        fail("리뷰 없음 → STEP 2(더미 삽입) 먼저 실행")
        return False

    import pandas as pd
    df = pd.DataFrame(rows)

    profiles, axes_names = build_profiles(df, KEYWORD, taste_axes, meta_axes, rules)
    ok(f"프로필 생성: {len(profiles)}개 식당, {len(axes_names)}개 축")

    if not profiles:
        fail("프로필 생성 실패 (리뷰 2건 미만)")
        return False

    # 결과 미리보기
    for name, p in profiles.items():
        info(f"{name} ({p['keyword_reviews']}건)")
        for ax, val in sorted(p["taste_vector"].items(), key=lambda x:-abs(x[1]))[:3]:
            if abs(val) > 0.001:
                bar = ("▲" if val>0 else "▼") * min(int(abs(val)*8),10)
                print(f"         {ax:12s} {val:+.3f}  {bar}")

    save_to_db(KEYWORD, profiles, {**taste_axes,**meta_axes}, taste_axes, meta_axes, df)
    save_json(KEYWORD, profiles, axes_names, {**taste_axes,**meta_axes}, taste_axes, meta_axes)
    ok("DB 저장 + JSON 저장 완료")
    return True


def _ensure_stand():
    """Stand JSON이 없으면 최소한의 더미 파일 생성."""
    stand_dir = os.path.join(os.path.dirname(__file__), "stand")
    os.makedirs(stand_dir, exist_ok=True)

    axes_path   = os.path.join(stand_dir, "stand_axes.json")
    lexicon_path= os.path.join(stand_dir, "stand_lexicon.json")
    rules_path  = os.path.join(stand_dir, "stand_rules.json")

    if not os.path.exists(axes_path):
        axes = {
            "keyword_aliases": {"삼겹살": ["삼겹살","흑돼지","목살","오겹살"]},
            "alias_to_category": {"삼겹살":"삼겹살"},
            "meta_axes": {"전반적만족":{"group":"메타","weight":0.3}},
            "taste_axes": {
                "불맛":   {"group":"맛"},
                "담백함": {"group":"맛"},
                "고소함": {"group":"맛"},
                "육즙":   {"group":"식감"},
                "바삭함": {"group":"식감"},
                "가성비": {"group":"기타"},
                "양많음": {"group":"기타"},
            },
            "food_specific_axes": {
                "삼겹살": {
                    "불맛":   {"group":"맛"},
                    "두툼함": {"group":"식감"},
                }
            },
        }
        with open(axes_path,"w",encoding="utf-8") as f:
            json.dump(axes, f, ensure_ascii=False, indent=2)
        info("stand_axes.json 생성 (더미)")

    if not os.path.exists(lexicon_path):
        lexicon = {
            "common":  {"전반적만족": {"positive":["맛있","최고","굿","짱","맛나"],
                                       "negative":["맛없","별로","최악","실망"]}},
            "shared":  {
                "불맛":   {"positive":["불맛","숯불","직화"],          "negative":["불맛없"]},
                "담백함": {"positive":["담백","깔끔","슴슴"],           "negative":["느끼","기름"]},
                "고소함": {"positive":["고소","버터"],                  "negative":[]},
                "육즙":   {"positive":["육즙","촉촉","부드러"],         "negative":["퍽퍽","푸석"]},
                "바삭함": {"positive":["바삭","겉바","크리스피"],       "negative":["눅눅"]},
                "가성비": {"positive":["가성비","저렴","착한 가격"],    "negative":["비싸","돈값"]},
                "양많음": {"positive":["양이 많","넉넉","푸짐","배부"], "negative":["양이 적","부족"]},
                "두툼함": {"positive":["두툼","두꺼"],                  "negative":["얇"]},
            },
            "food_specific": {
                "삼겹살": {
                    "불맛":   {"positive":["불맛","숯향","직화"], "negative":[]},
                    "두툼함": {"positive":["두툼","두꺼"],         "negative":["얇"]},
                }
            },
        }
        with open(lexicon_path,"w",encoding="utf-8") as f:
            json.dump(lexicon, f, ensure_ascii=False, indent=2)
        info("stand_lexicon.json 생성 (더미)")

    if not os.path.exists(rules_path):
        rules = {
            "scoring":            {"positive_hit": 1.0, "negative_hit": -0.5},
            "intensifiers":       {"정말": 1.3, "너무": 1.2, "완전": 1.3, "진짜": 1.2},
            "negations":          ["안","않","별로","전혀","못"],
            "rating_adjustments": {
                "전반적만족": {"taste_ge":[4,0.3],"taste_le":[2,-0.5],
                               "total_le":[2,-1.0],"total_ge_default":[5,0.3]},
                "양많음":     {"quantity_ge":[5,0.2],"quantity_le":[2,-0.2]},
            },
        }
        with open(rules_path,"w",encoding="utf-8") as f:
            json.dump(rules, f, ensure_ascii=False, indent=2)
        info("stand_rules.json 생성 (더미)")


# ══════════════════════════════════════════════════════════════
# STEP 4: API 응답 확인
# ══════════════════════════════════════════════════════════════

def test_api():
    section("STEP 4 — API 응답")
    info(f"대상: {API_BASE}")

    # /api/health
    try:
        r = http_requests.get(f"{API_BASE}/api/health", timeout=5)
        data = r.json()
        if data.get("ok"):
            ok(f"/api/health  keywords={data.get('keywords')}")
        else:
            fail(f"/api/health 실패: {data}")
            return False
    except Exception as e:
        fail(f"서버 연결 실패: {e}")
        print(f"\n  python app.py 를 다른 터미널에서 먼저 실행하세요")
        return False

    # /api/config
    r = http_requests.get(f"{API_BASE}/api/config?keyword={KEYWORD}", timeout=5)
    data = r.json()
    if "axes" in data:
        ok(f"/api/config  axes({len(data['axes'])})={data['axes'][:4]}")
    else:
        fail(f"/api/config: {data}")
        return False

    # /api/recommend
    axes = data["axes"]
    user_prefs = {ax: round(random.uniform(-0.5, 1.0), 2) for ax in axes[:3]}
    info(f"테스트 선호도: {user_prefs}")

    r = http_requests.post(
        f"{API_BASE}/api/recommend",
        json={"_keyword": KEYWORD, "text_preferences": user_prefs},
        timeout=10,
    )
    rec = r.json()
    if rec.get("results"):
        ok(f"/api/recommend  {rec['count']}개 결과")
        for i, res in enumerate(rec["results"][:3], 1):
            print(f"     {i}. {res['name']:20s}  유사도={res['similarity']:.3f}")
        return True
    else:
        fail(f"/api/recommend 결과 없음: {rec}")
        return False


# ══════════════════════════════════════════════════════════════
# STEP 5: 테스트 데이터 정리
# ══════════════════════════════════════════════════════════════

def cleanup_test_data():
    section("STEP 5 — 테스트 데이터 정리")
    from db import get_conn
    try:
        with get_conn() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    "DELETE FROM restaurants WHERE name LIKE %s",
                    (f"{TEST_PREFIX}%",)
                )
                deleted = cur.rowcount
        ok(f"테스트 식당 {deleted}개 삭제 (CASCADE로 리뷰/벡터도 삭제)")
        # 벡터 JSON 파일 삭제
        safe = KEYWORD.replace(" ","_")
        for ext in ("_vectors.json","_multimodal_vectors.json","_representative_images.json"):
            p = f"{safe}{ext}"
            if os.path.exists(p):
                os.remove(p); info(f"{p} 삭제")
    except Exception as e:
        fail(f"정리 실패: {e}")


# ══════════════════════════════════════════════════════════════
# 전체 실행
# ══════════════════════════════════════════════════════════════

def run_all():
    print(f"\n{BOLD}{'='*55}{RESET}")
    print(f"{BOLD}  TasteBridge 파이프라인 로컬 테스트{RESET}")
    print(f"{BOLD}  키워드: {KEYWORD}{RESET}")
    print(f"{BOLD}{'='*55}{RESET}")

    results = {}

    results["db"]   = test_db_connection()
    if not results["db"]:
        print(f"\n{RED}DB 연결 실패. 이후 테스트를 건너뜁니다.{RESET}")
        _print_summary(results); return

    results["data"] = test_insert_dummy()
    if not results["data"]:
        print(f"\n{YELLOW}더미 데이터 삽입 실패. 이후 테스트를 건너뜁니다.{RESET}")
        _print_summary(results); return

    results["prof"] = test_profiler()

    # API 테스트: app.py가 실행 중인지 확인
    section("app.py 실행 확인")
    info("app.py를 다른 터미널에서 실행한 뒤 Enter를 누르세요.")
    info("(지금 실행: python app.py)")
    try:
        input("  준비됐으면 Enter → ")
        results["api"] = test_api()
    except KeyboardInterrupt:
        info("API 테스트 건너뜀")
        results["api"] = None

    # 정리 여부 확인
    print()
    try:
        ans = input("  테스트 데이터를 삭제할까요? [y/N]: ").strip().lower()
        if ans == "y":
            cleanup_test_data()
    except KeyboardInterrupt:
        pass

    _print_summary(results)


def _print_summary(results):
    section("테스트 결과 요약")
    labels = {"db":"DB 연결","data":"더미 데이터","prof":"Food_profiler","api":"API 응답"}
    all_pass = True
    for key, label in labels.items():
        val = results.get(key)
        if val is True:   print(f"  {GREEN}PASS{RESET}  {label}")
        elif val is False: print(f"  {RED}FAIL{RESET}  {label}"); all_pass=False
        else:              print(f"  {YELLOW}SKIP{RESET}  {label}")
    print()
    if all_pass and all(v is not None for v in results.values()):
        print(f"  {GREEN}{BOLD}모든 테스트 통과!{RESET}")
    else:
        print(f"  {YELLOW}일부 테스트 실패 또는 스킵{RESET}")
    print()


# ── 진입점 ────────────────────────────────────────────────────

if __name__ == "__main__":
    if STEP == "db":
        test_db_connection()
    elif STEP == "data":
        if test_db_connection():
            test_insert_dummy()
    elif STEP == "prof":
        test_profiler()
    elif STEP == "api":
        test_api()
    elif STEP == "clean":
        cleanup_test_data()
    else:
        run_all()
