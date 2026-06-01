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

from db import get_conn, upsert_restaurant, get_all_keywords
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


def extract_semantic_evidence(
    review_emb: np.ndarray,        # (N, D) — L2 normalized
    sentences: list[str],          # 길이 N, review_emb와 1:1
    pos_emb: np.ndarray,           # (Mp, D) — positive prompt 임베딩
    neg_emb: np.ndarray,           # (Mn, D) — negative prompt 임베딩
    topk: int = 3,
    threshold: float = 0.12,
) -> list[dict]:
    """
    축 방향으로 의미가 분명한 리뷰 문장 top-k 추출.

    핵심: 점수 = max(pos cosine) - max(neg cosine)
      - prompt에 키워드('짬뽕')가 들어가 generic 문장이 모든 축에 0.8+ 받는
        문제를, pos/neg 둘 다에서 높게 나오면 상쇄시켜 제거.
      - "짬뽕 푸짐해요"는 온도-pos/neg 둘 다 높음 → diff≈0 → 탈락
      - "국물이 뜨끈해요"는 온도-pos만 높음 → diff 큼 → 채택
    threshold 미만(축 방향성 약함)은 버림.
    반환: [{"sentence": ..., "score": diff}, ...] (diff 내림차순)
    """
    if pos_emb.shape[0] == 0 or review_emb.shape[0] == 0:
        return []
    pos_max = (pos_emb @ review_emb.T).max(axis=0)          # (N,)
    if neg_emb.shape[0] > 0:
        neg_max = (neg_emb @ review_emb.T).max(axis=0)
    else:
        neg_max = np.zeros_like(pos_max)
    diff = pos_max - neg_max
    order = np.argsort(-diff)

    out: list[dict] = []
    seen: set[str] = set()
    for idx in order:
        score = float(diff[idx])
        if score < threshold:
            break                              # 정렬돼있으니 이하 전부 미달
        sent = sentences[idx].strip()
        key = sent[:40]
        if not sent or key in seen:
            continue
        seen.add(key)
        out.append({
            "sentence": sent[:200],
            "score": round(score, 4),
            "pos": round(float(pos_max[idx]), 4),
        })
        if len(out) >= topk:
            break
    return out


# ── DB 저장 ──────────────────────────────────────────────────

def save_semantic_vector(conn, restaurant_id: int, keyword: str,
                         semantic_vector: dict,
                         semantic_evidence: dict | None = None) -> bool:
    """semantic_vector (+ semantic_evidence) UPDATE. row 없으면 False."""
    payload = json.dumps(semantic_vector, ensure_ascii=False)
    ev_payload = json.dumps(semantic_evidence or {}, ensure_ascii=False)
    with conn.cursor() as cur:
        cur.execute("""
            UPDATE restaurant_vectors
               SET semantic_vector   = %s,
                   semantic_evidence = %s
             WHERE restaurant_id = %s AND keyword = %s
        """, (payload, ev_payload, restaurant_id, keyword))
        return cur.rowcount > 0


# ── 메인 ─────────────────────────────────────────────────────

def process_keyword(keyword: str, model, device: str, args) -> bool:
    """단일 keyword 처리. 모델은 호출부에서 1회 로드 후 재사용.
    리뷰 없음 등으로 건너뛰면 False, 정상 처리하면 True 반환."""
    print(f"\n{'━'*55}")
    print(f"  Semantic Text Profiler — '{keyword}'")
    print(f"{'━'*55}")

    # 2) 축
    taste_axes, meta_axes = fp.compose_axes(keyword)
    print(f"  📊 축: 맛 {len(taste_axes)} + 메타 {len(meta_axes)}")

    # 3) prompt 임베딩
    prompts = build_axis_prompts(taste_axes, meta_axes, keyword)
    axis_emb = {}
    for ax, pn in prompts.items():
        axis_emb[ax] = {
            "pos": embed_texts(model, pn["positive"], args.batch_size),
            "neg": embed_texts(model, pn["negative"], args.batch_size),
        }
    n_prompts = sum(e["pos"].shape[0] + e["neg"].shape[0] for e in axis_emb.values())
    print(f"  ✅ 축 prompt 임베딩: {n_prompts}개 ({len(axis_emb)} 축)")

    # 4) 리뷰 로드
    df = fp.load_from_db(keyword)
    if df.empty:
        print(f"  ⏭️  '{keyword}' 리뷰가 DB에 없음 → skip")
        return False
    rest_col = "Restaurant" if "Restaurant" in df.columns else "restaurant"
    rev_col = "Review" if "Review" in df.columns else "content"
    if args.restaurant:
        df = df[df[rest_col] == args.restaurant]
        if df.empty:
            print(f"  ⏭️  '{args.restaurant}' 리뷰 없음 → skip")
            return False

    grouped = df.groupby(rest_col)
    print(f"  📂 식당 {len(grouped)}개, 전체 리뷰 {len(df)}건")

    # 5) 식당별 임베딩 + 점수 + 근거
    semantic_results = {}
    evidence_results = {}     # {rest: {axis: [{sentence, score}, ...]}}
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

        # 각 축 점수 + 근거
        axis_scores = {}
        axis_evidence = {}
        debug_for_this = {}
        for ax_name, embs in axis_emb.items():
            pos_s, neg_s, score = compute_axis_score(
                rev_emb, embs["pos"], embs["neg"], topk=args.topk
            )
            axis_scores[ax_name] = score
            debug_for_this[ax_name] = (pos_s, neg_s, score)

            ev = extract_semantic_evidence(
                rev_emb, sentences, embs["pos"], embs["neg"],
                topk=args.evidence_topk, threshold=args.evidence_threshold
            )
            if ev:
                axis_evidence[ax_name] = ev

        semantic_results[rest_name] = axis_scores
        evidence_results[rest_name] = axis_evidence
        debug_preview.append((rest_name, debug_for_this, axis_evidence))

        # GPU 메모리 해제
        del rev_emb
        if device == "cuda":
            torch.cuda.empty_cache()

    # 6) 미리보기 (점수 + BERT 근거 문장)
    print(f"\n  ─ 결과 미리보기 (top 3 식당, threshold={args.evidence_threshold}) ─")
    for rest_name, dbg, axis_ev in debug_preview[:3]:
        print(f"\n  ▶ {rest_name}")
        sorted_axes = sorted(dbg.items(), key=lambda x: -abs(x[1][2]))[:6]
        for ax, (ps, ns, sc) in sorted_axes:
            bar = ("▲" if sc > 0 else "▼") * min(int(abs(sc) * 10), 12)
            print(f"      {ax:12s} pos={ps:+.3f} neg={ns:+.3f} score={sc:+.3f}  {bar}")
            for e in axis_ev.get(ax, [])[:2]:
                print(f"          ↳ [{e['score']:.2f}] \"{e['sentence'][:50]}\"")

    # 7) DB 저장
    if args.dry_run:
        print(f"\n  [DRY-RUN] DB 저장 생략 ({len(semantic_results)}개 식당 결과 계산됨)")
        return True

    with get_conn() as conn:
        saved = 0
        for rest_name, sv in semantic_results.items():
            rid = upsert_restaurant(conn, {"name": rest_name, "source": "semantic"})
            ok = save_semantic_vector(conn, rid, keyword, sv,
                                      semantic_evidence=evidence_results.get(rest_name, {}))
            if ok:
                saved += 1
        print(f"\n  ✅ DB 저장: {saved}/{len(semantic_results)}개 식당 (semantic_vector + evidence)")
        if saved < len(semantic_results):
            print(f"     ⚠️ {len(semantic_results)-saved}개는 restaurant_vectors row 없음")
            print(f"        → Food_profiler 먼저 돌려야 함")
    return True


def _resolve_keywords(raw_keywords: list[str], use_all: bool) -> list[str]:
    """--all 이면 DB의 모든 keyword, 아니면 인자 그대로 (중복 제거, 순서 유지)."""
    if use_all:
        with get_conn() as conn:
            kws = get_all_keywords(conn)
        if not kws:
            sys.exit("❌ DB에 keyword가 하나도 없습니다 (Food_profiler 먼저 돌리세요).")
        print(f"  🗂️  --all: DB keyword {len(kws)}개 → {', '.join(kws)}")
        return kws
    seen, out = set(), []
    for k in raw_keywords:
        k = k.strip()
        if k and k not in seen:
            seen.add(k)
            out.append(k)
    return out


def main():
    parser = argparse.ArgumentParser(description="KoSBERT 기반 의미 임베딩 매칭")
    parser.add_argument("keyword", nargs="*",
                        help="대상 keyword (여러 개 가능: 짬뽕 김밥 치킨). --all 쓰면 생략 가능")
    parser.add_argument("--all", action="store_true",
                        help="DB에 있는 모든 keyword를 한 번에 처리 (모델 1회 로드)")
    parser.add_argument("--topk", type=int, default=5,
                        help="prompt당 top-k 리뷰 평균")
    parser.add_argument("--batch-size", type=int, default=32,
                        help="임베딩 배치 크기 (4GB GPU=32 권장)")
    parser.add_argument("--max-sentences", type=int, default=10000,
                        help="식당당 임베딩할 최대 문장 (메모리/시간 절약)")
    parser.add_argument("--evidence-topk", type=int, default=3,
                        help="축별 BERT 근거 문장 최대 개수")
    parser.add_argument("--evidence-threshold", type=float, default=0.30,
                        help="pos-neg diff 이 값 미만 문장은 근거 제외 (노이즈 차단). 높일수록 엄격")
    parser.add_argument("--restaurant", default=None,
                        help="특정 식당만 처리 (디버깅용)")
    parser.add_argument("--device", default="auto",
                        choices=["auto", "cuda", "cpu"])
    parser.add_argument("--dry-run", action="store_true",
                        help="DB 저장 없이 결과 미리보기만")
    args = parser.parse_args()

    keywords = _resolve_keywords(args.keyword, args.all)
    if not keywords:
        parser.error("keyword를 1개 이상 주거나 --all 을 쓰세요.")

    # 모델은 1회만 로드 후 모든 keyword에 재사용 (멀티 keyword의 핵심 이득)
    device = get_device(args.device)
    model = load_model(device)

    done, skipped = [], []
    for i, kw in enumerate(keywords, 1):
        print(f"\n\n########## [{i}/{len(keywords)}] keyword='{kw}' ##########")
        try:
            ok = process_keyword(kw, model, device, args)
            (done if ok else skipped).append(kw)
        except Exception as e:
            print(f"  ❌ '{kw}' 처리 중 오류: {e} → 다음 keyword 계속")
            skipped.append(kw)

    print(f"\n{'━'*55}")
    print(f"  완료 — 처리 {len(done)}개 / 건너뜀 {len(skipped)}개")
    if done:
        print(f"    ✅ {', '.join(done)}")
    if skipped:
        print(f"    ⏭️  {', '.join(skipped)}")
    print(f"{'━'*55}\n")


if __name__ == "__main__":
    main()
