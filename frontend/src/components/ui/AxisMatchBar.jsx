import React from 'react';

/**
 * 축별 취향 매칭 막대 그래프.
 * props:
 *   - axes: [{ name, value, contribution? }]   (value는 -1~1 범위)
 *   - max:  표시 개수 (default 5)
 */
const AxisMatchBar = ({ axes = [], max = 5, showContribution = false }) => {
  if (!axes || axes.length === 0) return null;
  const items = axes.slice(0, max);

  return (
    <div style={{ display: 'flex', flexDirection: 'column', gap: '6px', marginTop: '4px' }}>
      {items.map((ax, i) => {
        const val = Number(ax.value || 0);
        const pct = Math.min(100, Math.max(0, Math.abs(val) * 100));
        const positive = val >= 0;
        const color = positive ? 'var(--primary, #FF7E67)' : '#9CA3AF';
        return (
          <div key={ax.name || i} style={{ display: 'grid', gridTemplateColumns: '70px 1fr 50px', gap: '8px', alignItems: 'center' }}>
            <span style={{ fontSize: '12px', fontWeight: 600, color: 'var(--text-dark, #1F2937)' }}>
              {ax.name}
            </span>
            <div style={{ background: '#F3F4F6', borderRadius: '999px', height: '8px', overflow: 'hidden' }}>
              <div style={{
                width: `${pct}%`,
                height: '100%',
                background: color,
                borderRadius: '999px',
                transition: 'width 0.3s'
              }} />
            </div>
            <span style={{ fontSize: '11px', color: 'var(--text-gray, #6B7280)', textAlign: 'right' }}>
              {showContribution && ax.contribution != null
                ? `${Math.round(ax.contribution * 100)}%`
                : `${val >= 0 ? '+' : ''}${val.toFixed(2)}`}
            </span>
          </div>
        );
      })}
    </div>
  );
};

export default AxisMatchBar;
