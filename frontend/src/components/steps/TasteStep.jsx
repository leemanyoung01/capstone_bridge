import React from 'react';
import { motion } from 'framer-motion';
import RadarChartViz from '../ui/RadarChartViz';
import Button from '../ui/Button';
import { axisLabel } from '../../utils/labels';

const TasteStep = ({ keyword, config, scores, onChipClick, onScoreChange, onNext, onReset }) => {
  const { axes_config, groups = {}, axes = [] } = config;
  
  // Create groups in order
  const groupPriority = ['공통', '맛', '식감', '기타', '메타'];
  const groupOrder = [];
  const used = new Set();
  
  groupPriority.forEach(label => {
    if (groups[label]) { groupOrder.push({ label, axes: groups[label] }); used.add(label); }
  });
  Object.keys(groups).forEach(label => {
    if (!used.has(label)) groupOrder.push({ label, axes: groups[label] });
  });

  return (
    <motion.div initial={{ opacity: 0, x: 20 }} animate={{ opacity: 1, x: 0 }} style={{ padding: '20px' }}>
      <div style={{ textAlign: 'center', marginBottom: '30px' }}>
        <h2 style={{ fontSize: '24px', fontWeight: '800' }}>{keyword} 맛 취향 탐색</h2>
        <p style={{ color: 'var(--text-gray)', fontSize: '14px', marginTop: '4px' }}>끌리는 단어를 자유롭게 선택해주세요 (최대 10점)</p>
      </div>

      <div style={{ display: 'grid', gridTemplateColumns: 'minmax(0, 1fr) 300px', gap: '30px', alignItems: 'start' }}>
        {/* Left: Chip Selection */}
        <div style={{ display: 'flex', flexDirection: 'column', gap: '24px' }}>
          {groupOrder.map(group => (
            <div key={group.label} style={{
              background: 'var(--white)', borderRadius: 'var(--radius-lg)', padding: '20px',
              border: '1px solid var(--border-color)', boxShadow: 'var(--shadow-sm)'
            }}>
              <h3 style={{ fontSize: '15px', fontWeight: '700', marginBottom: '16px', color: 'var(--text-dark)' }}>
                {group.label}
              </h3>
              <div style={{ display: 'flex', flexWrap: 'wrap', gap: '8px' }}>
                {group.axes.map(axis => {
                  const score = scores[axis];
                  // Consider selected if score is not exactly 0 (allows empty string while typing)
                  const isSelected = score !== 0;
                  
                  return (
                    <motion.div
                      key={axis}
                      layout
                      transition={{ 
                        layout: { duration: 0.2, ease: "easeOut" } 
                      }}
                      style={{ 
                        display: 'flex', alignItems: 'center',
                        padding: isSelected ? '6px 6px 6px 16px' : '10px 16px',
                        borderRadius: '999px',
                        fontSize: '14px',
                        fontWeight: '600',
                        backgroundColor: isSelected ? 'var(--primary)' : 'var(--bg-color)',
                        color: isSelected ? 'white' : 'var(--text-dark)',
                        border: `1px solid ${isSelected ? 'var(--primary)' : 'var(--border-color)'}`,
                        boxShadow: isSelected ? '0 4px 12px rgba(255, 126, 103, 0.2)' : 'none',
                        cursor: 'pointer',
                        whiteSpace: 'nowrap'
                      }}
                      onClick={() => onChipClick(axis)}
                    >
                      <span>{axisLabel(axis, config)}</span>
                      {isSelected && (
                        <div 
                          style={{ 
                            display: 'flex', alignItems: 'center', gap: '6px', 
                            background: 'rgba(255,255,255,0.2)', padding: '4px 6px', 
                            borderRadius: '999px', marginLeft: '12px' 
                          }}
                          onClick={(e) => e.stopPropagation()}
                        >
                          <button 
                            onClick={() => onScoreChange(axis, (parseInt(score) || 0) - 1)}
                            disabled={(parseInt(score) || 0) <= 1}
                            style={{ 
                              width: '24px', height: '24px', display: 'flex', alignItems: 'center', 
                              justifyContent: 'center', borderRadius: '50%', background: 'white', 
                              color: 'var(--primary)', fontSize: '16px', fontWeight: '800',
                              opacity: (parseInt(score) || 0) <= 1 ? 0.5 : 1, cursor: (parseInt(score) || 0) <= 1 ? 'not-allowed' : 'pointer'
                            }}
                          >
                            -
                          </button>
                          <input 
                            type="number"
                            value={score}
                            autoFocus={score === 1} // Auto focus when first selected
                            onChange={(e) => onScoreChange(axis, e.target.value)}
                            onBlur={(e) => {
                              const val = parseInt(e.target.value);
                              if (isNaN(val) || val < 1) onScoreChange(axis, 1);
                              else if (val > 10) onScoreChange(axis, 10);
                            }}
                            style={{ 
                              width: '32px', textAlign: 'center', fontSize: '14px', fontWeight: '800',
                              background: 'transparent', border: 'none', color: 'white',
                              appearance: 'none', MozAppearance: 'textfield', padding: 0,
                              outline: 'none'
                            }}
                          />
                          <button 
                            onClick={() => onScoreChange(axis, (parseInt(score) || 0) + 1)}
                            disabled={(parseInt(score) || 0) >= 10}
                            style={{ 
                              width: '24px', height: '24px', display: 'flex', alignItems: 'center', 
                              justifyContent: 'center', borderRadius: '50%', background: 'white', 
                              color: 'var(--primary)', fontSize: '16px', fontWeight: '800',
                              opacity: (parseInt(score) || 0) >= 10 ? 0.5 : 1, cursor: (parseInt(score) || 0) >= 10 ? 'not-allowed' : 'pointer'
                            }}
                          >
                            +
                          </button>
                        </div>
                      )}
                    </motion.div>
                  );
                })}
              </div>
            </div>
          ))}
        </div>

        {/* Right: Radar Chart Dashboard */}
        <div style={{ 
          background: 'var(--white)', borderRadius: 'var(--radius-lg)', padding: '24px', position: 'sticky', top: '24px',
          border: '1px solid var(--border-color)', boxShadow: 'var(--shadow-md)'
        }}>
          <h3 style={{ fontSize: '18px', fontWeight: '800', marginBottom: '4px' }}>나의 취향 프로필</h3>
          <p style={{ fontSize: '12px', color: 'var(--text-gray)', marginBottom: '20px' }}>선택할수록 그래프가 변합니다.</p>
          
          <RadarChartViz 
            data={groupOrder.reduce((acc, group) => {
              acc[group.label] = group.axes.reduce((sum, ax) => sum + (scores[ax] || 0), 0);
              return acc;
            }, {})} 
            axes={groupOrder.map(g => g.label).slice(0, 5)} 
          />
          
          <div style={{ display: 'flex', gap: '10px', marginTop: '24px' }}>
            <Button variant="outline" onClick={onReset} style={{ flex: 1, border: '1px solid var(--border-color)' }}>초기화</Button>
            <Button variant="primary" onClick={onNext} style={{ flex: 2, border: '1px solid #E56A54' }}>다음 단계 →</Button>
          </div>
        </div>
      </div>
    </motion.div>
  );
};

export default TasteStep;
