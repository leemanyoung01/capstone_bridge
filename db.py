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

def insert_review(conn, restaurant_id: int, review: dict) -> Optional[int]:
    """
    리뷰 INSERT. naver_review_id 중복이면 무시하고 None 반환.
    날짜는 _parse_date()로 안전하게 변환 후 삽입.
    크롤러(Date), csv_to_json(reviewed_at) 양쪽 키 모두 처리.
    """
    # "Date"(크롤러), "reviewed_at"(csv_to_json 변환 후), "date" 모두 시도
    raw_date = (
        review.get("Date")
        or review.get("reviewed_at")
        or review.get("date")
    )
    parsed_date = _parse_date(raw_date)

    with conn.cursor() as cur:
        cur.execute("""
            INSERT INTO reviews
                (restaurant_id, naver_review_id, author, content, rating,
                 menu, visit_count, has_image, voted_keywords, owner_reply, reviewed_at)
            VALUES
                (%(rid)s, %(nrid)s, %(author)s, %(content)s, %(rating)s,
                 %(menu)s, %(vc)s, %(hi)s, %(vk)s, %(reply)s, %(date)s)
            ON CONFLICT (naver_review_id) DO NOTHING
            RETURNING review_id
        """, {
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
        })
        row = cur.fetchone()
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


def get_reviews_for_profiler(conn, keyword: str, aliases: Optional[list[str]] = None) -> list[dict]:
    """
    Food_profiler용 리뷰 조회.
    menu 또는 content에 keyword / aliases 포함 리뷰 반환.
    컬럼명은 원본 CSV 컬럼명과 동일하게 맞춤 (Restaurant, Review, Total, Menu…).
    """
    terms = list(dict.fromkeys([keyword] + (aliases or [])))
    cond = " OR ".join(["(r.content ILIKE %s OR r.menu ILIKE %s)"] * len(terms))
    params: list = []
    for t in terms:
        params += [f"%{t}%", f"%{t}%"]

    with conn.cursor() as cur:
        cur.execute(f"""
            SELECT
                r.review_id,
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
            WHERE ({cond})
            GROUP BY r.review_id, rst.name
            ORDER BY rst.name, r.reviewed_at DESC
        """, params)
        return [dict(row) for row in cur.fetchall()]


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
        for field in ("text_vector", "image_vector", "fused_vector", "evidence_json"):
            raw = d.pop(field, None)
            out_key = {
                "text_vector": "text_vector",
                "image_vector": "image_vector",
                "fused_vector": "fused_vector",
                "evidence_json": "evidence",
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
                json.dumps(img.get("clip_vector", {}), ensure_ascii=False),
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