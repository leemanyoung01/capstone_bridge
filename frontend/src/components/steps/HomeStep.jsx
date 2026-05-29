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

// ── Category Data ───────────────────────────────────────────────

const CATEGORIES = [
  { id: 'meat',    label: '고기',      emoji: '🥩', color: 'var(--primary-light)'  },
  { id: 'korean',  label: '밥/한식',   emoji: '🍚', color: 'var(--accent-yellow)'  },
  { id: 'noodle',  label: '면요리',    emoji: '🍜', color: 'var(--accent-peach)'   },
  { id: 'seafood', label: '일식/해물', emoji: '🍣', color: 'var(--accent-blue)'    },
  { id: 'chinese', label: '중식',      emoji: '🥡', color: 'var(--accent-rose)'    },
  { id: 'western', label: '양식',      emoji: '🍕', color: 'var(--accent-green)'   },
  { id: 'snack',   label: '분식/야식', emoji: '🥟', color: 'var(--accent-lavender)' },
];

// Menus map N:M to categories. The display order inside each
// category drawer follows this list's order.
const MENU_ITEMS = [
  { name: '곱창',        categories: ['meat'] },
  { name: '닭갈비',      categories: ['meat'] },
  { name: '삼겹살',      categories: ['meat'] },
  { name: '소고기',      categories: ['meat'] },
  { name: '족발',        categories: ['meat'] },

  { name: '김치찌개',    categories: ['korean'] },
  { name: '덮밥',        categories: ['korean'] },
  { name: '뼈해장국',    categories: ['korean'] },
  { name: '순대국',      categories: ['korean'] },
  { name: '청국장',      categories: ['korean'] },
  { name: '콩나물국밥',  categories: ['korean'] },
  { name: '한상',        categories: ['korean'] },

  { name: '마라탕',      categories: ['noodle', 'chinese'] },
  { name: '소바',        categories: ['noodle'] },
  { name: '우동',        categories: ['noodle'] },
  { name: '짜장면',      categories: ['noodle', 'chinese'] },
  { name: '짬뽕',        categories: ['noodle', 'chinese'] },
  { name: '칼국수',      categories: ['noodle'] },
  { name: '파스타',      categories: ['noodle'] },

  { name: '돈까스',      categories: ['seafood'] },
  { name: '스시',        categories: ['seafood'] },
  // 아구찜은 한식·해물 두 카테고리에 모두 노출됩니다.
  { name: '아구찜',      categories: ['korean', 'seafood'] },
  { name: '조개',        categories: ['seafood'] },
  { name: '회',          categories: ['seafood'] },

  { name: '버거',        categories: ['western'] },
  { name: '샐러드',      categories: ['western'] },
  { name: '스테이크',    categories: ['western'] },
  { name: '피자',        categories: ['western'] },

  { name: '김밥',        categories: ['snack'] },
  { name: '딤섬',        categories: ['snack', 'chinese'] },
  { name: '떡볶이',      categories: ['snack'] },
  { name: '치킨',        categories: ['snack'] },
  { name: '커피',        categories: ['snack'] },
  { name: '훠궈',        categories: ['snack', 'chinese'] },
];

const itemsForCategory = (catId) =>
  MENU_ITEMS.filter(m => m.categories.includes(catId)).map(m => m.name);

// Row split — 4 in the first row, 3 in the second.
const ROW_BREAKS = [4, 7];

const HomeStep = ({ availableKeywords, defaultKeyword, onStart }) => {
  const [query, setQuery] = useState(defaultKeyword || '');
  const [selectedCat, setSelectedCat] = useState(null);

  const handleSubmit = (e) => {
    if (e && e.preventDefault) e.preventDefault();
    if (!query.trim()) return alert('음식명을 입력해주세요!');
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
      <StarDoodle size={14} opacity={0.3} style={{ position: 'absolute', top: '10%', left: '45%' }} />
      <StarDoodle size={10} opacity={0.25} style={{ position: 'absolute', bottom: '22%', right: '35%' }} />
      <SparkDoodle style={{ position: 'absolute', top: '30%', left: '20%', opacity: 0.35 }} />
      <SparkDoodle style={{ position: 'absolute', top: '32%', right: '18%', opacity: 0.3 }} />
      <WiggleLine style={{ position: 'absolute', top: '12%', right: '25%' }} />
      <WiggleLine style={{ position: 'absolute', bottom: '15%', left: '25%' }} />

      {/* Floating animations removed */}

      {/* Center hero content */}
      <motion.div
        initial={{ opacity: 0, y: 24 }}
        animate={{ opacity: 1, y: 0 }}
        transition={{ duration: 0.65, delay: 0.1 }}
        style={{
          textAlign: 'center',
          zIndex: 10,
          padding: '24px 20px',
          maxWidth: '700px',
          width: '100%',
          marginTop: '-8vh',
        }}
      >
        <h1 style={{
          fontSize: 'clamp(38px, 5.5vw, 64px)',
          fontWeight: 900,
          lineHeight: 1.12,
          letterSpacing: '-1.5px',
          color: 'var(--text-dark)',
          marginBottom: '12px',
          whiteSpace: 'pre-line',
          textAlign: 'center',
        }}>
          {'오늘 당신의\n미식 여행은?'}
        </h1>

        {/* Search bar */}
        <form onSubmit={handleSubmit} style={{ marginBottom: '12px' }}>
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

        {/* Category Browsing */}
        <div style={{ marginBottom: '32px' }}>
          <p style={{
            fontSize: '14px',
            color: 'var(--text-light)',
            marginBottom: '16px',
            fontWeight: 500,
          }}>
            또는 아래 카테고리를 터치하세요
          </p>

          <div style={{ display: 'flex', flexDirection: 'column', gap: '12px' }}>
            {ROW_BREAKS.map((endIdx, rowIdx) => {
              const startIdx = rowIdx === 0 ? 0 : ROW_BREAKS[rowIdx - 1];
              const rowCats = CATEGORIES.slice(startIdx, endIdx);
              const activeInRow = rowCats.find(c => c.id === selectedCat);
              const activeCat = CATEGORIES.find(c => c.id === selectedCat);
              const activeItems = activeInRow ? itemsForCategory(activeInRow.id) : [];

              return (
                <React.Fragment key={`row-${rowIdx}`}>
                  <div className="category-grid" style={{
                    display: 'grid',
                    gridTemplateColumns: `repeat(${rowCats.length}, 1fr)`,
                    gap: '12px',
                  }}>
                    {rowCats.map(cat => (
                      <motion.button
                        key={cat.id}
                        onClick={(e) => {
                          e.preventDefault();
                          setSelectedCat(selectedCat === cat.id ? null : cat.id);
                        }}
                        whileHover={{ y: -2 }}
                        whileTap={{ scale: 0.95 }}
                        className={`category-tab ${selectedCat === cat.id ? 'active' : ''}`}
                      >
                        <span className="cat-emoji">{cat.emoji}</span>
                        <span className="cat-label">{cat.label}</span>
                      </motion.button>
                    ))}
                  </div>

                  <motion.div
                    initial={false}
                    animate={{
                      height: activeInRow ? 'auto' : 0,
                      opacity: activeInRow ? 1 : 0,
                      marginTop: activeInRow ? '4px' : '0px',
                      marginBottom: activeInRow ? '8px' : '0px',
                    }}
                    style={{ overflow: 'hidden' }}
                  >
                    {activeInRow && (
                      <div style={{ display: 'flex', justifyContent: 'center', width: '100%' }}>
                        <motion.div
                          initial={{ y: -10, opacity: 0 }}
                          animate={{ y: 0, opacity: 1 }}
                          transition={{ duration: 0.3 }}
                          className="chips-container hide-scrollbar"
                          style={{ background: activeCat?.color, maxWidth: '100%' }}
                        >
                          {activeItems.map((item, idx) => (
                            <motion.button
                              key={item}
                              onClick={(e) => {
                                e.preventDefault();
                                onStart(item);
                              }}
                              whileHover={{ scale: 1.05 }}
                              whileTap={{ scale: 0.95 }}
                              initial={{ opacity: 0, scale: 0.8 }}
                              animate={{ opacity: 1, scale: 1 }}
                              transition={{ delay: idx * 0.05 }}
                              className="quick-chip"
                            >
                              {item}
                            </motion.button>
                          ))}
                        </motion.div>
                      </div>
                    )}
                  </motion.div>
                </React.Fragment>
              );
            })}
          </div>
        </div>

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
