
import React, { useState } from 'react';
import { motion, AnimatePresence } from 'framer-motion';
// AxisMatchBar는 현재 코드에서 사용되지 않아 주석 처리 또는 삭제를 권장합니다.
// import AxisMatchBar from '../ui/AxisMatchBar'; 
import RadarChartViz from '../ui/RadarChartViz';
import EvaluationCard from '../ui/EvaluationCard';
import CookingLoader from '../ui/CookingLoader';
import ShareButton from '../ui/ShareButton';
import { ExternalLink, ChevronDown, ChevronUp, RotateCcw } from 'lucide-react'; // HelpCircle도 미사용이라 제외

// ── Background Doodles ──
const StarDoodle = ({ size = 14, style = {} }) => (
  <svg width={size} height={size} viewBox="0 0 20 20" fill="none" style={style}>
    <path d="M10 1 L12 7.5 L19 7.5 L13.5 11.5 L15.5 18 L10 14 L4.5 18 L6.5 11.5 L1 7.5 L8 7.5 Z" fill="#333" opacity="0.12" />
  </svg>
);

const WavyLine = ({ style = {} }) => (
  <svg width="48" height="14" viewBox="0 0 48 14" fill="none" style={style}>
    <path d="M2 7 C6 2 10 12 14 7 C18 2 22 12 26 7 C30 2 34 12 38 7 C42 2 46 12 50 7" stroke="#888" strokeWidth="1.8" strokeLinecap="round" fill="none" opacity="0.2" />
  </svg>
);

const PlusDoodle = ({ style = {} }) => (
  <svg width="20" height="20" viewBox="0 0 20 20" fill="none" style={style}>
    <line x1="10" y1="2" x2="10" y2="18" stroke="#555" strokeWidth="2.2" strokeLinecap="round" opacity="0.18" />
    <line x1="2" y1="10" x2="18" y2="10" stroke="#555" strokeWidth="2.2" strokeLinecap="round" opacity="0.18" />
  </svg>
);

// Pastel accent pool for rank cards
const CARD_ACCENTS = [
  { bg: '#FFF3C4', border: '#F5D45E', rank: '#B87A00' },
  { bg: '#E8DCFF', border: '#C4A8F5', rank: '#6B3FA0' },
  { bg: '#D4EDD4', border: '#9DD09D', rank: '#2E7D32' },
  { bg: '#DAEAFF', border: '#90BFFF', rank: '#1A56B0' },
  { bg: '#FFE4D6', border: '#F5A07A', rank: '#B84E00' },
];

// 추천 결과 화면 상한. 백엔드도 기본 5건만 내려주지만, 백엔드 실수로 더 내려와도
// 프론트에서 한 번 더 자른다 (이중 방어).
const FRONT_RESULT_LIMIT = 5;

// ── Axis Label Mapping (For UI Display Only) ──
const AXIS_LABEL_MAP = {
  '번식감': '빵 식감',
  '패티육즙': '패티 육즙',
  '혼밥': '혼밥하기 좋은'
  // 필요한 경우 여기에 추가 매핑을 넣을 수 있습니다.
};

const getLabel = (key) => AXIS_LABEL_MAP[key] || key;

const InferenceStep = ({ results, error, scores = {}, config = {}, keyword = '', onRestart }) => {
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
    return <CookingLoader />;
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
      <StarDoodle size={18} style={{ position: 'absolute', top: '8%', left: '3%' }} />
      <PlusDoodle style={{ position: 'absolute', top: '25%', left: '2%' }} />
      <WavyLine style={{ position: 'absolute', top: '45%', left: '4%' }} />
      <StarDoodle size={12} style={{ position: 'absolute', top: '15%', right: '3%' }} />
      <PlusDoodle style={{ position: 'absolute', top: '35%', right: '2%' }} />
      <WavyLine style={{ position: 'absolute', top: '55%', right: '1%' }} />

      {/* ── Heading ── */}
      <div style={{ textAlign: 'center', padding: '52px 24px 36px' }}>
        <h2 style={{
          fontSize: 'clamp(32px, 5vw, 48px)',
          fontWeight: 900,
          letterSpacing: '-1px',
          lineHeight: 1.15,
          color: 'var(--text-dark)',
          marginBottom: '10px',
        }}>
          나만의 맛 스니펫 <span style={{ color: 'var(--text-gray)' }}>완성!</span> 🎉
        </h2>
        <p style={{ color: 'var(--text-gray)', fontSize: '15px' }}>
          취향 벡터와 가장 가까운 식당을 순위로 제시합니다.
        </p>
      </div>

      {/* ── Card list container ── */}
      <div style={{ maxWidth: '680px', margin: '0 auto', padding: '0 24px' }}>

        <div style={{ display: 'flex', flexDirection: 'column', gap: '14px', marginBottom: '32px' }}>
          {/* Top-N 안전 컷 — 백엔드도 기본 5건만 내려주지만, 더 와도 화면에는 5건만. */}
          {results.slice(0, FRONT_RESULT_LIMIT).map((item, idx) => {
            const rank = item.rank || idx + 1;
            const accent = CARD_ACCENTS[(rank - 1) % CARD_ACCENTS.length];
            const matchPct = item.match_percent != null
              ? item.match_percent
              : Math.round((item.similarity || 0) * 100);

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

            // 유기적인 스크랩북 느낌을 위한 미세한 기울기
            const rotation = (idx % 2 === 0 ? -0.7 : 0.7) + (idx * 0.1 % 0.5);

            return (
              <motion.div
                key={idx}
                initial={{ opacity: 0, y: 30, rotate: 0 }}
                animate={{ opacity: 1, y: 0, rotate: rotation }}
                transition={{ delay: idx * 0.08, type: 'spring', stiffness: 200, damping: 20 }}
                whileHover={{ y: -5, rotate: 0 }}
                style={{
                  backgroundColor: 'var(--white)',
                  borderRadius: '8px',
                  overflow: 'visible',
                  boxShadow: '0 12px 32px rgba(0,0,0,0.08), 0 2px 8px rgba(0,0,0,0.04)',
                  border: '1px solid var(--border-color)',
                  position: 'relative',
                  padding: '24px 20px',
                }}
              >
                {/* ── 1. 금메달 랭킹 배지 (Top Left Overlapping) ── */}
                <div style={{
                  position: 'absolute', top: '-14px', left: '-14px', zIndex: 10,
                  width: '56px', height: '56px', borderRadius: '50%',
                  background: accent.bg, border: `2.5px dashed ${accent.border}`,
                  boxShadow: '0 4px 12px rgba(0,0,0,0.15)',
                  display: 'flex', flexDirection: 'column', alignItems: 'center', justifyContent: 'center',
                  transform: 'rotate(-10deg)',
                }}>
                  <span style={{ fontSize: '10px', fontWeight: 900, color: accent.rank, lineHeight: 1 }}>RANK</span>
                  <span style={{ fontSize: '24px', fontWeight: 900, color: accent.rank, lineHeight: 1 }}>{rank}</span>
                </div>

                {/* ── 2. 매치 점수 스탬프 (Top Right) ── */}
                <div style={{
                  position: 'absolute', top: '20px', right: '20px', zIndex: 5,
                  display: 'flex', flexDirection: 'column', alignItems: 'center', justifyContent: 'center',
                  width: '68px', height: '68px', borderRadius: '50%',
                  border: `3.5px double ${accent.border}`,
                  color: accent.rank,
                  transform: 'rotate(15deg)',
                  opacity: 0.85,
                }}>
                  <span style={{ fontSize: '19px', fontWeight: 900, lineHeight: 1 }}>{matchPct}%</span>
                  <span style={{ fontSize: '12px', fontWeight: 800 }}>일치</span>
                </div>

                {/* ── 3. 가로 배치 레이아웃 (Image Left, Details Right) ── */}
                <div style={{ display: 'flex', gap: '20px', alignItems: 'flex-start' }}>

                  {/* 왼쪽: 큼직한 대표 이미지 (폴라로이드 프레임) */}
                  <div style={{
                    width: '130px', flexShrink: 0,
                    background: 'white', padding: '6px 6px 18px',
                    boxShadow: '0 4px 12px rgba(0,0,0,0.1), inset 0 0 0 1px rgba(0,0,0,0.05)',
                    borderRadius: '3px',
                    transform: 'rotate(-2deg)',
                    alignSelf: 'center',
                  }}>
                    {repImg ? (
                      <img
                        src={repImg.image_src}
                        alt={repImg.label || item.name}
                        style={{ width: '100%', aspectRatio: '1/1', objectFit: 'cover', borderRadius: '2px' }}
                        onError={e => { e.target.style.display = 'none'; }}
                      />
                    ) : (
                      <div style={{
                        width: '100%', aspectRatio: '1/1', background: '#F5F0EA',
                        display: 'flex', alignItems: 'center', justifyContent: 'center',
                        fontSize: '40px', borderRadius: '2px'
                      }}>🍽️</div>
                    )}
                  </div>

                  {/* 오른쪽: 상세 정보 */}
                  <div style={{ flex: 1, minWidth: 0, paddingTop: '4px' }}>

                    {/* 식당 이름 & 주소 */}
                    <div style={{ paddingRight: '60px' }}>
                      <h3 style={{
                        fontSize: '28px',
                        fontWeight: 800,
                        color: 'var(--text-dark)',
                        marginBottom: '6px',
                        lineHeight: 1.1,
                        letterSpacing: '-0.5px'
                      }}>
                        {item.name}
                      </h3>
                      {item.address && (
                        <p style={{ fontSize: '12px', color: 'var(--text-gray)' }}>📍 {item.address}</p>
                      )}
                    </div>

                    {/* 특징 태그 (마스킹 테이프 스타일) */}
                    {topAxes.length > 0 && (
                      <div style={{ display: 'flex', flexWrap: 'wrap', gap: '6px', marginTop: '16px' }}>
                        {topAxes.slice(0, 3).map(r => {
                          const c = axisContribs[r];
                          const pct = typeof c === 'number' ? Math.round(c * 100) : null;
                          return (
                            <span key={r} style={{
                              fontSize: '15px', background: '#F5F0EA', color: 'var(--text-dark)',
                              padding: '4px 12px', borderRadius: '4px 12px 12px 4px', fontWeight: 700,
                              borderLeft: '3px solid var(--primary-light)',
                              boxShadow: '1px 2px 4px rgba(0,0,0,0.05)',
                            }}>
                              #{getLabel(r)}{pct != null ? <span style={{ color: 'var(--text-gray)', marginLeft: 4, fontSize: '13px' }}>{pct}%</span> : null}
                            </span>
                          );
                        })}
                      </div>
                    )}

                    {/* 반영 비율 (귀여운 스티커 스타일) */}
                    <div style={{ marginTop: '12px', display: 'flex', alignItems: 'center', gap: '8px', flexWrap: 'wrap' }}>
                      <span style={{
                        fontSize: '14px',
                        background: '#EEF2FF', color: '#4338CA',
                        padding: '3px 10px', borderRadius: '3px', fontWeight: 700,
                        transform: 'rotate(-1.5deg)'
                      }}>
                        📝 텍스트 {textPct}%
                      </span>
                      {hasImageBasis && (
                        <span style={{
                          fontSize: '14px',
                          background: '#FEF3C7', color: '#92400E',
                          padding: '3px 10px', borderRadius: '3px', fontWeight: 700,
                          transform: 'rotate(1.5deg)'
                        }}>
                          📸 이미지 {imgPct}%
                        </span>
                      )}
                    </div>

                    {/* AI 요약/디버그 이유 */}
                    {item.debug_reason && (
                      <div style={{
                        marginTop: '16px', fontSize: '13px', color: 'var(--text-gray)',
                        background: 'var(--bg-color)',
                        padding: '8px 12px', borderRadius: '8px', border: '1px dashed var(--border-color)'
                      }}>
                        💡 {item.debug_reason}
                      </div>
                    )}

                  </div>
                </div>

                {/* Expand toggle (손글씨 적용) */}
                <button
                  onClick={() => toggle(idx)}
                  style={{
                    marginTop: '20px', background: 'transparent', border: 'none',
                    color: 'var(--text-gray)', fontSize: '16px', fontWeight: 700,
                    display: 'flex', alignItems: 'center', gap: '4px',
                    cursor: 'pointer', padding: 0,
                    marginLeft: 'auto'
                  }}
                >
                  자세히 보기 {isExpanded ? <ChevronUp size={16} /> : <ChevronDown size={16} />}
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
                            <div style={{ textAlign: 'center', fontSize: '18px', fontWeight: 800, marginBottom: '8px' }}>취향 밸런스 분석</div>
                            {(() => {
                              const selectedAxisKeys = Object.keys(scores).filter(ax => (scores[ax] || 0) > 0);
                              const radarLabelMap = {
                                ...config.axis_label_map,
                                ...AXIS_LABEL_MAP
                              };
                              // TasteStep의 로컬 매핑 로직 반영
                              const AXIS_DISPLAY_LABELS = {
                                커피: { 온도: '따뜻함' },
                                김밥: { 밥의간과양: '밥의 간과 양' },
                                버거: { 번식감: '빵 식감', 패티육즙: '패티 육즙' },
                              };
                              selectedAxisKeys.forEach(ax => {
                                if (AXIS_DISPLAY_LABELS[keyword]?.[ax]) {
                                  radarLabelMap[ax] = AXIS_DISPLAY_LABELS[keyword][ax];
                                }
                              });

                              return (
                                <RadarChartViz
                                  data={axisScores}
                                  axes={selectedAxisKeys}
                                  labelMap={radarLabelMap}
                                  minDomainMax={0.1}
                                />
                              );
                            })()}
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
                                  <span style={{ fontWeight: 800, color: 'var(--btn-dark)' }}>#{getLabel(ax)}</span>{' '}
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

                {/* Card footer — share + naver link side by side */}
                <div style={{
                  borderTop: '1px solid var(--border-color)',
                  padding: '12px 18px',
                  marginTop: '16px',
                  display: 'flex',
                  justifyContent: 'flex-end',
                  alignItems: 'center',
                  gap: '8px',
                  flexWrap: 'wrap',
                }}>
                  <ShareButton item={item} />
                  {placeUrl && (
                    <a
                      href={placeUrl}
                      target="_blank"
                      rel="noopener noreferrer"
                      style={{
                        display: 'inline-flex', alignItems: 'center', gap: '6px',
                        fontSize: '12px', fontWeight: 700, color: 'var(--text-dark)',
                        background: 'var(--bg-color)', border: '1.5px solid var(--border-color)',
                        borderRadius: '999px', padding: '7px 14px', textDecoration: 'none',
                        lineHeight: 1,
                      }}
                    >
                      {(item.naver_place_url || item.naver_url) ? '네이버 플레이스 보기' : '네이버에서 검색'} <ExternalLink size={12} />
                    </a>
                  )}
                </div>
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
      </div>
    </motion.div>
  );
};

export default InferenceStep;