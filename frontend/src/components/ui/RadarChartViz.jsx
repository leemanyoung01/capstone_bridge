import React from 'react';
import { Radar, RadarChart, PolarGrid, PolarAngleAxis, PolarRadiusAxis, ResponsiveContainer } from 'recharts';

// 긴 축 라벨을 두 줄로 분할해 가로 폭을 줄임 (차트 경계에서 잘리는 것 방지)
const toLines = (label) => {
  const text = String(label ?? '');
  // 구분자(·, 공백, /)가 있으면 첫 단어만 1줄, 나머지는 붙여서 2줄
  // 예: '고기 해산물 양' → ['고기', '해산물양'],  '육수·마라향' → ['육수', '마라향']
  const parts = text.split(/[·\s/]+/).filter(Boolean);
  if (parts.length >= 2) {
    return [parts[0], parts.slice(1).join('')];
  }
  // 구분자 없는 단일 토큰은 6자 이상일 때만 글자 수 절반으로 강제 분할
  if (text.length >= 6) {
    const cut = Math.ceil(text.length / 2);
    return [text.slice(0, cut), text.slice(cut)];
  }
  return [text];
};

// PolarAngleAxis 커스텀 틱 — 긴 라벨 자동 줄바꿈 + 세로 중앙 정렬
const AngleTick = ({ x, y, payload, textAnchor, fontSize }) => {
  const lines = toLines(payload?.value);
  const lineHeight = 1.15; // em
  const startDy = -((lines.length - 1) / 2) * lineHeight + 0.32;
  return (
    <text
      x={x}
      y={y}
      textAnchor={textAnchor}
      fill="var(--ink-2)"
      fontSize={fontSize}
      fontWeight={700}
    >
      {lines.map((line, i) => (
        <tspan key={i} x={x} dy={`${i === 0 ? startDy : lineHeight}em`}>
          {line}
        </tspan>
      ))}
    </text>
  );
};

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

  // 라벨이 2줄로 짧아진 만큼 여백을 줄여 그래프를 크게 (잘리지 않을 정도만 확보)
  const margin =
    axes.length <= 3
      ? { top: 26, right: 42, bottom: 26, left: 42 }
      : axes.length <= 5
      ? { top: 24, right: 40, bottom: 24, left: 40 }
      : { top: 22, right: 38, bottom: 22, left: 38 };

  return (
    <div style={{ width: '100%', height: 'var(--radar-h, 340px)', margin: '0 auto' }}>
      <ResponsiveContainer width="100%" height="100%">
        <RadarChart cx="50%" cy="50%" outerRadius="80%" data={chartData} margin={margin}>
          {/* 보조 그리드 링 — 가볍게 보이도록 */}
          <PolarGrid
            stroke="var(--line-2)"
            strokeDasharray="3 3"
            strokeWidth={1}
          />
          <PolarAngleAxis
            dataKey="subject"
            tick={<AngleTick fontSize={fontSize} />}
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
