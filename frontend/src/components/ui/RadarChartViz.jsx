import React from 'react';
import { Radar, RadarChart, PolarGrid, PolarAngleAxis, ResponsiveContainer } from 'recharts';

const RadarChartViz = ({ data, axes }) => {
  // data format expected: { "단맛": 5, "매운맛": 2, ... }
  // We need to convert it to array format for Recharts
  
  if (!axes || axes.length === 0) return <div style={{ textAlign: 'center', padding: '20px', color: 'var(--text-light)' }}>데이터가 없습니다</div>;

  const chartData = axes.map(axis => ({
    subject: axis,
    A: data[axis] || 0,
    fullMark: 5 // max score
  }));

  return (
    <div style={{ width: '100%', height: '250px' }}>
      <ResponsiveContainer width="100%" height="100%">
        <RadarChart cx="50%" cy="50%" outerRadius="70%" data={chartData}>
          <PolarGrid stroke="var(--border-color)" />
          <PolarAngleAxis dataKey="subject" tick={{ fill: 'var(--text-gray)', fontSize: 12, fontWeight: 500 }} />
          <Radar
            name="나의 취향"
            dataKey="A"
            stroke="var(--primary)"
            strokeWidth={2}
            fill="var(--primary)"
            fillOpacity={0.3}
            animationDuration={500}
          />
        </RadarChart>
      </ResponsiveContainer>
    </div>
  );
};

export default RadarChartViz;
