import React from 'react';
import { motion, AnimatePresence } from 'framer-motion';
import RadarChartViz from '../ui/RadarChartViz';
import Button from '../ui/Button';

const GROUP_PRIORITY = ['공통', '맛', '식감', '기타', '메타'];

const CHIP_ROTATIONS = [-0.6, -0.4, -0.2, 0, 0.2, 0.4, 0.6];
const chipRotation = (axis) => {
  let hash = 0;
  for (let i = 0; i < axis.length; i++) hash += axis.charCodeAt(i);
  return CHIP_ROTATIONS[hash % CHIP_ROTATIONS.length];
};

// ── Shared inline SVG decorations ──────────────────────────────
const StarDoodle = ({ size = 16, style = {} }) => (
  <svg width={size} height={size} viewBox="0 0 20 20" fill="none" style={style}>
    <path d="M10 1 L12 7.5 L19 7.5 L13.5 11.5 L15.5 18 L10 14 L4.5 18 L6.5 11.5 L1 7.5 L8 7.5 Z"
      fill="#333" opacity="0.18" />
  </svg>
);

const SparkDoodle = ({ style = {} }) => (
  <svg width="22" height="22" viewBox="0 0 22 22" fill="none" style={style}>
    <line x1="11" y1="1"  x2="11" y2="7"  stroke="#555" strokeWidth="2" strokeLinecap="round" opacity="0.22"/>
    <line x1="11" y1="15" x2="11" y2="21" stroke="#555" strokeWidth="2" strokeLinecap="round" opacity="0.22"/>
    <line x1="1"  y1="11" x2="7"  y2="11" stroke="#555" strokeWidth="2" strokeLinecap="round" opacity="0.22"/>
    <line x1="15" y1="11" x2="21" y2="11" stroke="#555" strokeWidth="2" strokeLinecap="round" opacity="0.22"/>
    <circle cx="11" cy="11" r="2.5" fill="#555" opacity="0.18"/>
  </svg>
);

const WavyLine = ({ style = {} }) => (
  <svg width="48" height="14" viewBox="0 0 48 14" fill="none" style={style}>
    <path d="M2 7 C6 2 10 12 14 7 C18 2 22 12 26 7 C30 2 34 12 38 7 C42 2 46 12 50 7"
      stroke="#888" strokeWidth="1.8" strokeLinecap="round" fill="none" opacity="0.3"/>
  </svg>
);

const PlusDoodle = ({ style = {} }) => (
  <svg width="20" height="20" viewBox="0 0 20 20" fill="none" style={style}>
    <line x1="10" y1="2" x2="10" y2="18" stroke="#555" strokeWidth="2" strokeLinecap="round" opacity="0.25"/>
    <line x1="2"  y1="10" x2="18" y2="10" stroke="#555" strokeWidth="2" strokeLinecap="round" opacity="0.25"/>
  </svg>
);

const TasteStep = ({ keyword, config, scores, onChipClick, onScoreChange, onNext, onReset }) => {
  const { groups = {} } = config;

  const used = new Set();
  const groupOrder = [];
  GROUP_PRIORITY.forEach(label => {
    if (groups[label]) { groupOrder.push({ label, axes: groups[label] }); used.add(label); }
  });
  Object.keys(groups).forEach(label => {
    if (!used.has(label)) groupOrder.push({ label, axes: groups[label] });
  });

  const radarData = groupOrder.reduce((acc, group) => {
    const selectedAxes = group.axes.filter(ax => (scores[ax] || 0) > 0);
    const sum = selectedAxes.reduce((s, ax) => s + scores[ax], 0);
    // 선택된 항목들의 평균 + 갯수에 따른 약간의 가중치로 시각적 불균형 해소
    acc[group.label] = selectedAxes.length > 0 ? (sum / selectedAxes.length) + (selectedAxes.length * 1.5) : 0;
    return acc;
  }, {});

  const selectedCount = Object.values(scores).filter(v => v > 0).length;

  return (
    <motion.div
      initial={{ opacity: 0, y: 16 }}
      animate={{ opacity: 1, y: 0 }}
      style={{ position: 'relative', minHeight: '100vh' }}
    >
      {/* ── Scattered background doodles ── */}
      <StarDoodle   style={{ position: 'absolute', top: '8%',   left:  '2%' }} />
      <PlusDoodle   style={{ position: 'absolute', top: '20%',  left:  '1.5%' }} />
      <WavyLine     style={{ position: 'absolute', top: '45%',  left:  '1%' }} />
      <SparkDoodle  style={{ position: 'absolute', bottom: '18%', left: '2%' }} />
      <StarDoodle size={12} style={{ position: 'absolute', top: '12%',  right: '2%' }} />
      <PlusDoodle   style={{ position: 'absolute', top: '55%',  right: '1.5%' }} />
      <WavyLine     style={{ position: 'absolute', bottom: '25%', right: '1%' }} />
      <SparkDoodle  style={{ position: 'absolute', bottom: '10%', right: '2%' }} />

      {/* ── Shared max-width wrapper (heading + grid aligned together) ── */}
      <div style={{ maxWidth: '980px', margin: '0 auto', padding: '0 28px 60px' }}>

        {/* ── Top-left asymmetric header ── */}
        <div style={{ padding: '52px 0 36px', textAlign: 'left' }}>
          <h2 style={{
            fontSize: 'clamp(28px, 4vw, 46px)',
            fontWeight: 900,
            letterSpacing: '-1.5px',
            lineHeight: 1.1,
            color: 'var(--text-dark)',
            marginBottom: '10px',
          }}>
            <span style={{ color: 'var(--primary)' }}>{keyword}</span>{' '}
            <span style={{ color: 'var(--text-gray)', fontWeight: 500 }}>맛 취향 탐색</span>
          </h2>
          <p style={{ fontSize: '15px', color: 'var(--text-gray)', display: 'flex', alignItems: 'center', gap: '10px' }}>
            끌리는 단어를 자유롭게 선택해주세요
            <AnimatePresence>
              {selectedCount > 0 && (
                <motion.span
                  key="badge"
                  initial={{ opacity: 0, scale: 0.7 }}
                  animate={{ opacity: 1, scale: 1 }}
                  exit={{ opacity: 0, scale: 0.7 }}
                  style={{
                    background: 'var(--primary)', color: 'white',
                    fontSize: '12px', fontWeight: 800,
                    padding: '2px 10px', borderRadius: '999px',
                  }}
                >
                  {selectedCount}개 선택됨
                </motion.span>
              )}
            </AnimatePresence>
          </p>
        </div>

        {/* ── Two-column layout ── */}
        <div style={{
          display: 'grid',
          gridTemplateColumns: 'minmax(0, 1fr) 380px',
          gap: '40px',
          alignItems: 'start',
        }}>
          {/* Left: chip groups with airy dividers */}
          <div>
            {groupOrder.map((group, gIdx) => {
              const isLast = gIdx === groupOrder.length - 1;
              return (
                <div
                  key={group.label}
                  style={{
                    borderBottom: isLast ? 'none' : '1px solid rgba(0,0,0,0.07)',
                    padding: '24px 0',
                  }}
                >
                  <h3 style={{
                    fontFamily: 'var(--font-handwritten)',
                    fontSize: '18px',
                    fontWeight: 800,
                    marginBottom: '18px',
                    color: 'var(--text-gray)',
                    textTransform: 'uppercase',
                    letterSpacing: '0.5px',
                  }}>
                    {group.label}
                  </h3>
                  <div style={{ display: 'flex', flexWrap: 'wrap', gap: '8px' }}>
                    {group.axes.map(axis => {
                      const score = scores[axis];
                      const isSelected = score !== 0;
                      return (
                        <motion.div
                          key={axis}
                          whileHover={{ y: isSelected ? 0 : -2, rotate: 0 }}
                          whileTap={{ scale: 0.94 }}
                          onClick={() => onChipClick(axis)}
                          style={{
                            rotate: chipRotation(axis),
                            display: 'flex', alignItems: 'center',
                            height: '42px', boxSizing: 'border-box',
                            padding: isSelected ? '0 8px 0 16px' : '0 18px',
                            borderRadius: '999px',
                            fontFamily: 'var(--font-handwritten)',
                            fontSize: '15px', fontWeight: 700,
                            // ── Claymorphism jelly sticker ──
                            backgroundColor: isSelected
                              ? 'var(--primary-light)'
                              : '#F5F0EA',
                            color: isSelected ? 'var(--primary)' : 'var(--text-dark)',
                            border: isSelected
                              ? '1.5px solid rgba(255,126,103,0.5)'
                              : '1.5px solid rgba(255,255,255,0.85)',
                            boxShadow: isSelected
                              ? '0 0 0 4px rgba(255,126,103,0.12), 0 4px 14px rgba(255,126,103,0.22), inset 0 1px 0 rgba(255,255,255,0.8)'
                              : '0 3px 10px rgba(0,0,0,0.07), 0 1px 3px rgba(0,0,0,0.04), inset 0 1px 0 rgba(255,255,255,0.95), inset 0 -1px 0 rgba(0,0,0,0.03)',
                            cursor: 'pointer', whiteSpace: 'nowrap',
                            transition: 'background 0.18s, color 0.18s, box-shadow 0.22s, border 0.18s',
                          }}
                        >
                          <span>{axis}</span>

                          {isSelected && (
                            <div
                              style={{ display: 'flex', alignItems: 'center', gap: '4px', marginLeft: 10 }}
                              onClick={e => e.stopPropagation()}
                            >
                              <div style={{
                                background: 'rgba(255,126,103,0.12)',
                                borderRadius: '999px', padding: '3px 6px',
                                display: 'flex', alignItems: 'center', gap: '4px',
                                whiteSpace: 'nowrap', flexWrap: 'nowrap'
                              }}>
                                  <button
                                    onClick={() => {
                                      const val = (parseInt(score) || 0) - 1;
                                      onScoreChange(axis, val <= 0 ? 0 : val);
                                    }}
                                    style={{
                                      width: '22px', height: '22px', borderRadius: '50%',
                                      background: 'white', color: 'var(--primary)',
                                      fontSize: '15px', fontWeight: 900, border: 'none',
                                      cursor: 'pointer', display: 'flex', alignItems: 'center',
                                      justifyContent: 'center', flexShrink: 0,
                                      boxShadow: '0 1px 4px rgba(0,0,0,0.1)',
                                    }}
                                  >−</button>
                                  <input
                                    type="number"
                                    value={score}
                                    autoFocus={score === 1}
                                    onChange={e => onScoreChange(axis, e.target.value)}
                                    onBlur={e => {
                                      const val = parseInt(e.target.value);
                                      if (isNaN(val) || val < 1) onScoreChange(axis, 1);
                                      else if (val > 10) onScoreChange(axis, 10);
                                    }}
                                    style={{
                                      width: '28px', textAlign: 'center', fontSize: '13px', fontWeight: 900,
                                      background: 'transparent', border: 'none', color: 'var(--primary)',
                                      appearance: 'none', MozAppearance: 'textfield', padding: 0, outline: 'none',
                                    }}
                                  />
                                  <button
                                    onClick={() => onScoreChange(axis, (parseInt(score) || 0) + 1)}
                                    disabled={(parseInt(score) || 0) >= 10}
                                    style={{
                                      width: '22px', height: '22px', borderRadius: '50%',
                                      background: 'white', color: 'var(--primary)',
                                      fontSize: '15px', fontWeight: 900, border: 'none',
                                      cursor: 'pointer', display: 'flex', alignItems: 'center',
                                      justifyContent: 'center', flexShrink: 0,
                                      opacity: (parseInt(score) || 0) >= 10 ? 0.4 : 1,
                                      boxShadow: '0 1px 4px rgba(0,0,0,0.1)',
                                    }}
                                  >+</button>
                                </div>
                            </div>
                          )}
                        </motion.div>
                      );
                    })}
                  </div>
                </div>
              );
            })}
          </div>

          {/* Right: sticky radar panel */}
          <div style={{
            background: 'rgba(255,255,255,0.95)',
            borderRadius: '24px',
            padding: '26px',
            position: 'sticky',
            top: '24px',
            boxShadow: '0 6px 28px rgba(0,0,0,0.08)',
            border: '1px solid rgba(0,0,0,0.06)',
          }}>
            <h3 style={{ fontFamily: 'var(--font-handwritten)', fontSize: '20px', fontWeight: 800, letterSpacing: '-0.4px', marginBottom: '2px', color: 'var(--text-dark)' }}>
              나의 취향 프로필
            </h3>
            <p style={{ fontFamily: 'var(--font-handwritten)', fontSize: '14px', color: 'var(--text-gray)', marginBottom: '12px' }}>
              선택할수록 그래프가 변합니다.
            </p>

            <div style={{
              background: 'white',
              padding: '10px 10px 32px',
              borderRadius: '3px',
              boxShadow: '0 6px 24px rgba(0,0,0,0.10), 0 2px 6px rgba(0,0,0,0.06)',
              transform: 'rotate(-0.8deg)',
              margin: '0 0 4px',
            }}>
              <RadarChartViz
                data={radarData}
                axes={groupOrder.map(g => g.label).slice(0, 5)}
              />
              <p style={{
                fontFamily: 'var(--font-handwritten)',
                fontSize: '15px',
                fontWeight: 700,
                color: 'var(--text-gray)',
                textAlign: 'center',
                marginTop: '8px',
              }}>나의 맛 조각들</p>
            </div>

            <div style={{ display: 'flex', gap: '10px', marginTop: '20px' }}>
              <Button variant="outline" onClick={onReset} style={{ flex: 1, padding: '12px', fontSize: '14px' }}>
                초기화
              </Button>
              <motion.button
                onClick={onNext}
                whileHover={{ scale: 1.03, y: -2 }}
                whileTap={{ scale: 0.97 }}
                style={{
                  flex: 2,
                  background: 'var(--btn-dark)', color: 'white', border: 'none',
                  borderRadius: '999px', padding: '12px 20px',
                  fontSize: '15px', fontWeight: 700, cursor: 'pointer',
                  fontFamily: 'inherit', boxShadow: '0 4px 16px rgba(0,0,0,0.20)',
                }}
              >
                다음 단계 →
              </motion.button>
            </div>
          </div>
        </div>
      </div>
    </motion.div>
  );
};

export default TasteStep;
