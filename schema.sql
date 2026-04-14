-- TasteBridge PostgreSQL 스키마
-- 실행: psql -U postgres -d tastebridge -f schema.sql

CREATE EXTENSION IF NOT EXISTS "pg_trgm";   -- 한글 LIKE 인덱스 최적화

-- ── 식당 기본 정보 ──────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS restaurants (
    restaurant_id   SERIAL PRIMARY KEY,
    name            VARCHAR(255) UNIQUE NOT NULL,
    place_id        VARCHAR(50),
    naver_url       TEXT,
    category        VARCHAR(100),
    address         TEXT,
    road_address    TEXT,
    phone           VARCHAR(30),
    lat             DOUBLE PRECISION DEFAULT 0,
    lng             DOUBLE PRECISION DEFAULT 0,
    source          VARCHAR(20)  DEFAULT 'auto',
    created_at      TIMESTAMP    DEFAULT CURRENT_TIMESTAMP,
    updated_at      TIMESTAMP    DEFAULT CURRENT_TIMESTAMP
);

-- ── 리뷰 원문 ──────────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS reviews (
    review_id           SERIAL PRIMARY KEY,
    restaurant_id       INT REFERENCES restaurants(restaurant_id) ON DELETE CASCADE,
    naver_review_id     VARCHAR(100) UNIQUE,
    author              VARCHAR(100),
    content             TEXT,
    rating              FLOAT,
    menu                VARCHAR(255),
    visit_count         INT          DEFAULT 0,
    has_image           BOOLEAN      DEFAULT FALSE,
    voted_keywords      TEXT,
    owner_reply         TEXT,
    reviewed_at         TIMESTAMP,
    crawled_at          TIMESTAMP    DEFAULT CURRENT_TIMESTAMP
);

-- ── 리뷰 이미지 ────────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS review_images (
    image_id    SERIAL PRIMARY KEY,
    review_id   INT REFERENCES reviews(review_id) ON DELETE CASCADE,
    image_url   TEXT,
    image_path  TEXT
);

-- ── 맛 벡터 (키워드별) ─────────────────────────────────────────
CREATE TABLE IF NOT EXISTS restaurant_vectors (
    id              SERIAL PRIMARY KEY,
    restaurant_id   INT  REFERENCES restaurants(restaurant_id) ON DELETE CASCADE,
    keyword         VARCHAR(100),
    text_vector     JSONB,           -- Food_profiler normalized {axis: score}
    image_vector    JSONB,           -- Multimodal CLIP 결과
    fused_vector    JSONB,           -- Late fusion 최종
    evidence_json   JSONB,           -- {axis: ["리뷰 문장", ...]}
    avg_rating      FLOAT  DEFAULT 0,
    avg_taste       FLOAT  DEFAULT 0,
    review_count    INT    DEFAULT 0,
    keyword_reviews INT    DEFAULT 0,
    scored_reviews  INT    DEFAULT 0,
    image_coverage  FLOAT  DEFAULT 0,
    has_image_data  BOOLEAN DEFAULT FALSE,
    created_at      TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    UNIQUE (restaurant_id, keyword)
);

-- ── 맛 축 정의 ─────────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS axes_config (
    id              SERIAL PRIMARY KEY,
    keyword         VARCHAR(100) NOT NULL,
    axis_name       VARCHAR(100) NOT NULL,
    group_name      VARCHAR(50),
    positive_kws    TEXT,
    negative_kws    TEXT,
    clip_prompt_pos TEXT,
    clip_prompt_neg TEXT,
    is_meta         BOOLEAN DEFAULT FALSE,
    UNIQUE (keyword, axis_name)
);

-- ── 대표 이미지 ────────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS representative_images (
    id              SERIAL PRIMARY KEY,
    restaurant_id   INT REFERENCES restaurants(restaurant_id) ON DELETE CASCADE,
    keyword         VARCHAR(100),
    axis            VARCHAR(100),
    label           VARCHAR(100),
    image_url       TEXT,
    image_path      TEXT,
    clip_vector     JSONB,
    review_snippet  TEXT,
    score           FLOAT   DEFAULT 0,
    rank            INT     DEFAULT 0,
    created_at      TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

-- ── 인덱스 ────────────────────────────────────────────────────
CREATE INDEX IF NOT EXISTS idx_restaurant_name    ON restaurants(name);
CREATE INDEX IF NOT EXISTS idx_restaurant_place   ON restaurants(place_id);
CREATE INDEX IF NOT EXISTS idx_reviews_restaurant ON reviews(restaurant_id);
CREATE INDEX IF NOT EXISTS idx_reviews_naver_id   ON reviews(naver_review_id);
CREATE INDEX IF NOT EXISTS idx_reviews_menu       ON reviews(menu);
CREATE INDEX IF NOT EXISTS idx_vectors_keyword    ON restaurant_vectors(keyword);
CREATE INDEX IF NOT EXISTS idx_vectors_fused      ON restaurant_vectors USING GIN (fused_vector);
CREATE INDEX IF NOT EXISTS idx_vectors_text       ON restaurant_vectors USING GIN (text_vector);
CREATE INDEX IF NOT EXISTS idx_rep_keyword        ON representative_images(keyword);
CREATE INDEX IF NOT EXISTS idx_axes_keyword       ON axes_config(keyword);

-- ── updated_at 자동 갱신 ──────────────────────────────────────
CREATE OR REPLACE FUNCTION _set_updated_at()
RETURNS TRIGGER LANGUAGE plpgsql AS $$
BEGIN NEW.updated_at = CURRENT_TIMESTAMP; RETURN NEW; END;
$$;

DROP TRIGGER IF EXISTS trg_restaurants_updated ON restaurants;
CREATE TRIGGER trg_restaurants_updated
    BEFORE UPDATE ON restaurants
    FOR EACH ROW EXECUTE FUNCTION _set_updated_at();
