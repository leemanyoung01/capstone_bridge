import React, { useState, useEffect } from 'react';
import { motion, AnimatePresence } from 'framer-motion';
import RadarChartViz from '../ui/RadarChartViz';
import EvaluationCard from '../ui/EvaluationCard';
import CookingLoader from '../ui/CookingLoader';
import ShareButton from '../ui/ShareButton';
import { ExternalLink, ChevronDown, ChevronUp, RotateCcw, MapPin } from 'lucide-react';

const FRONT_RESULT_LIMIT = 5;

const AXIS_LABEL_MAP = {
  '번식감': '빵 식감',
  '패티육즙': '패티 육즙',
  '혼밥': '혼밥하기 좋은',
};
const getLabel = (key) => AXIS_LABEL_MAP[key] || key;

const InferenceStep = ({ results, error, scores = {}, config = {}, keyword = '', onRestart }) => {
  const [expanded, setExpanded] = useState({});
  const toggle = (idx) => setExpanded(p => ({ ...p, [idx]: !p[idx] }));

  const [breakdownOpen, setBreakdownOpen] = useState({});
  const toggleBreakdown = (idx) => setBreakdownOpen(p => ({ ...p, [idx]: !p[idx] }));

  const [statsOpen, setStatsOpen] = useState({});
  const [statsCache, setStatsCache] = useState({});
  const [statsLoading, setStatsLoading] = useState({});

  const toggleStats = async (idx, restaurantName) => {
    const wasOpen = !!statsOpen[idx];
    setStatsOpen(p => ({ ...p, [idx]: !wasOpen }));
    if (!wasOpen && !statsCache[idx] && !statsLoading[idx]) {
      setStatsLoading(p => ({ ...p, [idx]: true }));
      try {
        const url = `/api/axis_stats?restaurant=${encodeURIComponent(restaurantName)}&keyword=${encodeURIComponent(keyword)}`;
        const data = await fetch(url).then(r => r.json());
        setStatsCache(p => ({ ...p, [idx]: data }));
      } catch (e) {
        setStatsCache(p => ({ ...p, [idx]: { error: String(e) } }));
      } finally {
        setStatsLoading(p => ({ ...p, [idx]: false }));
      }
    }
  };

  if (error) {
    return (
      <div className="infer-error step-enter">
        <span style={{ fontSize: 48 }}>⚠️</span>
        <h2>추천을 불러오지 못했습니다</h2>
        <p className="muted">{error}</p>
        <button className="btn btn-primary btn-lg" onClick={onRestart}>
          <RotateCcw size={16} /> 처음으로 돌아가기
        </button>
      </div>
    );
  }

  if (!results) return <CookingLoader />;

  if (results.length === 0) {
    return (
      <div className="infer-error step-enter">
        <p className="muted">추천 결과가 없습니다.</p>
      </div>
    );
  }

  return (
    <div className="step-enter">
      {/* Header */}
      <div className="infer-head">
        <span className="eyebrow">Step 3</span>
        <h2 className="infer-title display">
          나만의 맛 스니펫 <span className="muted" style={{ fontWeight: 500 }}>완성!</span>
        </h2>
        <p className="muted" style={{ marginTop: 8 }}>취향 벡터와 가장 가까운 식당을 순위로 제시합니다.</p>
      </div>

      {/* Result list */}
      <div className="infer-list">
        {results.slice(0, FRONT_RESULT_LIMIT).map((item, idx) => {
          const rank = item.rank || idx + 1;
          const matchPct = item.match_percent != null
            ? item.match_percent
            : Math.round((item.similarity || 0) * 100);
          const isExpanded = !!expanded[idx];
          const placeUrl = item.naver_place_url || item.place_url || item.naver_url || item.fallback_search_url;
          const topAxes = (item.top_axes || item.reasons || []).slice(0, 5);
          const axisScores = item.axis_scores || {};
          const axisContribs = item.axis_contributions || {};
          const textPct = Math.round((item.text_evidence_ratio ?? item.fusion_weights?.text ?? 1) * 100);
          const imgPct = Math.round((item.image_evidence_ratio ?? item.fusion_weights?.image ?? 0) * 100);
          const hasImageBasis = imgPct > 0 && (item.image_confidence || 0) > 0.05;
          const repImg = item.representative_image?.image_src ? item.representative_image : null;
          const evSentences = item.evidence_sentences && typeof item.evidence_sentences === 'object'
            ? item.evidence_sentences : null;

          return (
            <motion.div
              key={idx}
              initial={{ opacity: 0, y: 20 }}
              animate={{ opacity: 1, y: 0 }}
              transition={{ delay: idx * 0.08 }}
              className={`card result-card ${rank === 1 ? 'is-top' : ''}`}
              style={{ animationDelay: `${idx * 0.08}s` }}
            >
              <div className="result-main">
                {/* Photo */}
                <div className="result-photo">
                  {repImg ? (
                    <img
                      src={repImg.image_src}
                      alt={repImg.label || item.name}
                      style={{ width: '100%', height: '100%', objectFit: 'cover' }}
                      onError={e => { e.target.style.display = 'none'; }}
                    />
                  ) : (
                    <div style={{
                      width: '100%', height: '100%',
                      display: 'flex', alignItems: 'center', justifyContent: 'center',
                      fontSize: 40, background: 'var(--bg-tint)',
                    }}>🍽️</div>
                  )}
                  <div className="result-rank num">#{rank}</div>
                  {/* 모바일 전용: 일치율 + Best Pick 오버레이 */}
                  <div className="result-photo-overlay">
                    <span className="result-photo-match num">{matchPct}%</span>
                    {rank === 1 && <span className="result-photo-best">🏆</span>}
                  </div>
                </div>

                {/* Info */}
                <div className="result-info">
                  <div className="result-info-top">
                    <div className="result-match">
                      <span className="result-match-num num">{matchPct}</span>
                      <span className="result-match-pct">% 일치</span>
                    </div>
                    {rank === 1 && (
                      <span className="result-best">🏆 Best Pick</span>
                    )}
                  </div>

                  <h3 className="result-name display">{item.name}</h3>

                  {item.address && (
                    <div className="result-addr muted">
                      <MapPin size={13} />
                      <span>{item.address}</span>
                    </div>
                  )}

                  {topAxes.length > 0 && (
                    <div className="result-tags">
                      {topAxes.slice(0, 3).map(r => {
                        const c = axisContribs[r];
                        const pct = typeof c === 'number' ? Math.round(c * 100) : null;
                        return (
                          <span key={r} className="result-tag">
                            #{getLabel(r)}
                            {pct != null && <span className="num"> {pct}%</span>}
                          </span>
                        );
                      })}
                    </div>
                  )}

                  {/* Evidence ratio bar */}
                  <div className="evbar">
                    <div className="evbar-track">
                      {textPct > 0 && (
                        <span className="evbar-fill text" style={{ width: `${textPct}%` }} />
                      )}
                      {hasImageBasis && imgPct > 0 && (
                        <span className="evbar-fill image" style={{ width: `${imgPct}%` }} />
                      )}
                    </div>
                    <div className="evbar-legend">
                      <span><span className="dot text" />텍스트 {textPct}%</span>
                      {hasImageBasis && <span><span className="dot image" />이미지 {imgPct}%</span>}
                    </div>
                  </div>
                </div>
              </div>

              {/* Expand toggle */}
              <button className="result-expand" onClick={() => toggle(idx)}>
                자세히 보기 {isExpanded ? <ChevronUp size={15} /> : <ChevronDown size={15} />}
              </button>

              <AnimatePresence>
                {isExpanded && (
                  <motion.div
                    key="detail"
                    initial={{ opacity: 0, height: 0 }}
                    animate={{ opacity: 1, height: 'auto' }}
                    exit={{ opacity: 0, height: 0 }}
                    transition={{ duration: 0.25 }}
                    style={{ overflow: 'hidden' }}
                    className="result-detail"
                  >
                    {/* Radar + Evidence */}
                    {(Object.keys(axisScores).length > 0 || Object.keys(axisContribs).length > 0) && (
                      <div className="result-detail-grid">
                        <div className="result-radar">
                          {(() => {
                            const selectedAxisKeys = Object.keys(scores).filter(ax => (scores[ax] || 0) > 0);
                            const radarLabelMap = { ...config.axis_label_map, ...AXIS_LABEL_MAP };
                            return (
                              <RadarChartViz
                                data={axisScores}
                                axes={selectedAxisKeys}
                                labelMap={radarLabelMap}
                                minDomainMax={0.1}
                              />
                            );
                          })()}
                        </div>

                        {evSentences && Object.keys(evSentences).length > 0 && (
                          <div>
                            <span className="eyebrow" style={{ display: 'block', marginBottom: 12 }}>축별 추천 근거</span>
                            {Object.entries(evSentences).slice(0, 6).map(([ax, sents]) => (
                              <div key={ax} className="evidence-item">
                                <span className="evidence-axis">#{getLabel(ax)}</span>
                                <p className="evidence-sent muted">"{(sents || [])[0]}"</p>
                              </div>
                            ))}
                          </div>
                        )}
                      </div>
                    )}

                    {/* Breakdown sub-toggle */}
                    {item.match_breakdown && (() => {
                      const br = item.match_breakdown;
                      const main = br.main || {};
                      const ev = br.evidence_split || {};
                      const txtPct2 = Math.round((ev.text_ratio || 0) * 100);
                      const imgPct2 = Math.round((ev.image_ratio || 0) * 100);
                      const isOpen = !!breakdownOpen[idx];
                      return (
                        <div style={{ marginTop: 12 }}>
                          <button className="subtoggle-btn" onClick={() => toggleBreakdown(idx)}>
                            <span>🔍 계산 근거 (일치도 산출 방식)</span>
                            {isOpen ? <ChevronUp size={14} /> : <ChevronDown size={14} />}
                          </button>
                          <AnimatePresence initial={false}>
                            {isOpen && (
                              <motion.div
                                key="br"
                                initial={{ opacity: 0, height: 0 }}
                                animate={{ opacity: 1, height: 'auto' }}
                                exit={{ opacity: 0, height: 0 }}
                                transition={{ duration: 0.2 }}
                                style={{ overflow: 'hidden' }}
                              >
                                <div style={{
                                  marginTop: 8, background: 'var(--bg-tint)',
                                  borderRadius: 'var(--radius-sm)', padding: '14px 16px',
                                  fontSize: 12.5, lineHeight: 1.7,
                                }}>
                                  <div style={{ fontWeight: 800, marginBottom: 4 }}>일치도 {br.final_percent}%</div>
                                  <div className="muted" style={{ fontSize: 12, marginBottom: 10 }}>
                                    선택한 취향 축과 이 식당 리뷰의 코사인 유사도입니다.
                                  </div>
                                  <div style={{ background: 'var(--surface)', borderRadius: 'var(--radius-xs)', padding: '8px 12px', marginBottom: 14, fontSize: 12, lineHeight: 1.9 }}>
                                    맛 취향 유사도 <strong>{Math.round((main.taste_cosine || 0) * 100)}%</strong>
                                    {(main.meta_boost_amount || 0) > 0 && (
                                      <span className="muted"> ＋ 부가 만족도 <strong>{Math.round(main.meta_boost_amount * 100)}%</strong></span>
                                    )}
                                    <span style={{ fontWeight: 700 }}>
                                      {' '}→ 최종 <span style={{ color: 'var(--accent)' }}>{br.final_percent}%</span>
                                    </span>
                                  </div>
                                  {/* Evidence bar */}
                                  <div style={{ fontWeight: 800, marginBottom: 8 }}>점수 근거 비중</div>
                                  <div style={{ display: 'flex', height: 28, borderRadius: 'var(--radius-xs)', overflow: 'hidden', marginBottom: 6, fontSize: 11, fontWeight: 700 }}>
                                    {txtPct2 > 0 && (
                                      <div style={{ width: `${txtPct2}%`, background: 'color-mix(in oklab, var(--accent) 20%, transparent)', color: 'var(--accent)', display: 'flex', alignItems: 'center', justifyContent: 'center' }}>
                                        📝 {txtPct2}%
                                      </div>
                                    )}
                                    {imgPct2 > 0 && (
                                      <div style={{ width: `${imgPct2}%`, background: 'color-mix(in oklab, var(--accent-2) 20%, transparent)', color: 'var(--ink-2)', display: 'flex', alignItems: 'center', justifyContent: 'center' }}>
                                        📷 {imgPct2}%
                                      </div>
                                    )}
                                  </div>
                                </div>
                              </motion.div>
                            )}
                          </AnimatePresence>
                        </div>
                      );
                    })()}

                    {/* Stats sub-toggle */}
                    {(() => {
                      const isStatsOpen = !!statsOpen[idx];
                      const isStatsLoading = !!statsLoading[idx];
                      const data = statsCache[idx];
                      return (
                        <div style={{ marginTop: 8 }}>
                          <button className="subtoggle-btn" onClick={() => toggleStats(idx, item.name)}>
                            <span>📊 축별 추출 정확도</span>
                            {isStatsOpen ? <ChevronUp size={14} /> : <ChevronDown size={14} />}
                          </button>
                          <AnimatePresence initial={false}>
                            {isStatsOpen && (
                              <motion.div
                                key="stats"
                                initial={{ opacity: 0, height: 0 }}
                                animate={{ opacity: 1, height: 'auto' }}
                                exit={{ opacity: 0, height: 0 }}
                                transition={{ duration: 0.2 }}
                                style={{ overflow: 'hidden' }}
                              >
                                <div style={{
                                  marginTop: 8, background: 'var(--surface)',
                                  border: 'var(--bw) solid var(--line)',
                                  borderRadius: 'var(--radius-sm)', padding: 14,
                                  fontSize: 12,
                                }}>
                                  {isStatsLoading && <span className="muted">불러오는 중...</span>}
                                  {data?.error && <span style={{ color: 'var(--c-tomato)' }}>에러: {data.error}</span>}
                                  {data && !data.error && (() => {
                                    const userPickedAxes = new Set(Object.keys(scores || {}).filter(ax => (scores[ax] || 0) > 0));
                                    const allAxes = data.axes || [];
                                    const pickedAxes = allAxes.filter(a => userPickedAxes.has(a.axis));
                                    const displayAxes = pickedAxes.length > 0 ? pickedAxes : allAxes.slice(0, 5);
                                    return (
                                      <>
                                        <div style={{ fontWeight: 800, marginBottom: 8 }}>
                                          리뷰 {data.total_reviews}건에서 추출
                                        </div>
                                        <div style={{ overflowX: 'auto' }}>
                                          <table className="stats-table">
                                            <thead>
                                              <tr>
                                                <th>축</th>
                                                <th style={{ textAlign: 'right' }}>매칭 리뷰</th>
                                                <th style={{ textAlign: 'right' }}>매칭률</th>
                                                <th style={{ textAlign: 'right' }}>lex</th>
                                                <th style={{ textAlign: 'right' }}>BERT</th>
                                              </tr>
                                            </thead>
                                            <tbody>
                                              {displayAxes
                                                .map(a => ({
                                                  ...a,
                                                  _ev: (a.evidence_count || 0) > 0
                                                    ? a.evidence_count
                                                    : (a.semantic_evidence_samples || []).length,
                                                }))
                                                .filter(a => a._ev > 0)
                                                .map(a => (
                                                  <tr key={a.axis}>
                                                    <td style={{ fontWeight: 700 }}>{a.label || a.axis}</td>
                                                    <td className="num" style={{ textAlign: 'right' }}>{a.total_hits}</td>
                                                    <td className="num" style={{ textAlign: 'right' }}>{(a.hit_rate * 100).toFixed(1)}%</td>
                                                    <td className="num" style={{ textAlign: 'right' }}>{a.text_vector_score != null ? a.text_vector_score.toFixed(3) : '-'}</td>
                                                    <td className="num" style={{ textAlign: 'right' }}>{a.semantic_vector_score != null ? a.semantic_vector_score.toFixed(3) : '-'}</td>
                                                  </tr>
                                                ))}
                                            </tbody>
                                          </table>
                                        </div>
                                      </>
                                    );
                                  })()}
                                </div>
                              </motion.div>
                            )}
                          </AnimatePresence>
                        </div>
                      );
                    })()}

                    <EvaluationCard />
                  </motion.div>
                )}
              </AnimatePresence>

              {/* Footer */}
              <div className="result-foot">
                <ShareButton item={item} />
                {placeUrl && (
                  <a
                    href={placeUrl}
                    target="_blank"
                    rel="noopener noreferrer"
                    className="btn btn-ghost result-naver"
                  >
                    {(item.naver_place_url || item.naver_url) ? '네이버 플레이스' : '네이버 검색'}
                    <ExternalLink size={13} />
                  </a>
                )}
              </div>
            </motion.div>
          );
        })}
      </div>

      {/* Restart */}
      <div className="infer-restart">
        <button className="btn btn-ghost btn-lg" onClick={onRestart}>
          <RotateCcw size={16} /> 다시 테스트하기
        </button>
      </div>
    </div>
  );
};

export default InferenceStep;
