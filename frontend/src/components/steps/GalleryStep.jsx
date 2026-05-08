import React from 'react';
import { motion, AnimatePresence } from 'framer-motion';
import { ArrowLeft, ArrowRight, SkipForward } from 'lucide-react';

// ── Deterministic rotation pool — index-stable, no jitter on re-render ──
const ROTATIONS = [-2, 1, -1.5, 2, -1, 0.5, -0.5, 1.5, -2.5, 1, -1, 0.8];

// ── Inline SVG decorations (echoes HomeStep language) ──────────
const StarDoodle = ({ size = 14, style = {} }) => (
  <svg width={size} height={size} viewBox="0 0 20 20" fill="none" style={style}>
    <path d="M10 1 L12 7.5 L19 7.5 L13.5 11.5 L15.5 18 L10 14 L4.5 18 L6.5 11.5 L1 7.5 L8 7.5 Z"
      fill="#333" opacity="0.15" />
  </svg>
);

const WavyLine = ({ style = {} }) => (
  <svg width="48" height="14" viewBox="0 0 48 14" fill="none" style={style}>
    <path d="M2 7 C6 2 10 12 14 7 C18 2 22 12 26 7 C30 2 34 12 38 7 C42 2 46 12 50 7"
      stroke="#888" strokeWidth="1.8" strokeLinecap="round" fill="none" opacity="0.25"/>
  </svg>
);

const PlusDoodle = ({ style = {} }) => (
  <svg width="20" height="20" viewBox="0 0 20 20" fill="none" style={style}>
    <line x1="10" y1="2" x2="10" y2="18" stroke="#555" strokeWidth="2.2" strokeLinecap="round" opacity="0.22"/>
    <line x1="2"  y1="10" x2="18" y2="10" stroke="#555" strokeWidth="2.2" strokeLinecap="round" opacity="0.22"/>
  </svg>
);

// ── Ink-stamp SVG badge ─────────────────────────────────────────
const StampBadge = () => (
  <svg width="40" height="40" viewBox="0 0 40 40" fill="none">
    {/* Outer dashed ring — mimics ink stamp border */}
    <circle cx="20" cy="20" r="18" stroke="white" strokeWidth="1.5"
      strokeDasharray="3 2" opacity="0.7"/>
    {/* Filled circle */}
    <circle cx="20" cy="20" r="15" fill="var(--primary)" opacity="0.95"/>
    {/* Checkmark path */}
    <path d="M13 20 L18 25 L27 15" stroke="white" strokeWidth="2.8"
      strokeLinecap="round" strokeLinejoin="round"/>
  </svg>
);

const GalleryStep = ({ images, selectedImageIdx, onImageToggle, onPrev, onSkip, onNext }) => {
  return (
    <motion.div
      initial={{ opacity: 0, y: 16 }}
      animate={{ opacity: 1, y: 0 }}
      style={{ position: 'relative', minHeight: '100vh' }}
    >
      {/* ── Scattered doodles ── */}
      <StarDoodle size={18} style={{ position: 'absolute', top: '8%',   left:  '2%' }} />
      <PlusDoodle            style={{ position: 'absolute', top: '25%',  left:  '1.5%' }} />
      <WavyLine              style={{ position: 'absolute', bottom: '30%', left: '1%' }} />
      <StarDoodle size={12}  style={{ position: 'absolute', top: '15%',  right: '2%' }} />
      <PlusDoodle            style={{ position: 'absolute', top: '60%',  right: '1.5%' }} />
      <WavyLine              style={{ position: 'absolute', bottom: '18%', right: '1%' }} />

      {/* ── Heading ── */}
      <div style={{ textAlign: 'center', padding: '52px 24px 36px' }}>
        <h2 style={{
          fontSize: 'clamp(28px, 4vw, 46px)',
          fontWeight: 900,
          letterSpacing: '-1.5px',
          lineHeight: 1.1,
          color: 'var(--text-dark)',
          marginBottom: '10px',
        }}>
          <span style={{ color: 'var(--primary)' }}>📸</span>{' '}
          분위기{' '}
          <span style={{ color: 'var(--text-gray)', fontWeight: 500 }}>매칭</span>
        </h2>
        <p style={{ fontSize: '15px', color: 'var(--text-gray)', display: 'flex', alignItems: 'center', justifyContent: 'center', gap: '10px' }}>
          끌리는 사진을 골라주세요
          <AnimatePresence>
            {selectedImageIdx.length > 0 && (
              <motion.span
                key="count"
                initial={{ opacity: 0, scale: 0.7 }}
                animate={{ opacity: 1, scale: 1 }}
                exit={{ opacity: 0, scale: 0.7 }}
                style={{
                  background: 'var(--primary)', color: 'white',
                  fontSize: '12px', fontWeight: 800,
                  padding: '2px 10px', borderRadius: '999px',
                }}
              >
                {selectedImageIdx.length}장 선택됨
              </motion.span>
            )}
          </AnimatePresence>
        </p>
      </div>

      {/* ── Polaroid grid — paddingBottom leaves room for floating nav ── */}
      <div style={{ maxWidth: '1020px', margin: '0 auto', padding: '0 24px 120px' }}>
        {images.length === 0 ? (
          <div style={{
            textAlign: 'center', padding: '72px 20px',
            background: 'rgba(255,255,255,0.85)',
            borderRadius: '24px',
            boxShadow: 'var(--shadow-sm)',
          }}>
            <div style={{ fontSize: '48px', marginBottom: '16px' }}>🍽️</div>
            <p style={{ fontWeight: 800, fontSize: '16px', marginBottom: '6px' }}>아직 준비된 사진이 없어요!</p>
            <p style={{ fontSize: '13px', color: 'var(--text-gray)' }}>건너뛰기를 눌러 텍스트만으로 추천을 받아보세요.</p>
          </div>
        ) : (
          /* Flex-wrap so rotated Polaroids reflow naturally */
          <div style={{
            display: 'flex',
            flexWrap: 'wrap',
            gap: '24px',
            justifyContent: 'center',
          }}>
            {images.map((img, idx) => {
              const isSelected = selectedImageIdx.includes(idx);
              const rotation = ROTATIONS[idx % ROTATIONS.length];
              const imgSrc = img.image_src
                ? (img.image_src.startsWith('images/') ? `/${img.image_src}` : img.image_src)
                : '';

              return (
                <motion.div
                  key={idx}
                  whileHover={{
                    y: -10,
                    rotate: rotation * 0.3,
                    scale: 1.04,
                    transition: { duration: 0.22 },
                  }}
                  whileTap={{ scale: 0.97 }}
                  onClick={() => onImageToggle(idx)}
                  style={{
                    // ── Polaroid frame ──
                    width: '200px',
                    flexShrink: 0,
                    background: 'white',
                    padding: '10px 10px 36px',
                    borderRadius: '3px',
                    boxShadow: isSelected
                      ? '0 12px 40px rgba(255,126,103,0.25), 0 4px 12px rgba(0,0,0,0.12)'
                      : '0 8px 32px rgba(0,0,0,0.13), 0 2px 8px rgba(0,0,0,0.07)',
                    transform: `rotate(${rotation}deg)`,
                    cursor: 'pointer',
                    position: 'relative',
                    // Selected: coral border glow instead of solid black
                    outline: isSelected ? '2.5px solid var(--primary)' : '2.5px solid transparent',
                    transition: 'box-shadow 0.2s, outline 0.18s',
                  }}
                >
                  {/* Photo area */}
                  {imgSrc ? (
                    <img
                      src={imgSrc}
                      alt={img.label || '음식 사진'}
                      style={{
                        width: '100%',
                        aspectRatio: '1/1',
                        objectFit: 'cover',
                        display: 'block',
                        borderRadius: '1px',
                      }}
                    />
                  ) : (
                    <div style={{
                      width: '100%', aspectRatio: '1/1',
                      display: 'flex', alignItems: 'center', justifyContent: 'center',
                      fontSize: '44px', background: 'var(--accent-yellow)',
                      borderRadius: '1px',
                    }}>🍽️</div>
                  )}

                  {/* Handwritten Gaegu caption */}
                  <div style={{
                    marginTop: '12px',
                    textAlign: 'center',
                  }}>
                    <div style={{
                      fontFamily: 'var(--font-handwritten)',
                      fontSize: '16px',
                      fontWeight: 700,
                      color: 'var(--text-dark)',
                      lineHeight: 1.3,
                    }}>
                      {img.label || img.axis || '대표 이미지'}
                    </div>
                    {/* Restaurant name removed per user request */}
                  </div>

                  {/* Stamp animation on selection */}
                  <AnimatePresence>
                    {isSelected && (
                      <motion.div
                        key="stamp"
                        initial={{ scale: 0, rotate: -25, opacity: 0 }}
                        animate={{ scale: 1, rotate: 0, opacity: 1 }}
                        exit={{ scale: 0, opacity: 0 }}
                        transition={{ type: 'spring', stiffness: 380, damping: 16 }}
                        style={{
                          position: 'absolute',
                          top: '8px',
                          right: '8px',
                        }}
                      >
                        <StampBadge />
                      </motion.div>
                    )}
                  </AnimatePresence>
                </motion.div>
              );
            })}
          </div>
        )}
      </div>

      {/* ── Floating glassmorphism bottom nav ── */}
      <div style={{ position: 'fixed', bottom: '28px', left: '50%', transform: 'translateX(-50%)', zIndex: 100 }}>
      <motion.div
        initial={{ opacity: 0, y: 20 }}
        animate={{ opacity: 1, y: 0 }}
        transition={{ delay: 0.3, duration: 0.5 }}
        style={{
          display: 'flex',
          alignItems: 'center',
          gap: '8px',
          padding: '10px 14px',
          borderRadius: '999px',
          background: 'rgba(250,249,246,0.78)',
          backdropFilter: 'blur(14px)',
          WebkitBackdropFilter: 'blur(14px)',
          border: '1px solid rgba(255,255,255,0.6)',
          boxShadow: '0 8px 40px rgba(0,0,0,0.14), inset 0 1px 0 rgba(255,255,255,0.65)',
          whiteSpace: 'nowrap',
        }}
      >
        {/* Previous */}
        <motion.button
          onClick={onPrev}
          whileHover={{ x: -2 }}
          whileTap={{ scale: 0.95 }}
          style={{
            display: 'inline-flex', alignItems: 'center', gap: '5px',
            background: 'transparent', border: '1px solid rgba(0,0,0,0.12)',
            borderRadius: '999px', padding: '9px 16px',
            fontSize: '13px', fontWeight: 700, color: 'var(--text-gray)',
            cursor: 'pointer', fontFamily: 'inherit',
          }}
        >
          <ArrowLeft size={14} /> 이전
        </motion.button>

        {/* Divider */}
        <div style={{ width: '1px', height: '20px', background: 'rgba(0,0,0,0.1)' }} />

        {/* Skip */}
        <motion.button
          onClick={onSkip}
          whileTap={{ scale: 0.95 }}
          style={{
            display: 'inline-flex', alignItems: 'center', gap: '5px',
            background: 'transparent', border: 'none',
            borderRadius: '999px', padding: '9px 14px',
            fontSize: '13px', fontWeight: 700, color: 'var(--text-gray)',
            cursor: 'pointer', fontFamily: 'inherit',
          }}
        >
          <SkipForward size={14} /> 건너뛰기
        </motion.button>

        {/* Results CTA */}
        <motion.button
          onClick={onNext}
          whileHover={{ scale: 1.04, y: -1 }}
          whileTap={{ scale: 0.97 }}
          style={{
            display: 'inline-flex', alignItems: 'center', gap: '7px',
            background: 'var(--btn-dark)', color: 'white',
            border: 'none', borderRadius: '999px', padding: '10px 22px',
            fontSize: '14px', fontWeight: 700, cursor: 'pointer',
            fontFamily: 'inherit', boxShadow: '0 4px 16px rgba(0,0,0,0.22)',
          }}
        >
          결과 보기 <ArrowRight size={14} />
        </motion.button>
      </motion.div>
      </div>
    </motion.div>
  );
};

export default GalleryStep;
