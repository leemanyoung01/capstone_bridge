"""
db.py — PostgreSQL 전용 연결 레이어
=====================================
모든 모듈이 이 파일만 import.

환경변수 DATABASE_URL 우선, 없으면 기본값 사용.
  로컬 : postgresql://postgres:precap@localhost:5432/tastebridge
  AWS  : postgresql://user:pw@<rds-endpoint>:5432/tastebridge

설치: pip install psycopg2-binary python-dotenv
"""

import json
import os
import re
from contextlib import contextmanager
from datetime import datetime
from typing import Optional

import psycopg2
import psycopg2.extras
from psycopg2 import pool

try:
    from dotenv import load_dotenv
    load_dotenv()
except ImportError:
    pass

DATABASE_URL: str = os.environ.get(
    "DATABASE_URL",
    "postgresql://postgres:precap@localhost:5432/tastebridge",
)

_pool: Optional[pool.ThreadedConnectionPool] = None


def _get_pool() -> pool.ThreadedConnectionPool:
    global _pool
    if _pool is None or _pool.closed:
        _pool = pool.ThreadedConnectionPool(
            minconn=2,
            maxconn=10,
            dsn=DATABASE_URL,
            cursor_factory=psycopg2.extras.RealDictCursor,
        )
    return _pool


@contextmanager
def get_conn():
    """풀에서 커넥션을 빌려 자동 커밋/롤백/반납."""
    p = _get_pool()
    conn = p.getconn()
    try:
        yield conn
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        p.putconn(conn)


def close_pool():
    global _pool
    if _pool and not _pool.closed:
        _pool.closeall()
    _pool = None


# ──────────────────────────────────────────────
# 날짜 파싱 유틸
# ──────────────────────────────────────────────

def _parse_date(raw) -> Optional[str]:
    """
    네이버 날짜 형식 → 'YYYY-MM-DD' 문자열 변환.
    PostgreSQL TIMESTAMP 컬럼에 안전하게 삽입 가능.

    처리 가능한 입력 예시:
      "4.12.일"               → "2025-04-12"
      "2024.04.12"            → "2024-04-12"
      "2024.04.12. 오후 3:20" → "2024-04-12"
      "1일 전", "방금"        → None  (상대시간 변환 불가)
      NaN, None, ""           → None
    """
    if raw is None:
        return None

    # pandas NaN 처리
    try:
        import math
        if isinstance(raw, float) and math.isnan(raw):
            return None
    except Exception:
        pass

    raw = str(raw).strip()
    if not raw or raw.lower() in ("nan", "none", "nat"):
        return None

    # 상대시간은 변환 불가 → None
    if any(k in raw for k in ("전", "방금", "ago", "just")):
        return None

    nums = re.findall(r"\d+", raw)
    if not nums:
        return None

    try:
        now = datetime.now()
        if len(nums) >= 3 and len(nums[0]) == 4:
            # "2024.04.12..." 형태 — 연도 4자리로 시작
            y, m, d = int(nums[0]), int(nums[1]), int(nums[2])
        elif len(nums) >= 2:
            # "4.12.일", "04.12" 형태 — 연도 없음 → 올해 사용
            y, m, d = now.year, int(nums[0]), int(nums[1])
        else:
            return None

        # 유효한 날짜인지 검증 (예: 13월, 32일 방지)
        datetime(y, m, d)
        return f"{y}-{m:02d}-{d:02d}"

    except (ValueError, IndexError):
        return None


# ──────────────────────────────────────────────
# restaurants
# ──────────────────────────────────────────────

def upsert_restaurant(conn, info: dict) -> int:
    with conn.cursor() as cur:
        cur.execute("""
            INSERT INTO restaurants
                (name, place_id, naver_url, category, address,
                 road_address, phone, lat, lng, source)
            VALUES
                (%(name)s, %(place_id)s, %(naver_url)s, %(category)s, %(address)s,
                 %(road_address)s, %(phone)s, %(lat)s, %(lng)s, %(source)s)
            ON CONFLICT (name) DO UPDATE SET
                place_id     = COALESCE(NULLIF(EXCLUDED.place_id,''),     restaurants.place_id),
                naver_url    = COALESCE(NULLIF(EXCLUDED.naver_url,''),    restaurants.naver_url),
                category     = COALESCE(NULLIF(EXCLUDED.category,''),     restaurants.category),
                address      = COALESCE(NULLIF(EXCLUDED.address,''),      restaurants.address),
                road_address = COALESCE(NULLIF(EXCLUDED.road_address,''), restaurants.road_address),
                phone        = COALESCE(NULLIF(EXCLUDED.phone,''),        restaurants.phone),
                lat  = CASE WHEN EXCLUDED.lat  != 0 THEN EXCLUDED.lat  ELSE restaurants.lat  END,
                lng  = CASE WHEN EXCLUDED.lng  != 0 THEN EXCLUDED.lng  ELSE restaurants.lng  END,
                source     = EXCLUDED.source,
                updated_at = CURRENT_TIMESTAMP
            RETURNING restaurant_id
        """, {
            "name":         str(info.get("name", "")),
            "place_id":     str(info.get("place_id", "")),
            "naver_url":    str(info.get("naver_url", "")),
            "category":     str(info.get("category", "")),
            "address":      str(info.get("address", "")),
            "road_address": str(info.get("road_address", "")),
            "phone":        str(info.get("phone", "")),
            "lat":          float(info.get("lat", 0) or 0),
            "lng":          float(info.get("lng", 0) or 0),
            "source":       str(info.get("source", "auto")),
        })
        return cur.fetchone()["restaurant_id"]


def get_all_restaurants(conn) -> list[dict]:
    with conn.cursor() as cur:
        cur.execute("SELECT * FROM restaurants ORDER BY name")
        return [dict(r) for r in cur.fetchall()]


def get_restaurant_by_name(conn, name: str) -> Optional[dict]:
    with conn.cursor() as cur:
        cur.execute("SELECT * FROM restaurants WHERE name = %s", (name,))
        row = cur.fetchone()
        return dict(row) if row else None


# ──────────────────────────────────────────────
# reviews
# ──────────────────────────────────────────────

_HAS_CRAWL_KEYWORD_COL: Optional[bool] = None
_HAS_REVIEW_KEYWORDS_TABLE: Optional[bool] = None


def _has_crawl_keyword_col(cur) -> bool:
    """reviews.crawl_keyword 컬럼 존재 여부를 1회 검사 후 캐시. ALTER 미적용 환경에서도 안전 동작."""
    global _HAS_CRAWL_KEYWORD_COL
    if _HAS_CRAWL_KEYWORD_COL is not None:
        return _HAS_CRAWL_KEYWORD_COL
    cur.execute("""
        SELECT 1 FROM information_schema.columns
        WHERE table_name = 'reviews' AND column_name = 'crawl_keyword'
        LIMIT 1
    """)
    _HAS_CRAWL_KEYWORD_COL = cur.fetchone() is not None
    return _HAS_CRAWL_KEYWORD_COL


def _has_review_keywords_table(cur) -> bool:
    """review_keywords 테이블 존재 여부를 1회 검사 후 캐시. 마이그레이션 미적용 환경 호환."""
    global _HAS_REVIEW_KEYWORDS_TABLE
    if _HAS_REVIEW_KEYWORDS_TABLE is not None:
        return _HAS_REVIEW_KEYWORDS_TABLE
    cur.execute("""
        SELECT 1 FROM information_schema.tables
        WHERE table_name = 'review_keywords'
        LIMIT 1
    """)
    _HAS_REVIEW_KEYWORDS_TABLE = cur.fetchone() is not None
    return _HAS_REVIEW_KEYWORDS_TABLE


def _link_review_keyword(cur, review_id: int, keyword: str) -> None:
    """(review_id, keyword) 관계를 review_keywords에 INSERT. 이미 있으면 무시.
    테이블이 없으면 조용히 skip — 마이그레이션 미적용 환경에서도 깨지지 않게."""
    if not review_id or not keyword:
        return
    if not _has_review_keywords_table(cur):
        return
    cur.execute("""
        INSERT INTO review_keywords (review_id, keyword)
        VALUES (%s, %s)
        ON CONFLICT DO NOTHING
    """, (int(review_id), str(keyword).strip()))


def insert_review(conn, restaurant_id: int, review: dict) -> Optional[int]:
    """
    리뷰 INSERT. naver_review_id 중복이면 무시하고 None 반환.
    날짜는 _parse_date()로 안전하게 변환 후 삽입.
    크롤러(Date), csv_to_json(reviewed_at) 양쪽 키 모두 처리.
    crawl_keyword 컬럼이 존재하면 함께 저장 (없으면 무시 — ALTER 미적용 호환).
    """
    # "Date"(크롤러), "reviewed_at"(csv_to_json 변환 후), "date" 모두 시도
    raw_date = (
        review.get("Date")
        or review.get("reviewed_at")
        or review.get("date")
    )
    parsed_date = _parse_date(raw_date)

    crawl_kw = (
        review.get("CrawlKeyword")
        or review.get("crawl_keyword")
        or ""
    )
    crawl_kw = str(crawl_kw or "").strip() or None

    params = {
        "rid":     restaurant_id,
        "nrid":    (
            review.get("ReviewID")
            or review.get("naver_review_id")
            or review.get("review_id")
        ) or None,
        "author":  review.get("Author")  or review.get("author", ""),
        "content": review.get("Review")  or review.get("content", ""),
        "rating":  float(review.get("Total") or review.get("rating") or 0),
        "menu":    review.get("Menu")    or review.get("menu", ""),
        "vc":      int(review.get("VisitCount") or review.get("visit_count") or 0),
        "hi":      bool(review.get("HasPicture") or review.get("has_image") or False),
        "vk":      review.get("VotedKeywords") or review.get("voted_keywords", ""),
        "reply":   review.get("OwnerReply") or review.get("owner_reply", ""),
        "date":    parsed_date,
        "ck":      crawl_kw,
    }

    with conn.cursor() as cur:
        if _has_crawl_keyword_col(cur):
            cur.execute("""
                INSERT INTO reviews
                    (restaurant_id, naver_review_id, author, content, rating,
                     menu, visit_count, has_image, voted_keywords, owner_reply, reviewed_at,
                     crawl_keyword)
                VALUES
                    (%(rid)s, %(nrid)s, %(author)s, %(content)s, %(rating)s,
                     %(menu)s, %(vc)s, %(hi)s, %(vk)s, %(reply)s, %(date)s,
                     %(ck)s)
                ON CONFLICT (naver_review_id) DO NOTHING
                RETURNING review_id
            """, params)
            row = cur.fetchone()
            # 중복(naver_review_id) 행이라도 crawl_keyword가 비어있으면 보강 업데이트.
            # 이미지 재삽입은 하지 않도록 row=None을 그대로 유지해 함수가 None을 리턴.
            if row is None and crawl_kw and params["nrid"]:
                cur.execute("""
                    UPDATE reviews
                       SET crawl_keyword = %s
                     WHERE naver_review_id = %s
                       AND (crawl_keyword IS NULL OR crawl_keyword = '')
                """, (crawl_kw, params["nrid"]))
        else:
            cur.execute("""
                INSERT INTO reviews
                    (restaurant_id, naver_review_id, author, content, rating,
                     menu, visit_count, has_image, voted_keywords, owner_reply, reviewed_at)
                VALUES
                    (%(rid)s, %(nrid)s, %(author)s, %(content)s, %(rating)s,
                     %(menu)s, %(vc)s, %(hi)s, %(vk)s, %(reply)s, %(date)s)
                ON CONFLICT (naver_review_id) DO NOTHING
                RETURNING review_id
            """, params)
            row = cur.fetchone()

        # ── 중복 리뷰여도 review_keywords 관계는 등록해야 한다 ──
        # 같은 카페가 다른 keyword로 재크롤링될 때, naver_review_id 충돌로 row=None이
        # 되더라도 keyword 관계를 잃지 않게 기존 review_id를 조회해 link만 추가한다.
        if crawl_kw and params["nrid"]:
            if row is not None:
                _link_review_keyword(cur, row["review_id"], crawl_kw)
            else:
                # 기존 review_id 조회 후 link
                cur.execute(
                    "SELECT review_id FROM reviews WHERE naver_review_id = %s",
                    (params["nrid"],),
                )
                existing = cur.fetchone()
                if existing:
                    _link_review_keyword(cur, existing["review_id"], crawl_kw)

        if row is None:
            return None
        review_id = row["review_id"]

        # 이미지 저장
        for col_url, col_path in [("ImageURLs", "ImagePaths"), ("image_urls", "image_paths")]:
            raw_u = str(review.get(col_url, "") or "")
            raw_p = str(review.get(col_path, "") or "")
            if raw_u and raw_u != "nan":
                urls = [u.strip() for u in raw_u.split("|") if u.strip() and u.strip() != "nan"]
                paths = [p.strip() for p in raw_p.split("|") if p.strip() and p.strip() != "nan"]
                for i, url in enumerate(urls):
                    path = paths[i] if i < len(paths) else ""
                    cur.execute(
                        "INSERT INTO review_images (review_id, image_url, image_path) VALUES (%s,%s,%s)",
                        (review_id, url, path),
                    )
                break
        return review_id


# ──────────────────────────────────────────────
# 키워드 교차 오염 방지 설정
# ──────────────────────────────────────────────
# 식당별 keyword 언급 리뷰 비율(=density)이 이 값 미만이면 후보에서 제외.
# 단 식당명/카테고리/crawl_keyword 보호 조건이 걸리면 제외하지 않음.
# 함수 인자(density_threshold)로도 상시 override 가능.
DEFAULT_DENSITY_THRESHOLD = 0.02

# keyword별 카테고리 negative filter — restaurants.category가 substring으로 매칭되면 제외.
# 예) 청국장 검색에 "샐러드"·"카페" 카테고리는 부적합.
NEGATIVE_CATEGORIES_BY_KEYWORD: dict[str, list[str]] = {
    "청국장":   ["샐러드", "피자", "파스타", "카페", "디저트", "베이커리", "브런치", "양식"],
    "찌개":     ["샐러드", "피자", "파스타", "카페", "디저트", "베이커리"],
    "찜":       ["샐러드", "피자", "파스타", "카페", "디저트"],
    "찜류":     ["샐러드", "피자", "파스타", "카페", "디저트"],
    "김밥":     ["디저트", "카페", "베이커리", "브런치", "피자"],
    "분식":     ["피자", "파스타", "양식"],
    "회":       ["디저트", "카페", "베이커리"],
    "조개":     ["디저트", "카페", "베이커리"],
    "삼겹살":   ["디저트", "카페", "베이커리"],
    "구이":     ["디저트", "카페", "베이커리"],
    "버거":     ["한식", "디저트", "카페"],
    "샐러드":   ["치킨", "분식", "한식", "고기"],
}

# 식당명에 keyword 핵심어가 들어가면 density가 낮아도 보호.
# substring 매치(소문자/공백 정규화 후).
PROTECT_NAME_PATTERNS: dict[str, list[str]] = {
    "청국장": ["청국장"],
    "김밥":   ["김밥"],
    "회":     ["회센터", "수산", "활어", "사시미", "회집", "회관", "어시장"],
    "조개":   ["조개", "바지락", "키조개"],
    "찜":     ["찜", "아구찜", "갈비찜"],
    "찜류":   ["찜", "아구찜", "갈비찜"],
    "버거":   ["버거", "버~거", "burger"],
    "샐러드": ["샐러드", "salad"],
    "치킨":   ["치킨", "통닭", "닭"],
    "피자":   ["피자", "pizza"],
    "삼겹살": ["삼겹", "고깃", "정육"],
}


def _name_protected(name: str, keyword: str) -> bool:
    if not name:
        return False
    n = name.lower().replace(" ", "")
    for pat in PROTECT_NAME_PATTERNS.get(keyword, [keyword]):
        if pat and pat.lower().replace(" ", "") in n:
            return True
    return False


def _category_excluded(category: str, keyword: str) -> bool:
    if not category:
        return False
    cats = NEGATIVE_CATEGORIES_BY_KEYWORD.get(keyword) or []
    c = category.lower()
    return any(neg.lower() in c for neg in cats)


def _build_source_restaurant_set(conn, keyword: str, terms: list[str]) -> tuple[set, set, set]:
    """
    keyword의 source restaurant_id 집합을 세 신호로 구성:
      (1) review_keywords.keyword 가 keyword와 일치하는 review들의 restaurant_id (최우선 — N:M)
      (2) review_images.image_url에 `/reviews/<keyword>/` (raw 또는 URL-encoded) prefix
      (3) reviews.crawl_keyword 가 keyword/alias 중 하나와 정확히 일치

    반환: (src_by_link, src_by_prefix, src_by_crawl) — 세 집합.
    union이 source ground truth. 모두 비면 호출자가 호환 모드 fallback을 결정.
    """
    import urllib.parse as _up

    # (1) review_keywords link set (가장 명확한 신호) — 테이블 없으면 빈 집합
    src_by_link: set = set()
    with conn.cursor() as cur:
        if _has_review_keywords_table(cur):
            cur.execute("""
                SELECT DISTINCT v.restaurant_id
                FROM review_keywords rk
                JOIN reviews v ON v.review_id = rk.review_id
                WHERE rk.keyword = %s
                  AND v.restaurant_id IS NOT NULL
            """, (keyword,))
            src_by_link = {r["restaurant_id"] for r in cur.fetchall()}

    # (2) prefix-based set
    enc = _up.quote(keyword, safe="")
    patterns = [f"%/reviews/{keyword}/%"]
    if enc != keyword:
        patterns.append(f"%/reviews/{enc}/%")

    src_by_prefix: set = set()
    with conn.cursor() as cur:
        cond = " OR ".join(["ri.image_url ILIKE %s"] * len(patterns))
        cur.execute(f"""
            SELECT DISTINCT v.restaurant_id
            FROM reviews v
            JOIN review_images ri ON ri.review_id = v.review_id
            WHERE ({cond})
              AND v.restaurant_id IS NOT NULL
        """, patterns)
        src_by_prefix = {r["restaurant_id"] for r in cur.fetchall()}

    # (3) crawl_keyword 정확 일치 (컬럼 미존재 시 빈 집합)
    src_by_crawl: set = set()
    with conn.cursor() as cur:
        if _has_crawl_keyword_col(cur):
            placeholders = ",".join(["%s"] * len(terms))
            cur.execute(f"""
                SELECT DISTINCT v.restaurant_id
                FROM reviews v
                WHERE v.crawl_keyword IN ({placeholders})
                  AND v.restaurant_id IS NOT NULL
            """, terms)
            src_by_crawl = {r["restaurant_id"] for r in cur.fetchall()}

    return src_by_link, src_by_prefix, src_by_crawl


def get_reviews_for_profiler(
    conn,
    keyword: str,
    aliases: Optional[list[str]] = None,
    *,
    density_threshold: float = DEFAULT_DENSITY_THRESHOLD,
    return_debug: bool = False,
):
    """
    Food/Multimodal Profiler용 리뷰 조회. **Keyword source boundary**가 핵심.

    흐름:
      A0) source_set = (review_images.image_url에 `/reviews/<keyword>/` 가진 식당)
                       ∪ (reviews.crawl_keyword = <keyword/alias> 식당)
      A1) text/alias로 잡히는 식당 (진단용 — 실제 후보 산정엔 영향 X)
      B)  candidate_set 결정:
            - source_set 이 있으면  → candidate_set = source_set (ground truth)
            - source_set 이 비었으면 → candidate_set = text_alias_set (옛 데이터 호환, 경고)
      C)  candidate_set 안에서만 density/category negative/이름 보호 적용.
          단 source_set 식당은 prefix 자체가 보호 신호이므로 density/category 면제.
      D)  최종 통과 식당의 keyword-hit 리뷰만 반환.

    이로써 리뷰 본문에 '회'·'한상' 같은 단어가 들어갔다는 이유만으로
    다른 keyword 폴더로 크롤링된 식당이 끌려오는 문제를 차단한다.

    반환:
      return_debug=False  → list[dict]
      return_debug=True   → (list[dict], debug_dict)
    """
    terms = list(dict.fromkeys([keyword] + (aliases or [])))
    if not terms:
        return ([], {}) if return_debug else []

    # ── (A0) source restaurant set ──
    src_by_link, src_by_prefix, src_by_crawl = _build_source_restaurant_set(conn, keyword, terms)
    source_set = src_by_link | src_by_prefix | src_by_crawl

    # ── (A1) text/alias로 잡히는 식당 — 진단·비교용 ──
    text_alias_cond = " OR ".join(["(r2.content ILIKE %s OR r2.menu ILIKE %s)"] * len(terms))
    text_alias_params: list = []
    for t in terms:
        text_alias_params += [f"%{t}%", f"%{t}%"]
    text_alias_set: set = set()
    with conn.cursor() as cur:
        cur.execute(f"""
            SELECT DISTINCT r2.restaurant_id
            FROM reviews r2
            WHERE ({text_alias_cond})
              AND r2.restaurant_id IS NOT NULL
        """, text_alias_params)
        text_alias_set = {r["restaurant_id"] for r in cur.fetchall()}

    # ── (B) candidate_set 결정 ──
    source_set_empty_fallback = False
    if source_set:
        # ground truth = source. 텍스트는 보조 — 본문에 keyword가 안 나오는 식당이라도
        # /reviews/<kw>/ prefix가 있으면 그 식당은 정당한 후보.
        candidate_set = source_set
    else:
        # 옛 데이터: prefix도 crawl_keyword도 없음. 안전망으로 text/alias만 사용.
        candidate_set = text_alias_set
        source_set_empty_fallback = True

    excluded_no_source_prefix = len(text_alias_set - source_set)

    if not candidate_set:
        debug = {
            "keyword": keyword,
            "search_terms": terms,
            "density_threshold": density_threshold,
            "review_keywords_linked_restaurants": len(src_by_link),
            "source_by_prefix_restaurants": len(src_by_prefix),
            "crawl_keyword_restaurants": len(src_by_crawl),
            "text_alias_matched_restaurants": len(text_alias_set),
            "candidate_restaurants": 0,
            "kept_restaurants": 0,
            "final_restaurants": 0,
            "excluded_no_source_prefix": excluded_no_source_prefix,
            "excluded_by_density": 0,
            "excluded_by_category": 0,
            "protected_by_name": 0,
            "protected_by_crawl_keyword": 0,
            "protected_by_prefix": 0,
            "protected_by_review_link": 0,
            "source_set_empty_fallback": source_set_empty_fallback,
            "samples": [],
        }
        return ([], debug) if return_debug else []

    # ── (C) candidate_set 안에서 식당별 통계 + 필터링 ──
    hit_cond = " OR ".join(["(r2.content ILIKE %s OR r2.menu ILIKE %s)"] * len(terms))
    hit_params: list = []
    for t in terms:
        hit_params += [f"%{t}%", f"%{t}%"]
    crawl_match_cond = " OR ".join(["r2.crawl_keyword ILIKE %s"] * len(terms))
    crawl_params = [f"%{t}%" for t in terms]

    candidate_ids = list(candidate_set)
    sql_stats = f"""
        SELECT
            rst.restaurant_id,
            rst.name,
            COALESCE(rst.category, '')                              AS category,
            COUNT(r2.review_id)                                     AS total_reviews,
            SUM(CASE WHEN ({hit_cond}) THEN 1 ELSE 0 END)::int      AS hit_reviews,
            SUM(CASE WHEN ({crawl_match_cond}) THEN 1 ELSE 0 END)::int AS crawl_hits
        FROM restaurants rst
        LEFT JOIN reviews r2 ON r2.restaurant_id = rst.restaurant_id
        WHERE rst.restaurant_id = ANY(%s)
        GROUP BY rst.restaurant_id, rst.name, rst.category
    """
    stats_params = list(hit_params) + list(crawl_params) + [candidate_ids]

    candidates: list[dict] = []
    with conn.cursor() as cur:
        cur.execute(sql_stats, stats_params)
        for row in cur.fetchall():
            candidates.append(dict(row))

    kept_ids: list[int] = []
    samples: list[dict] = []
    excluded_by_density = 0
    excluded_by_category = 0
    protected_by_name = 0
    protected_by_crawl = 0
    protected_by_prefix = 0
    protected_by_review_link = 0

    for c in candidates:
        rid = c["restaurant_id"]
        name = c["name"] or ""
        category = c["category"] or ""
        total = int(c["total_reviews"] or 0)
        hits = int(c["hit_reviews"] or 0)
        crawl_hits = int(c["crawl_hits"] or 0)
        density = (hits / total) if total else 0.0

        # source boundary 신호 (review_keywords link / prefix / crawl_keyword) 자체가
        # 강한 보호 신호. 이 식당은 본문에 keyword가 안 나와도 정당 — density·category 면제.
        is_link_source = rid in src_by_link
        is_prefix_source = rid in src_by_prefix
        is_crawl_source = rid in src_by_crawl
        protected_name = _name_protected(name, keyword)
        protected_crawl = (crawl_hits > 0) or is_crawl_source
        protected = (protected_name or protected_crawl
                     or is_prefix_source or is_link_source)
        cat_excluded = _category_excluded(category, keyword)

        excluded_reason = None
        if cat_excluded and not protected:
            excluded_reason = "category_negative_filter"
            excluded_by_category += 1
        elif density < density_threshold and not protected:
            excluded_reason = "density_below_threshold"
            excluded_by_density += 1
        else:
            kept_ids.append(rid)
            if is_link_source:
                protected_by_review_link += 1
            if is_prefix_source:
                protected_by_prefix += 1
            if protected_name:
                protected_by_name += 1
            if protected_crawl:
                protected_by_crawl += 1

        if len(samples) < 25:
            samples.append({
                "restaurant_id": rid,
                "name": name,
                "category": category,
                "total_reviews": total,
                "hit_reviews": hits,
                "crawl_hits": crawl_hits,
                "density": round(density, 4),
                "protected_by_review_link": is_link_source,
                "protected_by_prefix": is_prefix_source,
                "protected_by_name": protected_name,
                "protected_by_crawl": protected_crawl,
                "category_excluded": cat_excluded,
                "kept": excluded_reason is None,
                "excluded_reason": excluded_reason,
            })

    debug = {
        "keyword": keyword,
        "search_terms": terms,
        "density_threshold": density_threshold,
        "review_keywords_linked_restaurants": len(src_by_link),
        "source_by_prefix_restaurants": len(src_by_prefix),
        "crawl_keyword_restaurants": len(src_by_crawl),
        "text_alias_matched_restaurants": len(text_alias_set),
        "candidate_restaurants": len(candidates),
        "kept_restaurants": len(kept_ids),
        "final_restaurants": len(kept_ids),
        "excluded_no_source_prefix": excluded_no_source_prefix,
        "excluded_by_density": excluded_by_density,
        "excluded_by_category": excluded_by_category,
        "protected_by_name": protected_by_name,
        "protected_by_crawl_keyword": protected_by_crawl,
        "protected_by_prefix": protected_by_prefix,
        "protected_by_review_link": protected_by_review_link,
        "source_set_empty_fallback": source_set_empty_fallback,
        "samples": samples,
    }

    if not kept_ids:
        return ([], debug) if return_debug else []

    # ── (D) 통과한 식당의 keyword-hit 리뷰만 SELECT ──
    # 식당 후보는 source boundary로 좁혀졌으므로 여기서는 기존 Food_profiler 의미를 유지.
    # text/menu에 keyword가 들어간 리뷰만 사용해야 kw_count / low_review_penalty / confidence
    # 계산이 의도대로 동작한다.
    # 단, source_by_prefix 식당이 keyword-hit 리뷰가 0건이라도 자동 제외되지 않게
    # ImageURLs가 있는 행은 추가로 받아온다 (Multimodal 이미지 평가 안정성).
    review_cond = " OR ".join(["(r.content ILIKE %s OR r.menu ILIKE %s)"] * len(terms))
    review_params: list = []
    for t in terms:
        review_params += [f"%{t}%", f"%{t}%"]

    sql_rows = f"""
        SELECT
            r.review_id,
            rst.restaurant_id  AS "RestaurantID",
            rst.name          AS "Restaurant",
            r.content         AS "Review",
            r.rating          AS "Total",
            r.menu            AS "Menu",
            r.reviewed_at     AS "Date",
            r.has_image       AS "HasPicture",
            r.voted_keywords  AS "VotedKeywords",
            r.author          AS "Author",
            r.visit_count     AS "VisitCount",
            r.owner_reply     AS "OwnerReply",
            COALESCE(string_agg(ri.image_url,  ' | ' ORDER BY ri.image_id), '') AS "ImageURLs",
            COALESCE(string_agg(ri.image_path, ' | ' ORDER BY ri.image_id), '') AS "ImagePaths"
        FROM reviews r
        JOIN restaurants rst ON rst.restaurant_id = r.restaurant_id
        LEFT JOIN review_images ri ON ri.review_id = r.review_id
        WHERE rst.restaurant_id = ANY(%s)
          AND ({review_cond})
        GROUP BY r.review_id, rst.restaurant_id, rst.name
        ORDER BY rst.name, r.reviewed_at DESC NULLS LAST
    """
    with conn.cursor() as cur:
        cur.execute(sql_rows, [kept_ids] + review_params)
        rows = [dict(r) for r in cur.fetchall()]

    debug["returned_reviews"] = len(rows)
    return (rows, debug) if return_debug else rows


# ──────────────────────────────────────────────
# 식당별 clean image lookup (Inference 카드 fallback)
# ──────────────────────────────────────────────

def get_best_restaurant_image(
    conn,
    keyword: str,
    restaurant_id: int,
    top_axes: Optional[list[str]] = None,
    limit_scan: int = 50,
) -> dict:
    """
    Inference 카드용 — 해당 식당의 `/reviews/<keyword>/` prefix를 가진 clean image를
    한 장 골라 반환. Gallery 대표 이미지 6장과 분리된 식당별 fallback.

    선택 우선순위:
      1) image_url에 `/reviews/<keyword>/` (raw 또는 URL-encoded) prefix가 있는 행
      2) top_axes 관련 단어가 review.content에 포함된 행을 가산점
      3) 그래도 부족하면 prefix만 맞는 최신 review_image

    스키마 변경 없이 SELECT만으로 구현. 못 찾으면 빈 dict.
    """
    import urllib.parse as _up

    if not restaurant_id or not keyword:
        return {}

    enc = _up.quote(keyword, safe="")
    patterns = [f"%/reviews/{keyword}/%"]
    if enc != keyword:
        patterns.append(f"%/reviews/{enc}/%")
    prefix_cond = " OR ".join(["ri.image_url ILIKE %s"] * len(patterns))

    # top_axes 키워드 → ILIKE 패턴 (있으면 가산점)
    axis_terms: list[str] = []
    if top_axes:
        for ax in top_axes:
            if isinstance(ax, str) and ax.strip():
                axis_terms.append(ax.strip())
    axis_terms = list(dict.fromkeys(axis_terms))[:5]

    if axis_terms:
        axis_score_expr = " + ".join([
            f"(CASE WHEN COALESCE(v.content,'') ILIKE %s THEN 1 ELSE 0 END)"
            for _ in axis_terms
        ])
        axis_params = [f"%{t}%" for t in axis_terms]
    else:
        axis_score_expr = "0"
        axis_params = []

    sql = f"""
        SELECT
            ri.image_id,
            ri.image_url,
            ri.image_path,
            v.review_id,
            v.content,
            ({axis_score_expr})::int AS axis_relevance,
            v.has_image,
            COALESCE(v.rating, 0)    AS rating
        FROM review_images ri
        JOIN reviews v ON v.review_id = ri.review_id
        WHERE v.restaurant_id = %s
          AND ({prefix_cond})
        ORDER BY axis_relevance DESC, v.rating DESC NULLS LAST, ri.image_id DESC
        LIMIT %s
    """
    params: list = list(axis_params) + [restaurant_id] + list(patterns) + [limit_scan]

    with conn.cursor() as cur:
        cur.execute(sql, params)
        row = cur.fetchone()

    if not row:
        return {}
    return {
        "image_src":      row.get("image_url", "") or "",
        "image_path":     row.get("image_path", "") or "",
        "review_snippet": (row.get("content") or "")[:120],
        "axis_relevance": int(row.get("axis_relevance") or 0),
        "source":         "restaurant_clean_image_fallback",
    }


# ──────────────────────────────────────────────
# restaurant_vectors (맛 벡터)
# ──────────────────────────────────────────────

def upsert_taste_profile(conn, restaurant_id: int, keyword: str, profile: dict):
    with conn.cursor() as cur:
        cur.execute("""
            INSERT INTO restaurant_vectors
                (restaurant_id, keyword, text_vector, evidence_json,
                 avg_rating, avg_taste, review_count, keyword_reviews, scored_reviews)
            VALUES
                (%(rid)s, %(kw)s, %(tv)s, %(ev)s,
                 %(ar)s,  %(at)s, %(rc)s, %(kr)s,    %(sr)s)
            ON CONFLICT (restaurant_id, keyword) DO UPDATE SET
                text_vector     = EXCLUDED.text_vector,
                evidence_json   = EXCLUDED.evidence_json,
                avg_rating      = EXCLUDED.avg_rating,
                avg_taste       = EXCLUDED.avg_taste,
                review_count    = EXCLUDED.review_count,
                keyword_reviews = EXCLUDED.keyword_reviews,
                scored_reviews  = EXCLUDED.scored_reviews
        """, {
            "rid": restaurant_id,
            "kw":  keyword,
            "tv":  json.dumps(profile.get("normalized", {}), ensure_ascii=False),
            "ev":  json.dumps(profile.get("evidence", {}), ensure_ascii=False),
            "ar":  float(profile.get("avg_total", 0)),
            "at":  float(profile.get("avg_taste", 0)),
            "rc":  int(profile.get("total_reviews", 0)),
            "kr":  int(profile.get("keyword_reviews", 0)),
            "sr":  int(profile.get("scored_reviews", 0)),
        })


def upsert_multimodal_profile(conn, restaurant_id: int, keyword: str, profile: dict):
    with conn.cursor() as cur:
        cur.execute("""
            INSERT INTO restaurant_vectors
                (restaurant_id, keyword, text_vector, image_vector,
                 fused_vector, image_coverage, has_image_data)
            VALUES
                (%(rid)s, %(kw)s, %(tv)s, %(iv)s,
                 %(fv)s,  %(ic)s, %(hid)s)
            ON CONFLICT (restaurant_id, keyword) DO UPDATE SET
                image_vector   = EXCLUDED.image_vector,
                fused_vector   = EXCLUDED.fused_vector,
                image_coverage = EXCLUDED.image_coverage,
                has_image_data = EXCLUDED.has_image_data
        """, {
            "rid": restaurant_id,
            "kw":  keyword,
            "tv":  json.dumps(profile.get("text_only_vector", {}), ensure_ascii=False),
            "iv":  json.dumps(profile.get("image_sentiment", {}), ensure_ascii=False),
            "fv":  json.dumps(profile.get("fused_vector", {}), ensure_ascii=False),
            "ic":  float(profile.get("fused_vector", {}).get("_img_coverage", 0)),
            "hid": bool(profile.get("has_image_data", False)),
        })


def get_profiles_by_keyword(conn, keyword: str) -> list[dict]:
    with conn.cursor() as cur:
        cur.execute("""
            SELECT rv.*, r.name, r.naver_url, r.road_address,
                   r.address, r.phone, r.category
            FROM restaurant_vectors rv
            JOIN restaurants r ON r.restaurant_id = rv.restaurant_id
            WHERE rv.keyword = %s
        """, (keyword,))
        rows = cur.fetchall()

    result = []
    for row in rows:
        d = dict(row)
        for field in ("text_vector", "image_vector", "fused_vector", "evidence_json", "semantic_vector"):
            raw = d.pop(field, None)
            out_key = {
                "text_vector": "text_vector",
                "image_vector": "image_vector",
                "fused_vector": "fused_vector",
                "evidence_json": "evidence",
                "semantic_vector": "semantic_vector",
            }[field]
            d[out_key] = json.loads(raw) if isinstance(raw, str) else (raw or {})
        result.append(d)
    return result


def get_all_keywords(conn) -> list[str]:
    with conn.cursor() as cur:
        cur.execute("SELECT DISTINCT keyword FROM restaurant_vectors ORDER BY keyword")
        return [r["keyword"] for r in cur.fetchall()]


# ──────────────────────────────────────────────
# axes_config
# ──────────────────────────────────────────────

def upsert_axes_config(conn, keyword: str, axes_dict: dict):
    with conn.cursor() as cur:
        for axis_name, info in axes_dict.items():
            cur.execute("""
                INSERT INTO axes_config
                    (keyword, axis_name, group_name, positive_kws, negative_kws,
                     clip_prompt_pos, clip_prompt_neg, is_meta)
                VALUES (%s,%s,%s,%s,%s,%s,%s,%s)
                ON CONFLICT (keyword, axis_name) DO UPDATE SET
                    group_name      = EXCLUDED.group_name,
                    positive_kws    = EXCLUDED.positive_kws,
                    negative_kws    = EXCLUDED.negative_kws,
                    clip_prompt_pos = EXCLUDED.clip_prompt_pos,
                    clip_prompt_neg = EXCLUDED.clip_prompt_neg,
                    is_meta         = EXCLUDED.is_meta
            """, (
                keyword,
                axis_name,
                info.get("group", "기타"),
                ",".join(info.get("positive_keywords", info.get("positive", []))),
                ",".join(info.get("negative_keywords", info.get("negative", []))),
                ",".join(info.get("clip_prompt_pos", [])),
                ",".join(info.get("clip_prompt_neg", [])),
                bool(info.get("is_meta", False)),
            ))


def get_axes_config(conn, keyword: str) -> dict:
    with conn.cursor() as cur:
        cur.execute("SELECT * FROM axes_config WHERE keyword = %s", (keyword,))
        rows = cur.fetchall()

    result = {}
    for row in rows:
        d = dict(row)
        result[d["axis_name"]] = {
            "group": d.get("group_name") or "기타",
            "positive_keywords": [x for x in (d.get("positive_kws") or "").split(",") if x],
            "negative_keywords": [x for x in (d.get("negative_kws") or "").split(",") if x],
            "clip_prompt_pos": [x for x in (d.get("clip_prompt_pos") or "").split(",") if x],
            "clip_prompt_neg": [x for x in (d.get("clip_prompt_neg") or "").split(",") if x],
            "is_meta": bool(d.get("is_meta", False)),
        }
    return result


# ──────────────────────────────────────────────
# representative_images
# ──────────────────────────────────────────────

def upsert_representative_images(conn, keyword: str, images: list[dict]):
    with conn.cursor() as cur:
        cur.execute("DELETE FROM representative_images WHERE keyword = %s", (keyword,))
        for rank, img in enumerate(images, 1):
            rest_id = None
            rest_name = img.get("restaurant", "")
            if rest_name:
                cur.execute("SELECT restaurant_id FROM restaurants WHERE name=%s", (rest_name,))
                row = cur.fetchone()
                if row:
                    rest_id = row["restaurant_id"]

            # 스키마 변경 없이 부가 점수는 clip_vector JSONB에 메타로 합쳐 저장
            clip_payload = dict(img.get("clip_vector", {}) or {})
            for meta_key in ("_axis_match_score", "_food_presence_score",
                             "_non_food_penalty", "_linked_text_score", "reason"):
                if meta_key in img and meta_key not in clip_payload:
                    clip_payload[meta_key] = img[meta_key]

            cur.execute("""
                INSERT INTO representative_images
                    (restaurant_id, keyword, axis, label, image_url,
                     clip_vector, review_snippet, score, rank)
                VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s)
            """, (
                rest_id,
                keyword,
                img.get("axis", ""),
                img.get("label", ""),
                img.get("image_src", ""),
                json.dumps(clip_payload, ensure_ascii=False),
                img.get("review_snippet", ""),
                float(img.get("score", 0)),
                rank,
            ))


def get_representative_images(conn, keyword: str) -> list[dict]:
    with conn.cursor() as cur:
        cur.execute("""
            SELECT ri.*, r.name AS restaurant_name
            FROM representative_images ri
            LEFT JOIN restaurants r ON r.restaurant_id = ri.restaurant_id
            WHERE ri.keyword = %s
            ORDER BY ri.rank
        """, (keyword,))
        rows = cur.fetchall()

    result = []
    for row in rows:
        d = dict(row)
        raw = d.pop("clip_vector", None)
        d["clip_vector"] = json.loads(raw) if isinstance(raw, str) else (raw or {})
        result.append(d)
    return result


def get_all_rep_keywords(conn) -> list[str]:
    with conn.cursor() as cur:
        cur.execute("SELECT DISTINCT keyword FROM representative_images ORDER BY keyword")
        return [r["keyword"] for r in cur.fetchall()]


# ──────────────────────────────────────────────
# 보조 헬퍼 (이미지 마이그레이션 / 대표이미지 갱신)
# ──────────────────────────────────────────────

def update_review_image_url(conn, image_id: int, new_url: str) -> int:
    with conn.cursor() as cur:
        cur.execute(
            "UPDATE review_images SET image_url = %s WHERE image_id = %s",
            (new_url, image_id),
        )
        return cur.rowcount


def delete_representative_images(conn, keyword: str) -> int:
    """특정 keyword의 대표 이미지를 모두 삭제 (재선정 직전에 사용)."""
    with conn.cursor() as cur:
        cur.execute("DELETE FROM representative_images WHERE keyword = %s", (keyword,))
        return cur.rowcount
