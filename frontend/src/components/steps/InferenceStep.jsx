import React, { useState } from 'react';
import { motion } from 'framer-motion';
import Button from '../ui/Button';
import AxisMatchBar from '../ui/AxisMatchBar';
import EvaluationCard from '../ui/EvaluationCard';
import { Star, ExternalLink, ChevronDown, ChevronUp } from 'lucide-react';

const InferenceStep = ({ results, error, onRestart }) => {
  const [expanded, setExpanded] = useState({});

  const toggle = (idx) => setExpanded((p) => ({ ...p, [idx]: !p[idx] }));

  if (error) {
    return (
      <div style={{ textAlign: 'center', padding: '60px 20px' }}>
        <div style={{ fontSize: '40px', marginBottom: '16px' }}>⚠️</div>
        <h2 style={{ fontSize: '20px', fontWeight: '700', color: '#D94452' }}>추천을 불러오지 못했습니다</h2>
        <p style={{ color: 'var(--text-gray)', marginTop: '8px', marginBottom: '24px' }}>{error}</p>
        <Button variant="primary" onClick={onRestart}>처음으로 돌아가기</Button>
      </div>
    );
  }

  if (!results || results.length === 0) {
    return <div style={{ textAlign: 'center', padding: '60px 20px' }}>추천 결과가 없습니다.</div>;
  }

  return (
    <motion.div initial={{ opacity: 0, y: 20 }} animate={{ opacity: 1, y: 0 }} style={{ padding: '20px', maxWidth: '640px', margin: '0 auto' }}>
      <div style={{ textAlign: 'center', marginBottom: '24px' }}>
        <h2 style={{ fontSize: '24px', fontWeight: '800' }}>나만의 맛 스니펫 완성! 🍕</h2>
        <p style={{ color: 'var(--text-gray)', fontSize: '14px', marginTop: '4px' }}>
          취향 벡터와 가장 가까운 식당을 찾았습니다.
        </p>
      </div>

      <EvaluationCard />

      <div style={{ display: 'flex', flexDirection: 'column', gap: '16px', marginBottom: '32px' }}>
        {results.map((item, idx) => {
          const matchPct = item.match_percent != null
            ? item.match_percent
            : Math.round((item.similarity || 0) * 100);
          const isExpanded = !!expanded[idx];
          const placeUrl = item.place_url || item.naver_url || item.fallback_search_url;

          // 축별 매칭 데이터 (top_axes + axis_scores)
          const topAxes = (item.top_axes || item.reasons || []).slice(0, 5);
          const axisScores = item.axis_scores || {};
          const axisContribs = item.axis_contributions || {};
          const matchData = topAxes.map((name) => ({
            name,
            value: axisScores[name] != null ? axisScores[name] : 0,
            contribution: axisContribs[name],
          }));

          // text/image 신뢰도
          const fw = item.fusion_weights || { text: 1, image: 0 };
          const textPct = Math.round((fw.text || 1) * 100);
          const imgPct = Math.round((fw.image || 0) * 100);
          const hasImageBasis = imgPct > 0 && (item.image_confidence || 0) > 0.05;

          // 대표 이미지
          const repImg = item.representative_image && item.representative_image.image_src
            ? item.representative_image
            : null;

          // evidence (객체 또는 배열 모두 호환)
          const evSentences = item.evidence_sentences && typeof item.evidence_sentences === 'object'
            ? item.evidence_sentences
            : null;

          return (
            <motion.div
              key={idx}
              initial={{ opacity: 0, x: -20 }}
              animate={{ opacity: 1, x: 0 }}
              transition={{ delay: idx * 0.08 }}
              style={{
                backgroundColor: 'var(--white)', borderRadius: 'var(--radius-lg)', padding: '18px',
                boxShadow: 'var(--shadow-md)',
                border: matchPct >= 80 ? '2px solid var(--primary)' : '1px solid var(--border-color)'
              }}
            >
              {/* ── 헤더 ─────────────────────────────── */}
              <div style={{ display: 'flex', gap: '12px', alignItems: 'flex-start' }}>
                {repImg && (
                  <img
                    src={repImg.image_src}
                    alt={repImg.label || item.name}
                    style={{
                      width: '72px', height: '72px', borderRadius: 'var(--radius-md)',
                      objectFit: 'cover', flexShrink: 0
                    }}
                    onError={(e) => { e.target.style.display = 'none'; }}
                  />
                )}
                <div style={{ flex: 1, minWidth: 0 }}>
                  <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'flex-start', gap: '8px' }}>
                    <h3 style={{ fontSize: '17px', fontWeight: '800', display: 'flex', alignItems: 'center', gap: '6px', flexWrap: 'wrap' }}>
                      {idx + 1}. {item.name}
                      {matchPct >= 80 && <span style={{ fontSize: '11px', background: 'var(--primary-light)', color: 'var(--primary)', padding: '2px 8px', borderRadius: '10px' }}>강력 추천</span>}
                    </h3>
                    <div style={{ display: 'flex', alignItems: 'center', gap: '4px', backgroundColor: '#FFF5E5', padding: '5px 10px', borderRadius: '999px', flexShrink: 0 }}>
                      <Star size={13} color="#F59E0B" fill="#F59E0B" />
                      <span style={{ fontSize: '13px', fontWeight: '800', color: '#B45309' }}>{matchPct}%</span>
                    </div>
                  </div>
                  {item.address && (
                    <p style={{ fontSize: '12px', color: 'var(--text-gray)', marginTop: '4px' }}>{item.address}</p>
                  )}
                </div>
              </div>

              {/* ── 추천 근거 한 줄 ────────────────── */}
              {item.debug_reason && (
                <div style={{ marginTop: '10px', fontSize: '12px', color: 'var(--text-gray)' }}>
                  💡 {item.debug_reason}
                </div>
              )}

              {/* ── 태그 (top_axes) ───────────────── */}
              {topAxes.length > 0 && (
                <div style={{ display: 'flex', flexWrap: 'wrap', gap: '6px', marginTop: '10px' }}>
                  {topAxes.slice(0, 3).map((r) => (
                    <span key={r} style={{ fontSize: '12px', backgroundColor: 'var(--bg-color)', color: 'var(--text-dark)', padding: '4px 10px', borderRadius: '999px', fontWeight: '600' }}>
                      #{r}
                    </span>
                  ))}
                </div>
              )}

              {/* ── 근거 비율 배지 ────────────────── */}
              <div style={{ marginTop: '10px', display: 'flex', gap: '8px', flexWrap: 'wrap', fontSize: '11px' }}>
                <span style={{ background: '#EEF2FF', color: '#4338CA', padding: '3px 8px', borderRadius: '6px', fontWeight: '600' }}>
                  텍스트 근거 {textPct}%
                </span>
                {hasImageBasis ? (
                  <span style={{ background: '#FEF3C7', color: '#92400E', padding: '3px 8px', borderRadius: '6px', fontWeight: '600' }}>
                    이미지 근거 {imgPct}%
                  </span>
                ) : (
                  <span style={{ background: '#F3F4F6', color: '#6B7280', padding: '3px 8px', borderRadius: '6px', fontWeight: '600' }}>
                    이미지 근거 부족 — 텍스트 중심
                  </span>
                )}
              </div>

              {/* ── 첫 evidence ──────────────────── */}
              {item.evidence && item.evidence.length > 0 && (
                <div style={{ backgroundColor: 'var(--bg-color)', padding: '10px 12px', borderRadius: 'var(--radius-md)', fontSize: '13px', color: 'var(--text-gray)', fontStyle: 'italic', marginTop: '10px' }}>
                  "{item.evidence[0]}"
                </div>
              )}

              {/* ── 자세히 보기 토글 ──────────────── */}
              <button
                onClick={() => toggle(idx)}
                style={{
                  marginTop: '10px', background: 'transparent', border: 'none',
                  color: 'var(--primary)', fontSize: '13px', fontWeight: 600,
                  display: 'flex', alignItems: 'center', gap: '4px', cursor: 'pointer', padding: 0
                }}
              >
                자세히 보기 {isExpanded ? <ChevronUp size={14} /> : <ChevronDown size={14} />}
              </button>

              {isExpanded && (
                <div style={{ marginTop: '12px', borderTop: '1px solid var(--border-color)', paddingTop: '12px', display: 'flex', flexDirection: 'column', gap: '14px' }}>
                  {/* 축별 매칭 그래프 */}
                  {matchData.length > 0 && (
                    <div>
                      <div style={{ fontSize: '12px', fontWeight: 700, marginBottom: '6px', color: 'var(--text-dark)' }}>
                        축별 식당 점수
                      </div>
                      <AxisMatchBar axes={matchData} max={5} />
                    </div>
                  )}
                  {/* 추천 기여도 */}
                  {Object.keys(axisContribs).length > 0 && (
                    <div>
                      <div style={{ fontSize: '12px', fontWeight: 700, marginBottom: '6px', color: 'var(--text-dark)' }}>
                        축별 추천 기여도
                      </div>
                      <AxisMatchBar
                        axes={Object.entries(axisContribs)
                          .sort((a, b) => b[1] - a[1])
                          .slice(0, 5)
                          .map(([n, c]) => ({ name: n, value: c, contribution: c }))}
                        max={5}
                        showContribution
                      />
                    </div>
                  )}
                  {/* 축별 evidence */}
                  {evSentences && Object.keys(evSentences).length > 0 && (
                    <div>
                      <div style={{ fontSize: '12px', fontWeight: 700, marginBottom: '6px', color: 'var(--text-dark)' }}>
                        축별 추천 근거 문장
                      </div>
                      <div style={{ display: 'flex', flexDirection: 'column', gap: '6px' }}>
                        {Object.entries(evSentences).slice(0, 4).map(([ax, sents]) => (
                          <div key={ax} style={{ fontSize: '12px' }}>
                            <span style={{ fontWeight: 700, color: 'var(--primary)' }}>#{ax}</span>{' '}
                            <span style={{ color: 'var(--text-gray)', fontStyle: 'italic' }}>"{(sents || [])[0]}"</span>
                          </div>
                        ))}
                      </div>
                    </div>
                  )}
                </div>
              )}

              {/* ── 외부 링크 버튼 ────────────────── */}
              {placeUrl && (
                <div style={{ marginTop: '12px', textAlign: 'right' }}>
                  <a
                    href={placeUrl}
                    target="_blank"
                    rel="noopener noreferrer"
                    style={{
                      display: 'inline-flex', alignItems: 'center', gap: '6px',
                      fontSize: '13px', fontWeight: 600, color: 'var(--primary)',
                      textDecoration: 'none', padding: '6px 12px',
                      border: '1px solid var(--primary)', borderRadius: 'var(--radius-md)'
                    }}
                  >
                    {item.naver_url ? '네이버 플레이스 보기' : '네이버에서 검색'} <ExternalLink size={13} />
                  </a>
                </div>
              )}
            </motion.div>
          );
        })}
      </div>

      <div style={{ textAlign: 'center' }}>
        <Button variant="primary" onClick={onRestart}>다시 테스트하기</Button>
      </div>
    </motion.div>
  );
};

export default InferenceStep;
