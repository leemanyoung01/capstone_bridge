import React, { useEffect, useState } from 'react';
import { Info } from 'lucide-react';

/**
 * 추천 품질 평가 카드.
 * /api/evaluation 호출 → 200 OK면 표시, 404/실패면 조용히 숨김.
 *
 * pseudo-label 기반 시연용 지표라는 점을 안내 문구로 명시한다.
 */
const EvaluationCard = () => {
  const [data, setData] = useState(null);
  const [hidden, setHidden] = useState(false);

  useEffect(() => {
    let alive = true;
    fetch('/api/evaluation')
      .then((res) => {
        if (!res.ok) throw new Error('no eval');
        return res.json();
      })
      .then((d) => {
        if (!alive) return;
        if (d && d.ok && d.metrics) {
          setData(d);
        } else {
          setHidden(true);
        }
      })
      .catch(() => alive && setHidden(true));
    return () => { alive = false; };
  }, []);

  if (hidden || !data) return null;

  const { metrics = {}, k = 5, queries = 0, label_type = 'pseudo-label', note = '' } = data;
  const items = [
    { key: 'precision@k', label: `Precision@${k}` },
    { key: 'recall@k',    label: `Recall@${k}` },
    { key: 'f1@k',        label: `F1@${k}` },
    { key: 'ndcg@k',      label: `NDCG@${k}` },
  ];

  const fmt = (v) => (typeof v === 'number' ? v.toFixed(3) : '—');

  return (
    <div style={{
      backgroundColor: 'var(--white)',
      borderRadius: 'var(--radius-lg)',
      padding: '18px',
      boxShadow: 'var(--shadow-sm)',
      border: '1px dashed var(--border-color)',
      marginBottom: '20px',
    }}>
      <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'flex-start', gap: '8px', marginBottom: '12px' }}>
        <div>
          <h3 style={{ fontSize: '15px', fontWeight: 800, marginBottom: '2px' }}>
            추천 품질 평가
          </h3>
          <p style={{ fontSize: '11px', color: 'var(--text-gray)' }}>
            평가 쿼리 수: {queries}개 · k={k}
          </p>
        </div>
        <span style={{
          fontSize: '10px', fontWeight: 700,
          background: '#FEF3C7', color: '#92400E',
          padding: '3px 8px', borderRadius: '999px',
          whiteSpace: 'nowrap',
        }}>
          {label_type === 'pseudo-label' ? '시연용 pseudo-label' : label_type}
        </span>
      </div>

      <div style={{
        display: 'grid',
        gridTemplateColumns: 'repeat(4, 1fr)',
        gap: '8px',
        marginBottom: '10px',
      }}>
        {items.map(({ key, label }) => (
          <div key={key} style={{
            background: 'var(--bg-color)',
            borderRadius: 'var(--radius-md)',
            padding: '10px 8px',
            textAlign: 'center',
          }}>
            <div style={{ fontSize: '10px', color: 'var(--text-gray)', fontWeight: 600 }}>
              {label}
            </div>
            <div style={{ fontSize: '18px', fontWeight: 800, color: 'var(--text-dark)', marginTop: '2px' }}>
              {fmt(metrics[key])}
            </div>
          </div>
        ))}
      </div>

      <div style={{
        display: 'flex', alignItems: 'flex-start', gap: '6px',
        fontSize: '11px', color: 'var(--text-gray)', lineHeight: 1.5,
        background: '#F9FAFB', padding: '8px 10px', borderRadius: '6px',
      }}>
        <Info size={12} style={{ flexShrink: 0, marginTop: '2px' }} />
        <span>
          {note || '모델 벡터 기반 시연용 평가입니다. 진짜 성능 평가는 사람이 직접 라벨링한 relevance 데이터가 필요합니다.'}
        </span>
      </div>
    </div>
  );
};

export default EvaluationCard;
