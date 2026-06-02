import { useEffect, useState } from 'react';
import { AnimatePresence, motion } from 'framer-motion';

const TEXTS = [
  '당신의 입맛 지도를 세밀하게 그리는 중...',
  '취향 재료들을 프라이팬에 볶고 있어요 🔥',
  '매콤달콤한 나만의 맛집 레시피 완성 중...',
  '고기와 야채의 황금 비율을 맞추는 중...',
  '리뷰 속 숨은 맛의 비밀을 찾고 있어요',
  '오늘의 최고 맛집을 불꽃 분석 중입니다',
  '불향 가득한 취향 조합을 완성 중...',
  '셰프의 마음으로 메뉴를 엄선하는 중...',
  '수천 개의 리뷰를 맛있게 요리하는 중...',
  '당신의 오늘 입맛에 딱 맞는 곳 찾는 중...',
];

/* tx = 피크 x 이동량 (양수 → 오른쪽, 음수 → 왼쪽) */
const INGREDIENTS = [
  { emoji: '🥩', tx: -34, delay: 0    },
  { emoji: '🥦', tx: -14, delay: 0.22 },
  { emoji: '🧄', tx:   2, delay: 0.44 },
  { emoji: '🌶️', tx:  16, delay: 0.66 },
  { emoji: '🫑', tx:  32, delay: 0.88 },
];

const ROTATE_MS = 2000;

const CookingLoader = () => {
  const [idx, setIdx] = useState(() => Math.floor(Math.random() * TEXTS.length));

  useEffect(() => {
    const tick = setInterval(() => setIdx(i => (i + 1) % TEXTS.length), ROTATE_MS);
    return () => clearInterval(tick);
  }, []);

  return (
    <div className="cooker-wrap" role="status" aria-live="polite">
      {/* 프라이팬 + 재료 */}
      <div className="pan-stage">
        {/* 연기 */}
        <div className="pan-smoke" />
        <div className="pan-smoke" />
        <div className="pan-smoke" />

        {/* 재료들 */}
        {INGREDIENTS.map(({ emoji, tx, delay }) => (
          <span
            key={emoji}
            className="pan-ingredient"
            style={{ '--tx': `${tx}px`, '--delay': `${delay}s` }}
            aria-hidden="true"
          >
            {emoji}
          </span>
        ))}

        {/* 프라이팬 */}
        <span className="pan-body" aria-hidden="true">🍳</span>
      </div>

      {/* 로테이팅 텍스트 */}
      <div className="cooker-text">
        <span className="eyebrow">리뷰 분석 중</span>
        <AnimatePresence mode="wait">
          <motion.p
            key={idx}
            className="cooker-line"
            initial={{ opacity: 0, y: 8 }}
            animate={{ opacity: 1, y: 0 }}
            exit={{ opacity: 0, y: -8 }}
            transition={{ duration: 0.38, ease: 'easeOut' }}
          >
            {TEXTS[idx]}
          </motion.p>
        </AnimatePresence>
        <div className="cooker-dots">
          <span /><span /><span />
        </div>
      </div>
    </div>
  );
};

export default CookingLoader;
