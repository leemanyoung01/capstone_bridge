import React, { useEffect, useState } from 'react';

/**
 * 성능표 대시보드 (시연 전용, #eval 해시로 진입).
 * /api/evaluation(evaluate_ranking.py 결과)을 받아 4-way ablation을 시각화한다.
 *   text_only(lex) → +CLIP(이미지) → +BERT(의미) → full(전체 late fusion)
 * 평소 추천 UI엔 노출하지 않고, 교수님 시연 때만 보여준다.
 */
const LABELS = {
  text_only: 'lex만',
  'text+image': '+CLIP',
  'text+sem': '+BERT',
  full: '+CLIP+BERT',
};
const SUBLABEL = {
  text_only: '키워드 매칭만',
  'text+image': '+ 이미지 융합',
  'text+sem': '+ 의미 매칭(BERT)',
  full: '전체 late fusion',
};
const COLORS = {
  text_only: '#9CA3AF',
  'text+image': '#60A5FA',
  'text+sem': '#F59E0B',
  full: '#34D399',
};
const METRICS = [
  { key: 'ndcg', label: 'NDCG@5', desc: '관련 식당이 상위에 배치됐는지(순위 품질)' },
  { key: 'mrr', label: 'MRR', desc: '첫 정답이 얼마나 위에 있는지' },
  { key: 'recall', label: 'Recall@5', desc: '정답 식당을 top5에 얼마나 담았는지' },
  { key: 'precision', label: 'Precision@5', desc: 'top5 중 정답 비율' },
];

const mean = (xs) => (xs.length ? xs.reduce((a, b) => a + b, 0) / xs.length : 0);
const fmt = (v) => (typeof v === 'number' ? v.toFixed(3) : '—');
const pct = (v) => `${(v * 100).toFixed(1)}%`;

const EvalDashboard = () => {
  const [data, setData] = useState(null);
  const [err, setErr] = useState(null);

  useEffect(() => {
    fetch('/api/evaluation')
      .then((r) => r.json())
      .then((d) => {
        if (d && d.reports && d.reports.length) setData(d);
        else setErr(d?.message || '평가 결과가 없습니다. evaluate_ranking.py를 먼저 실행하세요.');
      })
      .catch(() => setErr('백엔드(/api/evaluation) 응답 실패 — app.py가 켜져 있나요?'));
  }, []);

  if (err) {
    return (
      <div style={wrap}>
        <div style={{ maxWidth: 720, margin: '80px auto', textAlign: 'center', color: '#6B7280' }}>
          <h2 style={{ fontWeight: 800, marginBottom: 12 }}>성능표를 불러올 수 없어요</h2>
          <p>{err}</p>
        </div>
      </div>
    );
  }
  if (!data) return <div style={wrap}><div style={{ padding: 80, textAlign: 'center', color: '#9CA3AF' }}>불러오는 중…</div></div>;

  const variants = data.ablation_variants || ['text_only', 'text+image', 'text+sem', 'full'];
  const reports = data.reports || [];

  // 매크로 평균
  const macro = {};
  variants.forEach((v) => {
    macro[v] = {};
    METRICS.forEach((m) => {
      macro[v][m.key] = mean(reports.map((r) => r.summary?.[v]?.[m.key]).filter((x) => typeof x === 'number'));
    });
  });
  const base = variants[0];
  const maxNdcg = Math.max(...variants.map((v) => macro[v].ndcg), 0.001);

  // 키워드별 1등 variant (NDCG 기준)
  const winners = {};
  variants.forEach((v) => { winners[v] = 0; });
  const perKw = reports.map((r) => {
    const vals = {};
    variants.forEach((v) => { vals[v] = r.summary?.[v]?.ndcg ?? 0; });
    const best = variants.reduce((a, b) => (vals[b] > vals[a] ? b : a), variants[0]);
    winners[best] += 1;
    return { keyword: r.keyword, vals, best };
  });
  const multimodalWins = reports.length - winners[base];

  return (
    <div style={wrap}>
      <div style={{ maxWidth: 1080, margin: '0 auto', padding: '32px 20px 80px' }}>
        {/* 헤더 */}
        <div style={{ marginBottom: 24 }}>
          <h1 style={{ fontSize: 28, fontWeight: 900, marginBottom: 6 }}>
            추천 성능 평가 <span style={{ color: '#9CA3AF', fontSize: 15, fontWeight: 600 }}>4-way Ablation</span>
          </h1>
          <p style={{ color: '#6B7280', fontSize: 14 }}>
            키워드 {reports.length}개 · semantic_weight {data.semantic_weight} · 지표 k=5 ·
            정답: 네이버 voted_keywords (객관 라벨)
          </p>
        </div>

        {/* 헤드라인 카드 */}
        <div style={{ display: 'grid', gridTemplateColumns: `repeat(${variants.length}, 1fr)`, gap: 12, marginBottom: 28 }}>
          {variants.map((v) => {
            const d = macro[v].ndcg - macro[base].ndcg;
            const up = d > 0.0005;
            return (
              <div key={v} style={{ ...card, borderTop: `4px solid ${COLORS[v]}` }}>
                <div style={{ fontSize: 13, fontWeight: 800 }}>{LABELS[v]}</div>
                <div style={{ fontSize: 11, color: '#9CA3AF', marginBottom: 8 }}>{SUBLABEL[v]}</div>
                <div style={{ fontSize: 30, fontWeight: 900 }}>{fmt(macro[v].ndcg)}</div>
                <div style={{ fontSize: 12, fontWeight: 700, marginTop: 2, color: v === base ? '#9CA3AF' : up ? '#059669' : '#DC2626' }}>
                  {v === base ? 'baseline' : `${up ? '▲' : '▼'} ${d >= 0 ? '+' : ''}${(d * 100).toFixed(1)}%p`}
                </div>
              </div>
            );
          })}
        </div>

        {/* NDCG 막대그래프 */}
        <Section title="NDCG@5 비교 (높을수록 좋음)">
          <div style={{ display: 'flex', flexDirection: 'column', gap: 10 }}>
            {variants.map((v) => (
              <div key={v} style={{ display: 'flex', alignItems: 'center', gap: 12 }}>
                <div style={{ width: 110, fontSize: 13, fontWeight: 700, textAlign: 'right' }}>{LABELS[v]}</div>
                <div style={{ flex: 1, background: '#F3F4F6', borderRadius: 6, height: 26, position: 'relative' }}>
                  <div style={{ width: `${(macro[v].ndcg / maxNdcg) * 100}%`, background: COLORS[v], height: '100%', borderRadius: 6, transition: 'width .4s' }} />
                  <span style={{ position: 'absolute', right: 8, top: 4, fontSize: 12, fontWeight: 800, color: '#374151' }}>{fmt(macro[v].ndcg)}</span>
                </div>
              </div>
            ))}
          </div>
        </Section>

        {/* 매크로 지표 표 */}
        <Section title="전체 평균 지표">
          <div style={{ overflowX: 'auto' }}>
            <table style={table}>
              <thead>
                <tr>
                  <th style={th}>variant</th>
                  {METRICS.map((m) => <th key={m.key} style={th} title={m.desc}>{m.label}</th>)}
                </tr>
              </thead>
              <tbody>
                {variants.map((v) => (
                  <tr key={v}>
                    <td style={{ ...td, fontWeight: 800 }}>
                      <span style={{ display: 'inline-block', width: 10, height: 10, borderRadius: 2, background: COLORS[v], marginRight: 6 }} />
                      {LABELS[v]}
                    </td>
                    {METRICS.map((m) => {
                      const val = macro[v][m.key];
                      const d = val - macro[base][m.key];
                      return (
                        <td key={m.key} style={td}>
                          {fmt(val)}
                          {v !== base && (
                            <span style={{ fontSize: 10, marginLeft: 4, color: d > 0.0005 ? '#059669' : d < -0.0005 ? '#DC2626' : '#9CA3AF' }}>
                              {d >= 0 ? '+' : ''}{d.toFixed(3)}
                            </span>
                          )}
                        </td>
                      );
                    })}
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </Section>

        {/* 키워드별 1등 분포 */}
        <Section title={`키워드별 최고 성능 신호 (멀티모달이 ${multimodalWins}/${reports.length} 키워드에서 lex 이김)`}>
          <div style={{ display: 'flex', gap: 12, flexWrap: 'wrap' }}>
            {variants.map((v) => (
              <div key={v} style={{ ...card, flex: '1 1 140px', textAlign: 'center' }}>
                <div style={{ fontSize: 12, fontWeight: 700, color: COLORS[v] }}>{LABELS[v]} 1등</div>
                <div style={{ fontSize: 26, fontWeight: 900 }}>{winners[v]}<span style={{ fontSize: 13, color: '#9CA3AF' }}> 개</span></div>
              </div>
            ))}
          </div>
        </Section>

        {/* 키워드별 NDCG 표 */}
        <Section title="키워드별 NDCG (최고값 강조)">
          <div style={{ overflowX: 'auto' }}>
            <table style={table}>
              <thead>
                <tr>
                  <th style={th}>키워드</th>
                  {variants.map((v) => <th key={v} style={th}>{LABELS[v]}</th>)}
                </tr>
              </thead>
              <tbody>
                {perKw.map((row) => (
                  <tr key={row.keyword}>
                    <td style={{ ...td, fontWeight: 700 }}>{row.keyword}</td>
                    {variants.map((v) => (
                      <td key={v} style={{
                        ...td,
                        fontWeight: row.best === v ? 900 : 400,
                        background: row.best === v ? '#ECFDF5' : 'transparent',
                        color: row.best === v ? '#065F46' : '#374151',
                      }}>
                        {fmt(row.vals[v])}
                      </td>
                    ))}
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </Section>

        {/* 해석 */}
        <div style={{ ...card, background: '#FFFBEB', border: '1px solid #FDE68A', fontSize: 13, lineHeight: 1.7, color: '#78350F' }}>
          <b>해석</b><br />
          • <b>+BERT</b>가 평균 NDCG·Recall 최고 — 의미 매칭이 lex가 놓친 표현을 보강.<br />
          • <b>+CLIP</b>은 시각적 음식(치킨·아구찜·마라탕 등)에서 가장 자주 1등.<br />
          • 신호별 강점이 음식군마다 달라 멀티모달 융합의 가치를 보여줌.<br />
          • <b>정량 평가는 객관 정답(voted_keywords)이 있는 공유 축 한정</b>이며, 우리 고유 fine-grained 축(곱고소함·와사비간장조화 등)은 추천 화면의 BERT 근거 문장으로 정성 입증.
        </div>
      </div>
    </div>
  );
};

const Section = ({ title, children }) => (
  <div style={{ marginBottom: 28 }}>
    <h3 style={{ fontSize: 15, fontWeight: 800, marginBottom: 12 }}>{title}</h3>
    {children}
  </div>
);

const wrap = { minHeight: '100vh', background: '#FAF9F6' };
const card = { background: '#fff', borderRadius: 12, padding: 16, boxShadow: '0 1px 3px rgba(0,0,0,0.06)' };
const table = { width: '100%', borderCollapse: 'collapse', fontSize: 13 };
const th = { textAlign: 'center', padding: '8px 10px', borderBottom: '2px solid #E5E7EB', color: '#6B7280', fontWeight: 700, whiteSpace: 'nowrap' };
const td = { textAlign: 'center', padding: '7px 10px', borderBottom: '1px solid #F3F4F6', whiteSpace: 'nowrap' };

export default EvalDashboard;
