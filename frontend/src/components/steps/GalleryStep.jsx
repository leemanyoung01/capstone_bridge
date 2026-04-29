import React from 'react';
import { motion } from 'framer-motion';
import Button from '../ui/Button';

const GalleryStep = ({ images, selectedImageIdx, onImageToggle, onPrev, onSkip, onNext }) => {
  return (
    <motion.div initial={{ opacity: 0, x: 20 }} animate={{ opacity: 1, x: 0 }} style={{ padding: '20px' }}>
      <div style={{ textAlign: 'center', marginBottom: '30px' }}>
        <h2 style={{ fontSize: '24px', fontWeight: '800' }}>📸 분위기 매칭</h2>
        <p style={{ color: 'var(--text-gray)', fontSize: '14px', marginTop: '4px' }}>
          끌리는 사진을 골라주세요. 다중 선택도 가능합니다.
        </p>
      </div>

      {images.length === 0 ? (
        <div style={{ textAlign: 'center', padding: '60px 20px', color: 'var(--text-light)' }}>
          <div style={{ fontSize: '40px', marginBottom: '16px' }}>🍽️</div>
          <p>앗, 아직 준비된 사진이 없어요!</p>
          <p style={{ fontSize: '12px', marginTop: '8px' }}>건너뛰기를 눌러 텍스트만으로 추천을 받아보세요.</p>
        </div>
      ) : (
        <div style={{ 
          display: 'grid', gridTemplateColumns: 'repeat(auto-fill, minmax(180px, 1fr))', gap: '16px', marginBottom: '40px' 
        }}>
          {images.map((img, idx) => {
            const isSelected = selectedImageIdx.includes(idx);
            const imgSrc = img.image_src ? (img.image_src.startsWith('images/') ? `/${img.image_src}` : img.image_src) : '';
            return (
              <motion.div 
                key={idx}
                whileHover={{ y: -4 }}
                whileTap={{ scale: 0.98 }}
                onClick={() => onImageToggle(idx)}
                style={{
                  position: 'relative',
                  borderRadius: 'var(--radius-md)',
                  overflow: 'hidden',
                  cursor: 'pointer',
                  backgroundColor: 'var(--bg-color)',
                  border: `3px solid ${isSelected ? 'var(--primary)' : 'transparent'}`,
                  boxShadow: isSelected ? '0 0 0 4px rgba(255, 126, 103, 0.1)' : 'var(--shadow-sm)',
                  transition: 'border 0.2s, box-shadow 0.2s'
                }}
              >
                {imgSrc ? (
                  <img 
                    src={imgSrc} 
                    alt={img.label || '음식 사진'} 
                    style={{ width: '100%', aspectRatio: '1/1', objectFit: 'cover', display: 'block' }}
                  />
                ) : (
                  <div style={{ width: '100%', aspectRatio: '1/1', display: 'flex', alignItems: 'center', justifyContent: 'center', fontSize: '40px' }}>🍽️</div>
                )}
                {isSelected && (
                  <div style={{
                    position: 'absolute', top: '8px', right: '8px', background: 'var(--primary)', color: 'white',
                    width: '24px', height: '24px', borderRadius: '50%', display: 'flex', alignItems: 'center', justifyContent: 'center', fontWeight: 'bold'
                  }}>✓</div>
                )}
                <div style={{ padding: '12px' }}>
                  <div style={{ fontSize: '14px', fontWeight: '700', marginBottom: '4px' }}>{img.label || img.axis || '대표 이미지'}</div>
                  {img.restaurant && <div style={{ fontSize: '12px', color: 'var(--text-gray)' }}>{img.restaurant}</div>}
                </div>
              </motion.div>
            );
          })}
        </div>
      )}

      <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', borderTop: '1px solid var(--border-color)', paddingTop: '20px' }}>
        <Button variant="ghost" onClick={onPrev}>← 이전</Button>
        <div style={{ display: 'flex', gap: '12px' }}>
          <Button variant="secondary" onClick={onSkip}>건너뛰기</Button>
          <Button variant="primary" onClick={onNext}>결과 보기</Button>
        </div>
      </div>
    </motion.div>
  );
};

export default GalleryStep;
