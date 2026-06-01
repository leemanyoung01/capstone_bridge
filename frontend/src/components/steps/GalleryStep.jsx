import React from 'react';
import { motion, AnimatePresence } from 'framer-motion';
import { ArrowLeft, ArrowRight, SkipForward, Check, Image } from 'lucide-react';

const IMAGE_AXIS_DISPLAY_LABELS = {
  커피: { 온도: '따뜻함' },
  김밥: { 밥과양: '밥과 양' },
  버거: { 번식감: '빵 식감', 패티육즙: '패티 육즙' },
};

const getImageAxisDisplayName = (keyword, axis) =>
  IMAGE_AXIS_DISPLAY_LABELS?.[keyword]?.[axis] || axis;

const GalleryStep = ({ keyword, images, selectedImageIdx, onImageToggle, onPrev, onSkip, onNext }) => {
  return (
    <div className="step-enter">
      {/* Header */}
      <div className="gallery-head">
        <div>
          <span className="eyebrow">Step 2</span>
          <h2 className="gallery-title display">
            분위기 <span className="muted" style={{ fontWeight: 500 }}>매칭</span>
          </h2>
          <p className="gallery-desc muted">
            끌리는 사진을 골라주세요
            <AnimatePresence>
              {selectedImageIdx.length > 0 && (
                <motion.span
                  key="count"
                  initial={{ opacity: 0, scale: 0.7 }}
                  animate={{ opacity: 1, scale: 1 }}
                  exit={{ opacity: 0, scale: 0.7 }}
                  className="badge-count"
                  style={{ marginLeft: 12 }}
                >
                  <Check size={13} /> {selectedImageIdx.length}장 선택됨
                </motion.span>
              )}
            </AnimatePresence>
          </p>
        </div>
      </div>

      {/* Grid */}
      {images.length === 0 ? (
        <div className="gallery-empty card">
          <Image size={48} style={{ color: 'var(--ink-3)' }} />
          <h3>아직 준비된 사진이 없어요!</h3>
          <p className="muted">건너뛰기를 눌러 텍스트만으로 추천을 받아보세요.</p>
        </div>
      ) : (
        <div className="gallery-grid">
          {images.map((img, idx) => {
            const isSelected = selectedImageIdx.includes(idx);
            const imgSrc = img.image_src
              ? (img.image_src.startsWith('images/') ? `/${img.image_src}` : img.image_src)
              : '';
            const caption = getImageAxisDisplayName(keyword, img.label || img.axis || '대표 이미지');

            return (
              <div
                key={idx}
                className={`gallery-cell ${isSelected ? 'on' : ''}`}
                style={{ animationDelay: `${idx * 0.04}s` }}
                onClick={() => onImageToggle(idx)}
              >
                {imgSrc ? (
                  <img src={imgSrc} alt={caption} />
                ) : (
                  <div className="gallery-cell-placeholder">🍽️</div>
                )}
                <div className="gallery-cell-caption">{caption}</div>
                <div className="gallery-check">
                  <Check size={16} />
                </div>
              </div>
            );
          })}
        </div>
      )}

      {/* Bottom nav */}
      <div className="gallery-nav">
        <button className="btn btn-ghost" onClick={onPrev} style={{ gap: 6, padding: '10px 18px' }}>
          <ArrowLeft size={15} /> 이전
        </button>

        <div className="gallery-nav-right">
          <button className="btn btn-ghost" onClick={onSkip} style={{ gap: 6, padding: '10px 18px' }}>
            <SkipForward size={15} /> 건너뛰기
          </button>
          <button className="btn btn-primary" onClick={onNext}>
            결과 보기
            {selectedImageIdx.length > 0 && (
              <span className="gallery-nav-badge">{selectedImageIdx.length}</span>
            )}
            <ArrowRight size={15} />
          </button>
        </div>
      </div>
    </div>
  );
};

export default GalleryStep;
