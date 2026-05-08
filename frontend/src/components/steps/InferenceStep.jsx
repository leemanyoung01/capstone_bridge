import React, { useState } from 'react';
import { motion, AnimatePresence } from 'framer-motion';
import AxisMatchBar from '../ui/AxisMatchBar';
import RadarChartViz from '../ui/RadarChartViz';
import EvaluationCard from '../ui/EvaluationCard';
import { ExternalLink, ChevronDown, ChevronUp, HelpCircle, RotateCcw } from 'lucide-react';

// Pastel accent pool for rank cards
const CARD_ACCENTS = [
  { bg: '#FFF3C4', border: '#F5D45E', rank: '#B87A00' },
  { bg: '#E8DCFF', border: '#C4A8F5', rank: '#6B3FA0' },
  { bg: '#D4EDD4', border: '#9DD09D', rank: '#2E7D32' },
  { bg: '#DAEAFF', border: '#90BFFF', rank: '#1A56B0' },
  { bg: '#FFE4D6', border: '#F5A07A', rank: '#B84E00' },
];

// SVG star for the match badge
const StarSVG = ({ size = 14, fill = '#F59E0B' }) => (
  <svg width={size} height={size} viewBox="0 0 20 20" fill={fill}>
    <path d="M10 1 L12 7.5 L19 7.5 L13.5 11.5 L15.5 18 L10 14 L4.5 18 L6.5 11.5 L1 7.5 L8 7.5 Z" />
  </svg>
);

const ordinal = (n) => `${n}위`;

const InferenceStep = ({ results, error, onRestart }) => {
  const [expanded, setExpanded] = useState({});
  const toggle = (idx) => setExpanded(p => ({ ...p, [idx]: !p[idx] }));

  if (error) {
    return (
      <div style={{ textAlign: 'center', padding: '60px 20px' }}>
        <div style={{ fontSize: '44px', marginBottom: '16px' }}>⚠️</div>
        <h2 style={{ fontSize: '20px', fontWeight: 900, color: '#D94452' }}>추천을 불러오지 못했습니다</h2>
        <p style={{ color: 'var(--text-gray)', marginTop: '8px', marginBottom: '24px' }}>{error}</p>
        <motion.button
          onClick={onRestart}
          whileHover={{ scale: 1.04 }} whileTap={{ scale: 0.96 }}
          style={{
            background: 'var(--btn-dark)', color: 'white', border: 'none',
            borderRadius: '999px', padding: '14px 32px', fontSize: '15px',
            fontWeight: 700, cursor: 'pointer', fontFamily: 'inherit',
          }}
        >
          처음으로 돌아가기
        </motion.button>
      </div>
    );
  }

  if (!results) {
    return (
      <div style={{ textAlign: 'center', padding: '80px 20px', color: 'var(--text-gray)' }}>
        <motion.div
          animate={{ rotate: 360 }}
          transition={{ repeat: Infinity, duration: 1.2, ease: 'linear' }}
          style={{ fontSize: '32px', display: 'inline-block', marginBottom: '16px' }}
        >
          🍳
        </motion.div>
        <p style={{ fontWeight: 700, fontSize: '15px' }}>취향 분석 중...</p>
      </div>
    );
  }

  if (results.length === 0) {
    return <div style={{ textAlign: 'center', padding: '60px 20px', color: 'var(--text-gray)' }}>추천 결과가 없습니다.</div>;
  }

  return (
    <motion.div
      initial={{ opacity: 0, y: 20 }}
      animate={{ opacity: 1, y: 0 }}
      style={{ position: 'relative', minHeight: '100vh' }}
    >
      {/* Scattered decorative doodles */}
      <svg width="14" height="14" viewBox="0 0 20 20" fill="none" style={{ position: 'absolute', top: '10%', left: '5%', opacity: 0.18 }}>
        <path d="M10 1 L12 7.5 L19 7.5 L13.5 11.5 L15.5 18 L10 14 L4.5 18 L6.5 11.5 L1 7.5 L8 7.5 Z" fill="#333"/>
      </svg>
      <svg width="12" height="12" viewBox="0 0 20 20" fill="none" style={{ position: 'absolute', top: '50%', right: '4%', opacity: 0.15 }}>
        <path d="M10 1 L12 7.5 L19 7.5 L13.5 11.5 L15.5 18 L10 14 L4.5 18 L6.5 11.5 L1 7.5 L8 7.5 Z" fill="#333"/>
      </svg>

      {/* ── Heading ── */}
      <div style={{ textAlign: 'center', padding: '52px 24px 36px' }}>
        <h2 style={{
          fontSize: 'clamp(28px, 4vw, 42px)',
          fontWeight: 900,
          letterSpacing: '-1px',
          lineHeight: 1.15,
          color: 'var(--text-dark)',
          marginBottom: '10px',
        }}>
          나만의 맛 스니펫{' '}
          <span style={{ color: 'var(--text-gray)', fontWeight: 500 }}>완성!</span> 🎉
        </h2>
        <p style={{ color: 'var(--text-gray)', fontSize: '15px' }}>
          취향 벡터와 가장 가까운 식당을 순위로 제시합니다.
        </p>
      </div>

      {/* ── Card list container ── */}
      <div style={{ maxWidth: '680px', margin: '0 auto', padding: '0 24px' }}>

      <div style={{ display: 'flex', flexDirection: 'column', gap: '14px', marginBottom: '32px' }}>
        {results.map((item, idx) => {
          const rank = item.rank || idx + 1;
          const accent = CARD_ACCENTS[(rank - 1) % CARD_ACCENTS.length];
          const matchPct = item.match_percent != null
            ? item.match_percent
            : Math.round((item.similarity || 0) * 100);
          const rankScore = item.rank_score != null
            ? item.rank_score
            : (item.similarity_score != null ? item.similarity_score : item.similarity);
          const isExpanded = !!expanded[idx];
          const placeUrl = item.naver_place_url || item.place_url || item.naver_url || item.fallback_search_url;

          const topAxes = (item.top_axes || item.reasons || []).slice(0, 5);
          const axisScores = item.axis_scores || {};
          const axisContribs = item.axis_contributions || {};
          const matchData = topAxes.map(name => ({
            name, value: axisScores[name] ?? 0, contribution: axisContribs[name],
          }));

          const textRatio = item.text_evidence_ratio != null ? item.text_evidence_ratio : (item.fusion_weights?.text ?? 1);
          const imageRatio = item.image_evidence_ratio != null ? item.image_evidence_ratio : (item.fusion_weights?.image ?? 0);
          const textPct = Math.round(textRatio * 100);
          const imgPct = Math.round(imageRatio * 100);
          const hasImageBasis = imgPct > 0 && (item.image_confidence || 0) > 0.05;

          const repImg = item.representative_image?.image_src ? item.representative_image : null;
          const evSentences = item.evidence_sentences && typeof item.evidence_sentences === 'object' ? item.evidence_sentences : null;
          const pr = item.pseudo_relevance;

          return (
            <motion.div
              key={idx}
              initial={{ opacity: 0, y: 20 }}
              animate={{ opacity: 1, y: 0 }}
              transition={{ delay: idx * 0.07, ease: [0.22, 1, 0.36, 1] }}
              whileHover={{ y: -3 }}
              style={{
                backgroundColor: 'var(--white)',
                borderRadius: '24px',
                overflow: 'hidden',
                boxShadow: 'var(--shadow-md)',
                border: `1.5px solid ${matchPct >= 80 ? accent.border : 'var(--border-color)'}`,
              }}
            >
              {/* Recipe card top accent strip */}
              <div style={{
                height: '6px',
                background: accent.bg,
                borderBottom: `1.5px solid ${accent.border}`,
              }} />

              <div style={{ padding: '18px' }}>
                {/* Header row */}
                <div style={{ display: 'flex', gap: '14px', alignItems: 'flex-start' }}>
                  {/* Rank badge */}
                  <div style={{
                    width: '48px', height: '48px', borderRadius: '16px',
                    background: accent.bg, border: `1.5px solid ${accent.border}`,
                    display: 'flex', flexDirection: 'column', alignItems: 'center',
                    justifyContent: 'center', flexShrink: 0,
                  }}>
                    <span style={{ fontSize: '9px', fontWeight: 800, color: accent.rank, lineHeight: 1 }}>RANK</span>
                    <span style={{ fontSize: '20px', fontWeight: 900, color: accent.rank, lineHeight: 1 }}>{rank}</span>
                  </div>

                  {/* Thumbnail if available */}
                  {repImg && (
                    <img
                      src={repImg.image_src}
                      alt={repImg.label || item.name}
                      style={{ width: '64px', height: '64px', borderRadius: '16px', objectFit: 'cover', flexShrink: 0 }}
                      onError={e => { e.target.style.display = 'none'; }}
                    />
                  )}

                  {/* Name + match */}
                  <div style={{ flex: 1, minWidth: 0 }}>
                    <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'flex-start', gap: '8px' }}>
                      <div>
                        <h3 style={{ fontSize: '16px', fontWeight: 900, letterSpacing: '-0.3px', display: 'flex', alignItems: 'center', gap: '6px', flexWrap: 'wrap' }}>
                          {item.name}
                          {matchPct >= 80 && (
                            <span style={{
                              fontSize: '10px', background: accent.bg, color: accent.rank,
                              padding: '2px 8px', borderRadius: '999px', fontWeight: 800,
                            }}>강력 추천</span>
                          )}
                        </h3>
                        {item.address && (
                          <p style={{ fontSize: '11px', color: 'var(--text-gray)', marginTop: '3px' }}>{item.address}</p>
                        )}
                      </div>

                      {/* Match sticker */}
                      <div style={{
                        display: 'flex', flexDirection: 'column', alignItems: 'center',
                        background: accent.bg, border: `1.5px solid ${accent.border}`,
                        borderRadius: '16px', padding: '6px 12px', flexShrink: 0,
                        minWidth: '64px',
                      }}>
                        <StarSVG size={13} fill={accent.rank} />
                        <span style={{ fontSize: '22px', fontWeight: 900, color: accent.rank, lineHeight: 1.1 }}>{matchPct}%</span>
                        <span style={{ fontSize: '9px', fontWeight: 700, color: accent.rank, opacity: 0.7 }}>MATCH</span>
                      </div>
                    </div>

                    <div style={{ fontSize: '10px', color: 'var(--text-light)', marginTop: '4px' }}>
                      랭킹 점수 {typeof rankScore === 'number' ? rankScore.toFixed(2) : '—'}
                    </div>
                  </div>
                </div>

                {/* Debug reason */}
                {item.debug_reason && (
                  <div style={{ marginTop: '12px', fontSize: '12px', color: 'var(--text-gray)', background: 'var(--bg-color)', borderRadius: '10px', padding: '8px 12px' }}>
                    💡 {item.debug_reason}
                  </div>
                )}

                {/* Axis chips */}
                {topAxes.length > 0 && (
                  <div style={{ display: 'flex', flexWrap: 'wrap', gap: '6px', marginTop: '12px' }}>
                    {topAxes.slice(0, 3).map(r => {
                      const c = axisContribs[r];
                      const pct = typeof c === 'number' ? Math.round(c * 100) : null;
                      return (
                        <span key={r} style={{
                          fontSize: '12px', background: 'var(--bg-color)', color: 'var(--text-dark)',
                          padding: '4px 10px', borderRadius: '999px', fontWeight: 700,
                          border: '1px solid var(--border-color)',
                        }}>
                          #{r}{pct != null ? <span style={{ color: 'var(--text-light)', marginLeft: 3 }}>{pct}%</span> : null}
                        </span>
                      );
                    })}
                  </div>
                )}

                {/* Fusion ratio */}
                <div style={{ marginTop: '10px', display: 'flex', alignItems: 'center', gap: '6px', flexWrap: 'wrap', fontSize: '11px' }}>
                  <span style={{ fontWeight: 700 }}>반영</span>
                  <span style={{ background: '#EEF2FF', color: '#4338CA', padding: '2px 8px', borderRadius: '6px', fontWeight: 700 }}>
                    텍스트 {textPct}%
                  </span>
                  {hasImageBasis ? (
                    <span style={{ background: '#FEF3C7', color: '#92400E', padding: '2px 8px', borderRadius: '6px', fontWeight: 700 }}>
                      이미지 {imgPct}%
                    </span>
                  ) : (
                    <span style={{ background: '#F3F4F6', color: '#6B7280', padding: '2px 8px', borderRadius: '6px', fontWeight: 600 }}>
                      이미지 근거 없음
                    </span>
                  )}
                </div>

                {/* Pseudo-label badge */}
                {pr && (
                  <div style={{ marginTop: '8px', display: 'flex', alignItems: 'center', gap: '5px', fontSize: '11px' }}>
                    <span
                      title="평가용 pseudo-label"
                      style={{
                        display: 'inline-flex', alignItems: 'center', gap: '3px',
                        background: pr === 'relevant' ? '#DCFCE7' : '#F3F4F6',
                        color: pr === 'relevant' ? '#166534' : '#6B7280',
                        padding: '2px 8px', borderRadius: '6px', fontWeight: 700,
                      }}
                    >
                      {pr === 'relevant' ? '관련 있음' : '관련 낮음'} <HelpCircle size={10} />
                    </span>
                    <span style={{ color: 'var(--text-light)' }}>(pseudo-label)</span>
                  </div>
                )}

                {/* Expand toggle */}
                <button
                  onClick={() => toggle(idx)}
                  style={{
                    marginTop: '12px', background: 'transparent', border: 'none',
                    color: 'var(--text-gray)', fontSize: '12px', fontWeight: 700,
                    display: 'flex', alignItems: 'center', gap: '4px',
                    cursor: 'pointer', padding: 0, fontFamily: 'inherit',
                  }}
                >
                  자세히 보기 {isExpanded ? <ChevronUp size={13} /> : <ChevronDown size={13} />}
                </button>

                <AnimatePresence>
                  {isExpanded && (
                    <motion.div
                      key="expanded"
                      initial={{ opacity: 0, height: 0 }}
                      animate={{ opacity: 1, height: 'auto' }}
                      exit={{ opacity: 0, height: 0 }}
                      transition={{ duration: 0.25 }}
                      style={{ overflow: 'hidden' }}
                    >
                      <div style={{ borderTop: '1px solid var(--border-color)', paddingTop: '14px', marginTop: '10px', display: 'flex', flexDirection: 'column', gap: '14px' }}>
                        {(matchData.length > 0 || Object.keys(axisContribs).length > 0) && (
                          <div style={{ background: 'var(--bg-color)', borderRadius: '16px', padding: '16px 8px' }}>
                            <div style={{ fontFamily: 'var(--font-handwritten)', textAlign: 'center', fontSize: '18px', fontWeight: 800, marginBottom: '8px' }}>취향 밸런스 분석</div>
                            <RadarChartViz
                              data={axisScores}
                              axes={Array.from(new Set([
                                ...topAxes,
                                ...Object.keys(axisContribs).sort((a, b) => axisContribs[b] - axisContribs[a]).slice(0, 6),
                              ])).slice(0, 7)}
                            />
                            <div style={{ textAlign: 'center', fontSize: '11px', color: 'var(--text-gray)', marginTop: '4px' }}>
                              식당의 특징 점수와 취향 기여도를 종합한 그래프입니다.
                            </div>
                          </div>
                        )}

                        {evSentences && Object.keys(evSentences).length > 0 && (
                          <div>
                            <div style={{ fontSize: '12px', fontWeight: 800, marginBottom: '8px' }}>축별 추천 근거</div>
                            <div style={{ display: 'flex', flexDirection: 'column', gap: '6px' }}>
                              {Object.entries(evSentences).slice(0, 4).map(([ax, sents]) => (
                                <div key={ax} style={{ fontSize: '12px', background: 'var(--bg-color)', borderRadius: '10px', padding: '8px 12px' }}>
                                  <span style={{ fontWeight: 800, color: 'var(--btn-dark)' }}>#{ax}</span>{' '}
                                  <span style={{ color: 'var(--text-gray)', fontStyle: 'italic' }}>"{(sents || [])[0]}"</span>
                                </div>
                              ))}
                            </div>
                          </div>
                        )}

                        <EvaluationCard />
                      </div>
                    </motion.div>
                  )}
                </AnimatePresence>
              </div>

              {/* Naver link */}
              {placeUrl && (
                <div style={{ borderTop: '1px solid var(--border-color)', padding: '12px 18px', textAlign: 'right' }}>
                  <a
                    href={placeUrl}
                    target="_blank"
                    rel="noopener noreferrer"
                    style={{
                      display: 'inline-flex', alignItems: 'center', gap: '6px',
                      fontSize: '12px', fontWeight: 700, color: 'var(--text-dark)',
                      background: 'var(--bg-color)', border: '1.5px solid var(--border-color)',
                      borderRadius: '999px', padding: '7px 14px', textDecoration: 'none',
                    }}
                  >
                    {(item.naver_place_url || item.naver_url) ? '네이버 플레이스 보기' : '네이버에서 검색'} <ExternalLink size={12} />
                  </a>
                </div>
              )}
            </motion.div>
          );
        })}
      </div>

      <div style={{ textAlign: 'center', paddingBottom: '40px' }}>
        <motion.button
          onClick={onRestart}
          whileHover={{ scale: 1.04, y: -2 }}
          whileTap={{ scale: 0.96 }}
          style={{
            display: 'inline-flex', alignItems: 'center', gap: '8px',
            background: 'var(--btn-dark)', color: 'white', border: 'none',
            borderRadius: '999px', padding: '14px 36px', fontSize: '15px',
            fontWeight: 700, cursor: 'pointer', fontFamily: 'inherit',
            boxShadow: '0 6px 24px rgba(0,0,0,0.20)',
          }}
        >
          <RotateCcw size={16} /> 다시 테스트하기
        </motion.button>
      </div>
      </div>{/* /maxWidth container */}
    </motion.div>
  );
};

export default InferenceStep;
