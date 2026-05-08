import React, { useState } from 'react';
import { Search, ArrowRight } from 'lucide-react';
import { motion } from 'framer-motion';

// ── Inline SVG decorations ──────────────────────────────────────

const CurvedArrow = ({ flip = false, rotate = 0, style = {} }) => (
  <svg
    width="52" height="42" viewBox="0 0 52 42" fill="none"
    style={{ transform: `rotate(${rotate}deg) scaleX(${flip ? -1 : 1})`, flexShrink: 0, ...style }}
  >
    <path
      d="M4 28 C10 10 28 4 44 16"
      stroke="#444" strokeWidth="2.2" strokeLinecap="round" fill="none"
    />
    <path
      d="M38 10 L44 16 L36 20"
      stroke="#444" strokeWidth="2.2" strokeLinecap="round" strokeLinejoin="round" fill="none"
    />
  </svg>
);

const StarDoodle = ({ size = 18, opacity = 0.55, style = {} }) => (
  <svg width={size} height={size} viewBox="0 0 20 20" fill="none" style={style}>
    <path
      d="M10 1 L12 7.5 L19 7.5 L13.5 11.5 L15.5 18 L10 14 L4.5 18 L6.5 11.5 L1 7.5 L8 7.5 Z"
      fill="#333" opacity={opacity}
    />
  </svg>
);

const SparkDoodle = ({ style = {} }) => (
  <svg width="28" height="28" viewBox="0 0 28 28" fill="none" style={style}>
    <line x1="14" y1="2" x2="14" y2="8"  stroke="#555" strokeWidth="2" strokeLinecap="round" opacity="0.5"/>
    <line x1="14" y1="20" x2="14" y2="26" stroke="#555" strokeWidth="2" strokeLinecap="round" opacity="0.5"/>
    <line x1="2"  y1="14" x2="8"  y2="14" stroke="#555" strokeWidth="2" strokeLinecap="round" opacity="0.5"/>
    <line x1="20" y1="14" x2="26" y2="14" stroke="#555" strokeWidth="2" strokeLinecap="round" opacity="0.5"/>
    <line x1="5"  y1="5"  x2="9"  y2="9"  stroke="#555" strokeWidth="1.8" strokeLinecap="round" opacity="0.35"/>
    <line x1="19" y1="19" x2="23" y2="23" stroke="#555" strokeWidth="1.8" strokeLinecap="round" opacity="0.35"/>
    <line x1="19" y1="5"  x2="23" y2="9"  stroke="#555" strokeWidth="1.8" strokeLinecap="round" opacity="0.35"/>
    <line x1="5"  y1="19" x2="9"  y2="23" stroke="#555" strokeWidth="1.8" strokeLinecap="round" opacity="0.35"/>
    <circle cx="14" cy="14" r="3" fill="#555" opacity="0.45"/>
  </svg>
);

const WiggleLine = ({ style = {} }) => (
  <svg width="48" height="14" viewBox="0 0 48 14" fill="none" style={style}>
    <path
      d="M2 7 C6 2 10 12 14 7 C18 2 22 12 26 7 C30 2 34 12 38 7 C42 2 46 12 50 7"
      stroke="#888" strokeWidth="1.8" strokeLinecap="round" fill="none" opacity="0.45"
    />
  </svg>
);

// ── Floating bento card data ────────────────────────────────────

const CARDS = [
  {
    id: 1,
    emoji: '🍣',
    bg: 'var(--accent-blue)',
    rotation: -6,
    pos: { top: '9%', left: '4%' },
    annotation: '초밥 최고!',
    annotationPos: { bottom: -28, right: -86 },
    arrowFlip: false,
    arrowRotate: 20,
    delay: 0.25,
  },
  {
    id: 2,
    emoji: '🥩',
    bg: 'var(--accent-peach)',
    rotation: 5,
    pos: { top: '9%', right: '4%' },
    annotation: '고기는 진리',
    annotationPos: { bottom: -28, left: -96 },
    arrowFlip: true,
    arrowRotate: -20,
    delay: 0.35,
  },
  {
    id: 3,
    emoji: '🐟',
    bg: 'var(--accent-green)',
    rotation: 5,
    pos: { bottom: '9%', left: '4%' },
    annotation: '매콤 냠냠',
    annotationPos: { top: -28, right: -80 },
    arrowFlip: false,
    arrowRotate: -160,
    delay: 0.45,
  },
  {
    id: 4,
    emoji: '🍲',
    bg: 'var(--accent-lavender)',
    rotation: -4,
    pos: { bottom: '9%', right: '4%' },
    annotation: '추억의 맛',
    annotationPos: { top: -28, left: -80 },
    arrowFlip: true,
    arrowRotate: 160,
    delay: 0.55,
  },
];

const HomeStep = ({ availableKeywords, defaultKeyword, onStart }) => {
  const [query, setQuery] = useState(defaultKeyword || '');

  const handleSubmit = (e) => {
    if (e && e.preventDefault) e.preventDefault();
    if (!query.trim()) return alert('음식명을 입력해주세요!');
    if (!availableKeywords.includes(query.trim())) {
      return alert(`'${query}' 데이터가 없습니다.`);
    }
    onStart(query.trim());
  };

  return (
    <div style={{
      position: 'relative',
      minHeight: '100vh',
      display: 'flex',
      alignItems: 'center',
      justifyContent: 'center',
      background: 'var(--bg-color)',
      overflow: 'hidden',
    }}>
      {/* Background doodles */}
      <StarDoodle size={14} opacity={0.3} style={{ position: 'absolute', top: '18%', left: '48%' }} />
      <StarDoodle size={10} opacity={0.25} style={{ position: 'absolute', bottom: '28%', left: '40%' }} />
      <SparkDoodle style={{ position: 'absolute', top: '40%', left: '28%', opacity: 0.35 }} />
      <SparkDoodle style={{ position: 'absolute', top: '38%', right: '28%', opacity: 0.3 }} />
      <WiggleLine style={{ position: 'absolute', top: '14%', right: '30%' }} />
      <WiggleLine style={{ position: 'absolute', bottom: '18%', left: '28%' }} />

      {/* Floating bento cards */}
      {CARDS.map((card) => (
        <motion.div
          key={card.id}
          className="bento-card"
          initial={{ opacity: 0, y: 40, rotate: card.rotation }}
          animate={{ opacity: 1, y: 0, rotate: card.rotation }}
          transition={{ delay: card.delay, duration: 0.75, ease: [0.22, 1, 0.36, 1] }}
          whileHover={{ y: -12, rotate: card.rotation * 0.3, transition: { duration: 0.28 } }}
          style={{
            position: 'absolute',
            ...card.pos,
            width: 'clamp(140px, 13vw, 180px)',
            height: 'clamp(140px, 13vw, 180px)',
            background: card.bg,
            borderRadius: '28px',
            boxShadow: 'var(--shadow-card)',
            display: 'flex',
            alignItems: 'center',
            justifyContent: 'center',
            fontSize: 'clamp(54px, 5.5vw, 74px)',
            userSelect: 'none',
            cursor: 'default',
          }}
        >
          {card.emoji}

          {/* Handwritten annotation */}
          <div style={{
            position: 'absolute',
            ...card.annotationPos,
            display: 'flex',
            alignItems: 'center',
            gap: '4px',
            whiteSpace: 'nowrap',
            pointerEvents: 'none',
          }}>
            <CurvedArrow
              flip={card.arrowFlip}
              rotate={card.arrowRotate}
              style={{ opacity: 0.65 }}
            />
            <span style={{
              fontFamily: 'var(--font-handwritten)',
              fontSize: '16px',
              fontWeight: 700,
              color: '#3A3A3A',
              lineHeight: 1,
            }}>
              {card.annotation}
            </span>
          </div>
        </motion.div>
      ))}

      {/* Center hero content */}
      <motion.div
        initial={{ opacity: 0, y: 24 }}
        animate={{ opacity: 1, y: 0 }}
        transition={{ duration: 0.65, delay: 0.1 }}
        style={{
          textAlign: 'center',
          zIndex: 10,
          padding: '24px 20px',
          maxWidth: '500px',
          width: '100%',
        }}
      >
        <h1 style={{
          fontSize: 'clamp(38px, 5.5vw, 64px)',
          fontWeight: 900,
          lineHeight: 1.12,
          letterSpacing: '-1.5px',
          color: 'var(--text-dark)',
          marginBottom: '32px',
          whiteSpace: 'pre-line',
        }}>
          {'오늘 당신의\n미식 여행은?'}
        </h1>

        {/* Search bar */}
        <form onSubmit={handleSubmit} style={{ marginBottom: '20px' }}>
          <div style={{
            display: 'flex',
            alignItems: 'center',
            background: 'rgba(255,255,255,0.92)',
            backdropFilter: 'blur(8px)',
            borderRadius: '999px',
            padding: '10px 14px 10px 22px',
            boxShadow: '0 4px 20px rgba(0,0,0,0.07)',
            border: '1.5px solid #E8E8E8',
            gap: '10px',
          }}>
            <Search size={17} color="#AAA" style={{ flexShrink: 0 }} />
            <input
              type="text"
              value={query}
              onChange={e => setQuery(e.target.value)}
              placeholder="스시 맛집, 삼겹살, 매운 음식..."
              list="keywords-list"
              style={{
                flex: 1, border: 'none', outline: 'none',
                fontSize: '15px', background: 'transparent',
                color: 'var(--text-dark)', padding: '6px 0',
                fontFamily: 'inherit',
              }}
            />
            <datalist id="keywords-list">
              {availableKeywords.map(kw => <option key={kw} value={kw} />)}
            </datalist>
          </div>
        </form>

        {/* CTA button */}
        <motion.button
          onClick={handleSubmit}
          whileHover={{ scale: 1.04, y: -3 }}
          whileTap={{ scale: 0.96 }}
          style={{
            background: 'var(--btn-dark)',
            color: 'white',
            border: 'none',
            borderRadius: '999px',
            padding: '16px 40px',
            fontSize: '16px',
            fontWeight: 700,
            cursor: 'pointer',
            display: 'inline-flex',
            alignItems: 'center',
            gap: '10px',
            fontFamily: 'inherit',
            boxShadow: '0 6px 28px rgba(0,0,0,0.22)',
            letterSpacing: '-0.2px',
          }}
        >
          미식 여행 시작하기
          <ArrowRight size={18} />
        </motion.button>
      </motion.div>
    </div>
  );
};

export default HomeStep;
