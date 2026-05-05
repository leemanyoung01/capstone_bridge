import React from 'react';
import { Radar, RadarChart, PolarGrid, PolarAngleAxis, PolarRadiusAxis, ResponsiveContainer } from 'recharts';

const RadarChartViz = ({ data, axes }) => {
  // data format expected: { "단맛": 5, "매운맛": 2, ... }
  
  if (!axes || axes.length === 0) return <div style={{ textAlign: 'center', padding: '20px', color: 'var(--text-light)' }}>데이터가 없습니다</div>;

  const chartData = axes.map(axis => ({
    subject: axis,
    A: data[axis] || 0,
    fullMark: 1.0 // adjusted from 5
  }));

  // Determine domain based on max value to make it look "fuller"
  const maxVal = Math.max(...chartData.map(d => d.A), 0.1);
  const domainMax = maxVal * 1.1; // Tight domain for maximum spread

  return (
    <div style={{ width: '100%', height: '320px', margin: '0 auto' }}>
      <ResponsiveContainer width="100%" height="100%">
        <RadarChart cx="50%" cy="50%" outerRadius="90%" data={chartData}>
          <PolarGrid stroke="#E5E7EB" />
          <PolarAngleAxis 
            dataKey="subject" 
            tick={{ fill: 'var(--text-dark)', fontSize: 12, fontWeight: 800 }} 
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
            strokeWidth={3}
            fill="var(--primary)"
            fillOpacity={0.4}
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
