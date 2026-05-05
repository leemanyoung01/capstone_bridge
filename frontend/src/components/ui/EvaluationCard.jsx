import React, { useEffect, useState } from 'react';
import { Info, ChevronDown, ChevronUp } from 'lucide-react';

/**
 * 추천 목록 품질 평가 카드.
 * /api/evaluation 호출 → 200 OK면 표시, 404/실패면 조용히 숨김.
 *
 * Precision/Recall/F1/NDCG는 추천 리스트 전체에 대한 ranking 지표이며
 * 개별 식당 카드의 매칭% 와는 무관함을 안내한다.
 */
const METRIC_INFO = [
  {
    key: 'precision_at_k', alt: 'precision@k', label: 'Precision',
    desc: '상위 K개 추천 중 관련 있다고 판단된 식당의 비율',
  },
  {
    key: 'recall_at_k', alt: 'recall@k', label: 'Recall',
    desc: '관련 식당 중 상위 K개 안에 포함된 비율',
  },
  {
    key: 'f1_at_k', alt: 'f1@k', label: 'F1',
    desc: 'Precision과 Recall의 조화 평균 (균형 점수)',
  },
  {
    key: 'ndcg_at_k', alt: 'ndcg@k', label: 'NDCG',
    desc: '관련 식당이 더 높은 순위에 배치되었는지 평가하는 순위 품질 점수',
  },
];

const EvaluationCard = () => {
  const [data, setData] = useState(null);
  const [missing, setMissing] = useState(false);
  const [expanded, setExpanded] = useState(false);

  useEffect(() => {
    let alive = true;
    fetch('/api/evaluation')
      .then((res) => res.json().then((j) => ({ ok: res.ok, body: j })))
      .then(({ ok, body }) => {
        if (!alive) return;
        if (ok && body && body.ok && body.metrics) {
          setData(body);
        } else {
          setMissing(true);
        }
      })
      .catch(() => alive && setMissing(true));
    return () => { alive = false; };
  }, []);

  if (missing) {
    return (
      <div style={{
        backgroundColor: 'var(--white)',
        borderRadius: 'var(--radius-lg)',
        padding: '16px',
        boxShadow: 'var(--shadow-sm)',
        border: '1px dashed var(--border-color)',
        marginTop: '16px',
        marginBottom: '8px',
        textAlign: 'center',
        color: 'var(--text-gray)',
        fontSize: '12px',
      }}>
        <div style={{ fontSize: '14px', fontWeight: 700, marginBottom: '4px', color: 'var(--text-dark)' }}>
          추천 목록 품질 평가
        </div>
        평가 데이터 없음 — <code>python evaluation.py --labels eval_labels.csv --k 5</code> 실행 후 표시됩니다.
      </div>
    );
  }
  if (!data) return null;

  const {
    metrics = {},
    k = 5,
    queries = data.query_count || 0,
    label_type = 'pseudo-label',
    description = '',
  } = data;

  const isPseudo = label_type === 'pseudo-label';

  return (
    <div style={{
      backgroundColor: 'var(--white)',
      borderRadius: 'var(--radius-lg)',
      padding: '24px',
      boxShadow: 'var(--shadow-sm)',
      border: '1px dashed var(--border-color)',
      marginTop: '16px',
      marginBottom: '8px',
    }}>
      <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'flex-start', gap: '8px', marginBottom: '12px' }}>
        <div>
          <h3 style={{ fontSize: '15px', fontWeight: 800, marginBottom: '2px' }}>
            추천 목록 품질 평가
          </h3>
          <p style={{ fontSize: '11px', color: 'var(--text-gray)' }}>
            평가 쿼리 수 {queries} · k={k} · 추천 리스트 전체에 대한 순위 평가입니다
          </p>
        </div>
        <span style={{
          fontSize: '10px', fontWeight: 700,
          background: isPseudo ? '#FEF3C7' : '#DCFCE7',
          color:       isPseudo ? '#92400E' : '#166534',
          padding: '3px 8px', borderRadius: '999px',
          whiteSpace: 'nowrap',
        }}>
          {isPseudo ? '시연용 pseudo-label' : '사용자 라벨 기반'}
        </span>
      </div>

      <div style={{
        display: 'grid',
        gridTemplateColumns: 'repeat(4, 1fr)',
        gap: '8px',
        marginBottom: '10px',
      }}>
        {METRIC_INFO.map((info) => {
          const raw = metrics[info.key] ?? metrics[info.alt];
          const num = typeof raw === 'number' ? raw : null;
          const pct = num != null ? Math.max(0, Math.min(100, Math.round(num * 100))) : 0;
          return (
            <div key={info.key} style={{
              background: '#FAF9F6',
              borderRadius: 'var(--radius-md)',
              padding: '14px 10px',
              textAlign: 'center',
            }} title={info.desc}>
              <div style={{ fontSize: '12px', color: 'var(--text-gray)', fontWeight: 600, marginBottom: '4px' }}>
                {info.label}@{k}
              </div>
              <div style={{ fontSize: '22px', fontWeight: 900, color: 'var(--text-dark)' }}>
                {num != null ? num.toFixed(3) : '—'}
              </div>
              <div style={{
                marginTop: '8px',
                height: '6px',
                background: '#E5E7EB',
                borderRadius: '999px',
                overflow: 'hidden',
              }}>
                <div style={{
                  width: `${pct}%`,
                  height: '100%',
                  background: 'var(--primary, #FF7E67)',
                  borderRadius: '999px',
                  transition: 'width 0.4s',
                }} />
              </div>
            </div>
          );
        })}
      </div>

      <button
        onClick={() => setExpanded((v) => !v)}
        style={{
          background: 'transparent', border: 'none',
          color: 'var(--primary)', fontSize: '12px', fontWeight: 600,
          display: 'inline-flex', alignItems: 'center', gap: '4px',
          cursor: 'pointer', padding: 0, marginBottom: '8px',
        }}
      >
        평가 설명 {expanded ? <ChevronUp size={13} /> : <ChevronDown size={13} />}
      </button>

      {expanded && (
        <div style={{
          background: '#F9FAFB', borderRadius: '8px', padding: '10px 12px',
          fontSize: '11.5px', color: 'var(--text-gray)', lineHeight: 1.6,
          marginBottom: '8px',
        }}>
          <div style={{ display: 'flex', flexDirection: 'column', gap: '4px' }}>
            {METRIC_INFO.map((info) => (
              <div key={info.key}>
                <span style={{ fontWeight: 700, color: 'var(--text-dark)' }}>
                  {info.label}@{k}
                </span>{' — '}{info.desc}
              </div>
            ))}
          </div>
        </div>
      )}

      <div style={{
        display: 'flex', alignItems: 'flex-start', gap: '6px',
        fontSize: '11px', color: 'var(--text-gray)', lineHeight: 1.5,
        background: '#F9FAFB', padding: '8px 10px', borderRadius: '6px',
      }}>
        <Info size={12} style={{ flexShrink: 0, marginTop: '2px' }} />
        <span>
          {description ||
            (isPseudo
              ? '현재 지표는 추천 목록 전체의 순위 품질을 점검하기 위한 평가이며, 최종 성능 평가는 사용자 라벨링 데이터 확보 후 재평가할 예정입니다.'
              : '사용자가 라벨링한 relevance 데이터 기반 평가입니다.')}
        </span>
      </div>
    </div>
  );
};

export default EvaluationCard;
