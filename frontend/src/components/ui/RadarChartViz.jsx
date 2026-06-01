import React from 'react';
import { Radar, RadarChart, PolarGrid, PolarAngleAxis, PolarRadiusAxis, ResponsiveContainer } from 'recharts';

const RadarChartViz = ({ data, axes, labelMap = {}, minDomainMax = 1.0 }) => {
  if (!axes || axes.length === 0) return (
    <div style={{ textAlign: 'center', padding: '20px', color: 'var(--ink-3)' }}>데이터가 없습니다</div>
  );

  const chartData = axes.map(axis => ({
    subject: labelMap[axis] || axis,
    A: data[axis] || 0,
    fullMark: 1.0,
  }));

  const maxVal = Math.max(...chartData.map(d => d.A), 0.1);
  const domainMax = Math.max(maxVal * 1.25, minDomainMax);

  // 축 개수가 많을수록 레이블 폰트 줄임
  const fontSize = axes.length >= 7 ? 11 : axes.length >= 5 ? 12 : 13;

  // 축 개수가 적을수록 마진을 줄여 차트가 더 넓게 차지하도록
  const margin =
    axes.length <= 3
      ? { top: 20, right: 36, bottom: 20, left: 36 }
      : axes.length <= 5
      ? { top: 16, right: 32, bottom: 16, left: 32 }
      : { top: 12, right: 28, bottom: 12, left: 28 };

  return (
    <div style={{ width: '100%', height: 'var(--radar-h, 340px)', margin: '0 auto' }}>
      <ResponsiveContainer width="100%" height="100%">
        <RadarChart cx="50%" cy="50%" outerRadius="72%" data={chartData} margin={margin}>
          {/* 보조 그리드 링 — 가볍게 보이도록 */}
          <PolarGrid
            stroke="var(--line-2)"
            strokeDasharray="3 3"
            strokeWidth={1}
          />
          <PolarAngleAxis
            dataKey="subject"
            tick={{ fill: 'var(--ink-2)', fontSize, fontWeight: 700 }}
          />
          <PolarRadiusAxis
            angle={90}
            domain={[0, domainMax]}
            tick={false}
            axisLine={false}
          />
          <Radar
            name="취향 프로필"
            dataKey="A"
            stroke="var(--accent)"
            strokeWidth={2}
            fill="var(--accent)"
            fillOpacity={0.22}
            dot={{ r: 4, fill: 'var(--accent)', strokeWidth: 2, stroke: 'var(--card)' }}
            isAnimationActive={true}
            animationDuration={800}
            animationBegin={100}
            animationEasing="ease-out"
          />
        </RadarChart>
      </ResponsiveContainer>
    </div>
  );
};

export default RadarChartViz;
