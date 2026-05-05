import React, { useState } from 'react';
import { motion } from 'framer-motion';
import Button from '../ui/Button';
import AxisMatchBar from '../ui/AxisMatchBar';
import RadarChartViz from '../ui/RadarChartViz';
import EvaluationCard from '../ui/EvaluationCard';
import { axisLabel } from '../../utils/labels';
import { Star, ExternalLink, ChevronDown, ChevronUp, HelpCircle } from 'lucide-react';

const ordinal = (n) => `${n}위`;

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
          취향 벡터와 가장 가까운 식당을 순위로 제시합니다.
        </p>
      </div>

      {/* EvaluationCard moved to individual cards */}

      <div style={{ display: 'flex', flexDirection: 'column', gap: '16px', marginBottom: '32px' }}>
        {results.map((item, idx) => {
          const rank = item.rank || idx + 1;
          const matchPct = item.match_percent != null
            ? item.match_percent
            : Math.round((item.similarity || 0) * 100);
          const rankScore = item.rank_score != null
            ? item.rank_score
            : (item.similarity_score != null ? item.similarity_score : item.similarity);
          const isExpanded = !!expanded[idx];
          const placeUrl = item.naver_place_url || item.place_url || item.naver_url || item.fallback_search_url;

          // 축별 매칭 (top_axes + axis_scores)
          const topAxes = (item.top_axes || item.reasons || []).slice(0, 5);
          const axisScores = item.axis_scores || {};
          const axisContribs = item.axis_contributions || {};
          const matchData = topAxes.map((name) => ({
            name,
            value: axisScores[name] != null ? axisScores[name] : 0,
            contribution: axisContribs[name],
          }));

          // 추천 반영 비율 (text/image evidence ratio)
          const textRatio = item.text_evidence_ratio != null
            ? item.text_evidence_ratio
            : (item.fusion_weights?.text ?? 1);
          const imageRatio = item.image_evidence_ratio != null
            ? item.image_evidence_ratio
            : (item.fusion_weights?.image ?? 0);
          const textPct = Math.round(textRatio * 100);
          const imgPct = Math.round(imageRatio * 100);
          const hasImageBasis = imgPct > 0 && (item.image_confidence || 0) > 0.05;

          // 대표 이미지
          const repImg = item.representative_image && item.representative_image.image_src
            ? item.representative_image
            : null;

          // 축별 evidence
          const evSentences = item.evidence_sentences && typeof item.evidence_sentences === 'object'
            ? item.evidence_sentences
            : null;

          // pseudo-label relevance
          const pr = item.pseudo_relevance;     // "relevant" | "not_relevant" | null

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
              {/* 헤더 */}
              <div style={{ display: 'flex', gap: '12px', alignItems: 'flex-start' }}>
                {repImg && (
                  <img
                    src={repImg.image_src}
                    alt={repImg.label || item.name}
                    style={{ width: '72px', height: '72px', borderRadius: 'var(--radius-md)', objectFit: 'cover', flexShrink: 0 }}
                    onError={(e) => { e.target.style.display = 'none'; }}
                  />
                )}
                <div style={{ flex: 1, minWidth: 0 }}>
                  <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'flex-start', gap: '8px' }}>
                    <h3 style={{ fontSize: '17px', fontWeight: '800', display: 'flex', alignItems: 'center', gap: '6px', flexWrap: 'wrap' }}>
                      <span style={{
                        background: 'var(--primary-light, #FFF1ED)', color: 'var(--primary, #FF7E67)',
                        padding: '2px 8px', borderRadius: '6px', fontSize: '12px', fontWeight: 700,
                      }}>{ordinal(rank)}</span>
                      {item.name}
                      {matchPct >= 80 && <span style={{ fontSize: '11px', background: 'var(--primary-light)', color: 'var(--primary)', padding: '2px 8px', borderRadius: '10px' }}>강력 추천</span>}
                    </h3>
                    <div style={{ display: 'flex', alignItems: 'center', gap: '6px', backgroundColor: '#FFF5E5', padding: '8px 16px', borderRadius: '999px', flexShrink: 0, border: '1px solid #FCD34D' }}>
                      <Star size={18} color="#F59E0B" fill="#F59E0B" />
                      <span style={{ fontSize: '20px', fontWeight: '900', color: '#B45309' }}>{matchPct}%</span>
                    </div>
                  </div>
                  {item.address && (
                    <p style={{ fontSize: '12px', color: 'var(--text-gray)', marginTop: '4px' }}>{item.address}</p>
                  )}
                  <div style={{ fontSize: '11px', color: 'var(--text-gray)', marginTop: '4px' }}>
                    취향 일치도 {matchPct}% · 랭킹 점수 {typeof rankScore === 'number' ? rankScore.toFixed(2) : '—'}
                  </div>
                </div>
              </div>

              {/* 추천 한 줄 설명 */}
              {item.debug_reason && (
                <div style={{ marginTop: '10px', fontSize: '12px', color: 'var(--text-gray)' }}>
                  💡 {item.debug_reason}
                </div>
              )}

              {/* 주요 일치 축 + contribution % */}
              {topAxes.length > 0 && (
                <div style={{ display: 'flex', flexWrap: 'wrap', gap: '6px', marginTop: '10px' }}>
                  {topAxes.slice(0, 3).map((r) => {
                    const c = axisContribs[r];
                    const pct = typeof c === 'number' ? Math.round(c * 100) : null;
                    return (
                      <span key={r} style={{
                        fontSize: '12px',
                        backgroundColor: 'var(--bg-color)',
                        color: 'var(--text-dark)',
                        padding: '4px 10px',
                        borderRadius: '999px',
                        fontWeight: '600',
                      }}>
                        #{axisLabel(r, item)}{pct != null ? <span style={{ color: 'var(--text-gray)', marginLeft: 4 }}>{pct}%</span> : null}
                      </span>
                    );
                  })}
                </div>
              )}

              {/* 추천 반영 비율 */}
              <div style={{ marginTop: '10px', display: 'flex', alignItems: 'center', gap: '8px', flexWrap: 'wrap', fontSize: '11px' }}>
                <span style={{ fontWeight: 700, color: 'var(--text-dark)' }}>추천 반영 비율</span>
                <span style={{ background: '#EEF2FF', color: '#4338CA', padding: '3px 8px', borderRadius: '6px', fontWeight: '600' }}>
                  텍스트 {textPct}%
                </span>
                {hasImageBasis ? (
                  <span style={{ background: '#FEF3C7', color: '#92400E', padding: '3px 8px', borderRadius: '6px', fontWeight: '600' }}>
                    이미지 {imgPct}%
                  </span>
                ) : (
                  <span style={{ background: '#F3F4F6', color: '#6B7280', padding: '3px 8px', borderRadius: '6px', fontWeight: '600' }}>
                    이미지 근거 없음 · 텍스트 기반 추천
                  </span>
                )}
              </div>

              {/* pseudo-label 배지 */}
              {pr && (
                <div style={{ marginTop: '8px', display: 'flex', alignItems: 'center', gap: '6px', fontSize: '11px' }}>
                  <span
                    title="평가용 pseudo-label에서 이 식당이 관련 있음/없음으로 분류된 표기입니다. 실제 사용자 평가는 아닙니다."
                    style={{
                      display: 'inline-flex', alignItems: 'center', gap: '4px',
                      background: pr === 'relevant' ? '#DCFCE7' : '#F3F4F6',
                      color:       pr === 'relevant' ? '#166534' : '#6B7280',
                      padding: '3px 8px', borderRadius: '6px', fontWeight: 700,
                    }}
                  >
                    평가 기준: {pr === 'relevant' ? '관련 있음' : '관련 낮음'}
                    <HelpCircle size={11} />
                  </span>
                  <span style={{ color: 'var(--text-gray)' }}>(pseudo-label)</span>
                </div>
              )}

              {/* Evidence Quote Block Removed */}

              {/* 자세히 보기 토글 */}
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
                  {(matchData.length > 0 || Object.keys(axisContribs).length > 0) && (
                    <div style={{ 
                      background: 'var(--bg-color)', 
                      borderRadius: 'var(--radius-md)', 
                      padding: '20px 0',
                      marginTop: '10px'
                    }}>
                      <div style={{ textAlign: 'center', fontSize: '14px', fontWeight: 700, marginBottom: '4px', color: 'var(--text-dark)' }}>
                        취향 밸런스 분석
                      </div>
                      <RadarChartViz 
                        data={axisScores} 
                        axes={Array.from(new Set([
                          ...topAxes, 
                          ...Object.keys(axisContribs).sort((a, b) => axisContribs[b] - axisContribs[a]).slice(0, 6)
                        ])).slice(0, 7)} 
                      />
                      <div style={{ textAlign: 'center', fontSize: '11px', color: 'var(--text-gray)', marginTop: '4px' }}>
                        식당의 특징 점수와 취향 기여도를 종합한 그래프입니다.
                      </div>
                    </div>
                  )}
                  {evSentences && Object.keys(evSentences).length > 0 && (
                    <div>
                      <div style={{ fontSize: '12px', fontWeight: 700, marginBottom: '6px', color: 'var(--text-dark)' }}>
                        축별 추천 근거 문장
                      </div>
                      <div style={{ display: 'flex', flexDirection: 'column', gap: '6px' }}>
                        {Object.entries(evSentences).slice(0, 4).map(([ax, sents]) => {
                          const raw = (sents || [])[0] || '';
                          // 이모티콘/반복 정리 + 80~100자 자르기
                          const cleaned = String(raw)
                            .replace(/[\u{1F300}-\u{1FAFF}\u{2600}-\u{27BF}]/gu, '')
                            .replace(/[ㅋㅎ!?.~]{3,}/g, (s) => s.slice(0, 2))
                            .trim();
                          if (!cleaned) return null;
                          const truncated = cleaned.length > 95 ? cleaned.slice(0, 95) + '…' : cleaned;
                          return (
                            <div key={ax} style={{ fontSize: '12px' }}>
                              <span style={{ fontWeight: 700, color: 'var(--primary)' }}>#{axisLabel(ax, item)} 근거</span>{' '}
                              <span style={{ color: 'var(--text-gray)', fontStyle: 'italic' }}>"{truncated}"</span>
                            </div>
                          );
                        })}
                      </div>
                    </div>
                  )}

                  {/* Evaluation Metrics inserted here */}
                  <div style={{ marginTop: '10px' }}>
                     <EvaluationCard />
                  </div>
                </div>
              )}

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
                    {(item.naver_place_url || item.naver_url) ? '네이버 플레이스 보기' : '네이버에서 검색'} <ExternalLink size={13} />
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
