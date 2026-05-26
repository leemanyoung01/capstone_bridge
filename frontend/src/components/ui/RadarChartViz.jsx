import React from 'react';
import { Radar, RadarChart, PolarGrid, PolarAngleAxis, PolarRadiusAxis, ResponsiveContainer } from 'recharts';

const RadarChartViz = ({ data, axes, labelMap = {}, minDomainMax = 1.0 }) => {
  // data format expected: { "단맛": 5, "매운맛": 2, ... }
  
  if (!axes || axes.length === 0) return <div style={{ textAlign: 'center', padding: '20px', color: 'var(--text-light)' }}>데이터가 없습니다</div>;

  const chartData = axes.map(axis => ({
    subject: labelMap[axis] || axis,
    A: data[axis] || 0,
    fullMark: 1.0 // adjusted from 5
  }));

  // Determine domain based on max value to make it look "fuller"
  const maxVal = Math.max(...chartData.map(d => d.A), 0.1);
  // 데이터가 거의 없을 때 차트가 너무 작아 보이지 않도록 최솟값(minDomainMax)을 보장하면서,
  // 항상 25% 정도의 여백을 두어 시각적으로 안정감을 줌
  const domainMax = Math.max(maxVal * 1.25, minDomainMax);

  return (
    <div style={{ width: '100%', height: '340px', margin: '0 auto' }}>
      <ResponsiveContainer width="100%" height="100%">
        <RadarChart cx="50%" cy="50%" outerRadius="55%" data={chartData} margin={{ top: 15, right: 35, bottom: 30, left: 35 }}>
          <PolarGrid stroke="#E5E7EB" />
          <PolarAngleAxis
            dataKey="subject"
            tick={{ fill: 'var(--text-dark)', fontSize: 14, fontWeight: 800 }}
          />
          <PolarRadiusAxis 
            angle={90} 
            domain={[0, domainMax]} 
            tick={false} 
            axisLine={false} 
          />
          <Radar
            name="식당 특성"
            dataKey="A"
            stroke="var(--primary)"
            strokeWidth={4}
            fill="var(--primary)"
            fillOpacity={0.35}
            dot={{ r: 4, fill: 'var(--primary)', strokeWidth: 2, stroke: '#fff' }}
            isAnimationActive={true}
            animationDuration={1000}
            animationBegin={200}
            animationEasing="ease-out"
          />
        </RadarChart>
      </ResponsiveContainer>
    </div>
  );
};

export default RadarChartViz;
