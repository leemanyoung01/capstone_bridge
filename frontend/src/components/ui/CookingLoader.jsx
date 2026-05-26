import { useEffect, useState } from 'react';
import { AnimatePresence, motion } from 'framer-motion';

const TEXT_POOLS = {
  gourmet: [
    '당신의 입맛 지도를 세밀하게 그리는 중...',
    "오늘 가장 완벽한 '한 입'을 매칭하고 있어요.",
    '셰프의 마음으로 추천 메뉴를 고르는 중...',
  ],
  cooking: [
    '선택하신 취향을 프라이팬에 맛있게 볶는 중...',
    '맵기와 감칠맛의 황금 비율을 맞추고 있습니다.',
    '오늘 날씨와 딱 맞는 숨은 맛집 에센스 투하 중...',
  ],
};

// Five ingredients tossed out of the pan. tx/tx2 are the peak X-offsets
// at the top of the arc (35%) and on the way down (70%); delay staggers
// each piece so they don't move in lockstep.
const INGREDIENTS = [
  { emoji: '🥕', tx: -38, tx2: -18, delay: 0    },
  { emoji: '🥦', tx: -14, tx2:  -4, delay: 0.18 },
  { emoji: '🌽', tx:   6, tx2:   2, delay: 0.36 },
  { emoji: '🍅', tx:  22, tx2:  10, delay: 0.54 },
  { emoji: '🥩', tx:  40, tx2:  20, delay: 0.72 },
];

const ROTATE_MS = 1500;

const CookingLoader = ({ label }) => {
  // Pick one phrase set on mount and keep it stable for this loader instance.
  const [phrases] = useState(() => {
    const keys = Object.keys(TEXT_POOLS);
    return TEXT_POOLS[keys[Math.floor(Math.random() * keys.length)]];
  });
  const [idx, setIdx] = useState(0);

  useEffect(() => {
    const tick = setInterval(() => {
      setIdx(i => (i + 1) % phrases.length);
    }, ROTATE_MS);
    return () => clearInterval(tick);
  }, [phrases.length]);

  return (
    <div className="cooking-loader" role="status" aria-live="polite">
      <div className="pan-stage">
        {INGREDIENTS.map(({ emoji, tx, tx2, delay }) => (
          <span
            key={emoji}
            className="ingredient"
            style={{
              '--tx':    `${tx}px`,
              '--tx2':   `${tx2}px`,
              '--delay': `${delay}s`,
            }}
          >
            {emoji}
          </span>
        ))}

        <span className="pan-steam" />
        <span className="pan-steam s2" />
        <span className="pan-steam s3" />

        <span className="pan-body" aria-hidden="true">🍳</span>
      </div>

      <AnimatePresence mode="wait">
        <motion.p
          key={idx}
          className="cooking-text"
          initial={{ opacity: 0, y: 6 }}
          animate={{ opacity: 1, y: 0 }}
          exit={{ opacity: 0, y: -6 }}
          transition={{ duration: 0.35, ease: 'easeOut' }}
        >
          {phrases[idx]}
        </motion.p>
      </AnimatePresence>

      {label && (
        <p style={{ marginTop: 8, fontSize: 12, color: 'var(--text-light)' }}>
          {label}
        </p>
      )}
    </div>
  );
};

export default CookingLoader;
