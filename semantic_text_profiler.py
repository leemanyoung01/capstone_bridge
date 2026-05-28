"""
semantic_text_profiler.py — Sentence Embedding 기반 의미 매칭
============================================================

Food_profiler의 lex substring 매칭이 못 잡는 표현을
KoSBERT semantic 임베딩으로 보강한다.

예시:
  lex에 '느글' 키워드가 없어도 "느글거리네요" → '느끼함' 축에 매칭.

설계:
  1. compose_axes()로 키워드의 축 정의 (Food_profiler 재사용)
  2. 각 축마다 positive/negative prompt 자동 생성 (label + lex + keyword)
  3. KoSBERT로 prompt와 리뷰 문장 모두 임베딩
  4. axis_score = mean(top-k pos cosine) - mean(top-k neg cosine) ∈ [-1, 1]
  5. restaurant_vectors.semantic_vector (JSONB) 에 저장

추가만 함:
  - 기존 text_vector / image_vector / fused_vector 미터치
  - representative_images 미터치
  - 새 컬럼 semantic_vector만 채움. app.py가 옵트인으로 fusion에 반영.

사용:
  python semantic_text_profiler.py 짬뽕
  python semantic_text_profiler.py 짬뽕 --topk 5 --batch-size 32
  python semantic_text_profiler.py 짬뽕 --restaurant "공푸 성신여대본점"  # 단일 식당
  python semantic_text_profiler.py 짬뽕 --dry-run  # DB 저장 생략, 결과만 미리보기
"""
from __future__ import annotations

import argparse
import json
import os
import random
import re
import sys
from pathlib import Path
from typing import Optional

import numpy as np
import pandas as pd

try:
    import torch
    from sentence_transformers import SentenceTransformer
except ImportError as e:
    print(f"❌ sentence-transformers / torch 미설치: {e}")
    print("   → pip install sentence-transformers")
    sys.exit(1)

from db import get_conn, upsert_restaurant
import Food_profiler as fp


# ── 설정 ──────────────────────────────────────────────────────
MODEL_NAME = "jhgan/ko-sroberta-multitask"   # 한국어 sentence transformer
EMBED_DIM = 768
SENT_SPLIT_RE = re.compile(r'[.!?。…\n]+')
MIN_SENT_LEN = 3            # 너무 짧은 문장 ("굿") 제외
MAX_SENT_LEN = 200          # 너무 긴 문장 자르기

CACHE_DIR = Path(".cache_semantic")
CACHE_DIR.mkdir(exist_ok=True)


# ── 유틸 ──────────────────────────────────────────────────────

def get_device(prefer: str = "auto") -> str:
    if prefer == "cpu":
        return "cpu"
    if torch.cuda.is_available():
        return "cuda"
    return "cpu"


def load_model(device: str) -> SentenceTransformer:
    print(f"  📦 모델 로드: {MODEL_NAME}  device={device}")
    return SentenceTransformer(MODEL_NAME, device=device)


def split_into_sentences(text: str) -> list[str]:
    """문장 분리 + 길이 필터."""
    if not isinstance(text, str) or not text.strip():
        return []
    raw = SENT_SPLIT_RE.split(text.strip())
    return [s.strip()[:MAX_SENT_LEN] for s in raw
            if s and len(s.strip()) >= MIN_SENT_LEN]


def embed_texts(model, texts: list[str], batch_size: int = 32,
                show_progress: bool = False) -> np.ndarray:
    """배치 임베딩. L2 normalize → dot product = cosine."""
    if not texts:
        return np.zeros((0, EMBED_DIM), dtype=np.float32)
    emb = model.encode(
        texts,
        batch_size=batch_size,
        show_progress_bar=show_progress,
        convert_to_numpy=True,
        normalize_embeddings=True,
    )
    return emb.astype(np.float32)


# ── 축별 prompt 생성 ──────────────────────────────────────────

def _join_kws(kws: list, n: int = 5) -> str:
    """lex 키워드 합치기 (자연어 prompt용)."""
    pruned = [k for k in (kws or []) if k][:n]
    return ", ".join(pruned) if pruned else ""


def build_axis_prompts(taste_axes: dict, meta_axes: dict, keyword: str) -> dict:
    """
    각 축마다 positive/negative prompt 리스트 생성.
    label + 키워드 + lex 키워드를 자연어 형태로 엮어 다양성 확보.
    """
    all_axes = {**taste_axes, **meta_axes}
    prompts = {}
    for ax_name, info in all_axes.items():
        label = info.get("label") or ax_name
        positives = info.get("positive", []) or []
        negatives = info.get("negative", []) or []

        pos_prompts = [
            f"{label}이 좋은 {keyword}",
            f"{label}이 풍부한 {keyword}",
        ]
        pos_kw = _join_kws(positives, 5)
        if pos_kw:
            pos_prompts.append(f"{pos_kw}한 {keyword}")
            pos_prompts.append(f"{keyword}가 {pos_kw}")

        neg_prompts = [
            f"{label}이 부족한 {keyword}",
            f"{label}이 없는 {keyword}",
        ]
        neg_kw = _join_kws(negatives, 3)
        if neg_kw:
            neg_prompts.append(f"{neg_kw}한 {keyword}")

        prompts[ax_name] = {"positive": pos_prompts, "negative": neg_prompts}
    return prompts


# ── 점수 계산 ─────────────────────────────────────────────────

def compute_axis_score(
    review_emb: np.ndarray,                  # (N, D) — L2 normalized
    pos_emb: np.ndarray,                     # (Mp, D)
    neg_emb: np.ndarray,                     # (Mn, D)
    topk: int = 5,
) -> tuple[float, float, float]:
    """
    pos/neg prompt와 리뷰 임베딩의 코사인 → 각각 top-k 평균.
    axis_score = pos - neg ∈ [-1, 1] 근사.
    """
    if review_emb.shape[0] == 0:
        return 0.0, 0.0, 0.0

    def topk_mean(prompt_emb):
        if prompt_emb.shape[0] == 0:
            return 0.0
        sim = prompt_emb @ review_emb.T          # (M, N)
        k = min(topk, sim.shape[1])
        # 각 prompt마다 top-k → 평균 → 모든 prompt 평균
        topk_vals = np.partition(sim, -k, axis=1)[:, -k:]
        return float(topk_vals.mean())

    pos_score = topk_mean(pos_emb)
    neg_score = topk_mean(neg_emb)
    return pos_score, neg_score, round(pos_score - neg_score, 4)


# ── DB 저장 ──────────────────────────────────────────────────

def save_semantic_vector(conn, restaurant_id: int, keyword: str,
                         semantic_vector: dict) -> bool:
    """semantic_vector 컬럼만 UPDATE. row 없으면 False."""
    payload = json.dumps(semantic_vector, ensure_ascii=False)
    with conn.cursor() as cur:
        cur.execute("""
            UPDATE restaurant_vectors
               SET semantic_vector = %s
             WHERE restaurant_id = %s AND keyword = %s
        """, (payload, restaurant_id, keyword))
        return cur.rowcount > 0


# ── 메인 ─────────────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser(description="KoSBERT 기반 의미 임베딩 매칭")
    parser.add_argument("keyword", help="대상 keyword (예: 짬뽕)")
    parser.add_argument("--topk", type=int, default=5,
                        help="prompt당 top-k 리뷰 평균")
    parser.add_argument("--batch-size", type=int, default=32,
                        help="임베딩 배치 크기 (4GB GPU=32 권장)")
    parser.add_argument("--max-sentences", type=int, default=2000,
                        help="식당당 임베딩할 최대 문장 (메모리/시간 절약)")
    parser.add_argument("--restaurant", default=None,
                        help="특정 식당만 처리 (디버깅용)")
    parser.add_argument("--device", default="auto",
                        choices=["auto", "cuda", "cpu"])
    parser.add_argument("--dry-run", action="store_true",
                        help="DB 저장 없이 결과 미리보기만")
    args = parser.parse_args()

    print(f"\n{'━'*55}")
    print(f"  Semantic Text Profiler — '{args.keyword}'")
    print(f"{'━'*55}")

    # 1) 모델
    device = get_device(args.device)
    model = load_model(device)

    # 2) 축
    taste_axes, meta_axes = fp.compose_axes(args.keyword)
    print(f"  📊 축: 맛 {len(taste_axes)} + 메타 {len(meta_axes)}")

    # 3) prompt 임베딩
    prompts = build_axis_prompts(taste_axes, meta_axes, args.keyword)
    axis_emb = {}
    for ax, pn in prompts.items():
        axis_emb[ax] = {
            "pos": embed_texts(model, pn["positive"], args.batch_size),
            "neg": embed_texts(model, pn["negative"], args.batch_size),
        }
    n_prompts = sum(e["pos"].shape[0] + e["neg"].shape[0] for e in axis_emb.values())
    print(f"  ✅ 축 prompt 임베딩: {n_prompts}개 ({len(axis_emb)} 축)")

    # 4) 리뷰 로드
    df = fp.load_from_db(args.keyword)
    if df.empty:
        sys.exit(f"❌ '{args.keyword}' 리뷰가 DB에 없습니다.")
    rest_col = "Restaurant" if "Restaurant" in df.columns else "restaurant"
    rev_col = "Review" if "Review" in df.columns else "content"
    if args.restaurant:
        df = df[df[rest_col] == args.restaurant]
        if df.empty:
            sys.exit(f"❌ '{args.restaurant}' 리뷰 없음")

    grouped = df.groupby(rest_col)
    print(f"  📂 식당 {len(grouped)}개, 전체 리뷰 {len(df)}건")

    # 5) 식당별 임베딩 + 점수
    semantic_results = {}
    debug_preview = []
    for ri, (rest_name, sub) in enumerate(grouped, 1):
        sentences = []
        for txt in sub[rev_col].dropna().tolist():
            sentences.extend(split_into_sentences(str(txt)))

        if len(sentences) > args.max_sentences:
            random.seed(42)
            sentences = random.sample(sentences, args.max_sentences)
        if not sentences:
            print(f"  [{ri}/{len(grouped)}] {rest_name}: 문장 0개, skip")
            continue

        print(f"  [{ri}/{len(grouped)}] {rest_name}: {len(sentences)}문장 → 임베딩 중...", flush=True)
        rev_emb = embed_texts(model, sentences, args.batch_size)

        # 각 축 점수
        axis_scores = {}
        debug_for_this = {}
        for ax_name, embs in axis_emb.items():
            pos_s, neg_s, score = compute_axis_score(
                rev_emb, embs["pos"], embs["neg"], topk=args.topk
            )
            axis_scores[ax_name] = score
            debug_for_this[ax_name] = (pos_s, neg_s, score)

        semantic_results[rest_name] = axis_scores
        debug_preview.append((rest_name, debug_for_this))

        # GPU 메모리 해제
        del rev_emb
        if device == "cuda":
            torch.cuda.empty_cache()

    # 6) 미리보기
    print(f"\n  ─ 결과 미리보기 (top 3 식당) ─")
    for rest_name, dbg in debug_preview[:3]:
        print(f"\n  ▶ {rest_name}")
        sorted_axes = sorted(dbg.items(), key=lambda x: -abs(x[1][2]))[:6]
        for ax, (ps, ns, sc) in sorted_axes:
            bar = ("▲" if sc > 0 else "▼") * min(int(abs(sc) * 10), 12)
            print(f"      {ax:12s} pos={ps:+.3f} neg={ns:+.3f} score={sc:+.3f}  {bar}")

    # 7) DB 저장
    if args.dry_run:
        print(f"\n  [DRY-RUN] DB 저장 생략 ({len(semantic_results)}개 식당 결과 계산됨)")
        return

    with get_conn() as conn:
        saved = 0
        for rest_name, sv in semantic_results.items():
            rid = upsert_restaurant(conn, {"name": rest_name, "source": "semantic"})
            ok = save_semantic_vector(conn, rid, args.keyword, sv)
            if ok:
                saved += 1
        print(f"\n  ✅ DB 저장: {saved}/{len(semantic_results)}개 식당")
        if saved < len(semantic_results):
            print(f"     ⚠️ {len(semantic_results)-saved}개는 restaurant_vectors row 없음")
            print(f"        → Food_profiler 먼저 돌려야 함")

    print(f"\n{'━'*55}")
    print(f"  완료")
    print(f"{'━'*55}\n")


if __name__ == "__main__":
    main()
