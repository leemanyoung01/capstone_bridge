import React from 'react';
import { motion } from 'framer-motion';
import RadarChartViz from '../ui/RadarChartViz';
import Button from '../ui/Button';

const TasteStep = ({ keyword, config, scores, onChipClick, onNext, onReset }) => {
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
        <p style={{ color: 'var(--text-gray)', fontSize: '14px', marginTop: '4px' }}>끌리는 단어를 자유롭게 선택해주세요</p>
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
                  const clickCount = scores[axis] || 0;
                  const isSelected = clickCount > 0;
                  return (
                    <motion.button
                      key={axis}
                      whileHover={{ scale: 1.05 }}
                      whileTap={{ scale: 0.95 }}
                      onClick={() => onChipClick(axis)}
                      style={{
                        padding: '10px 16px',
                        borderRadius: '999px',
                        fontSize: '14px',
                        fontWeight: '600',
                        backgroundColor: isSelected ? 'var(--primary)' : 'var(--bg-color)',
                        color: isSelected ? 'white' : 'var(--text-dark)',
                        border: `1px solid ${isSelected ? 'var(--primary)' : 'var(--border-color)'}`,
                        transition: 'background-color 0.2s',
                        display: 'flex', alignItems: 'center', gap: '6px'
                      }}
                    >
                      {axis}
                      {clickCount > 0 && (
                        <span style={{ 
                          fontSize: '11px', background: 'rgba(255,255,255,0.2)', padding: '2px 6px', borderRadius: '10px'
                        }}>+{clickCount}</span>
                      )}
                    </motion.button>
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
