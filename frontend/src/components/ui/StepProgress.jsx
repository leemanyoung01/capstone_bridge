import React from 'react';
import { motion } from 'framer-motion';

const STEPS = [
  { num: 1, label: '맛 탐색' },
  { num: 2, label: '이미지 매칭' },
  { num: 3, label: '상세 추천' },
];

const StepProgress = ({ currentStep }) => {
  return (
    <div style={{
      display: 'flex',
      justifyContent: 'center',
      alignItems: 'center',
      gap: '6px',
      padding: '18px 24px',
      borderBottom: '1px solid rgba(0,0,0,0.07)',
      background: 'transparent',
    }}>
      {STEPS.map((step, idx) => {
        const isActive = currentStep === step.num;
        const isPast = currentStep > step.num;
        return (
          <React.Fragment key={step.num}>
            <div style={{ display: 'flex', alignItems: 'center', gap: '7px' }}>
              <motion.div
                animate={{
                  background: isActive ? '#1A1A1A' : isPast ? '#D4EDD4' : 'rgba(0,0,0,0.08)',
                  scale: isActive ? 1.1 : 1,
                }}
                transition={{ duration: 0.25 }}
                style={{
                  width: '28px', height: '28px', borderRadius: '50%',
                  display: 'flex', alignItems: 'center', justifyContent: 'center',
                  fontSize: '13px', fontWeight: 900, flexShrink: 0,
                  color: isActive ? 'white' : isPast ? '#2E7D32' : '#888',
                }}
              >
                {isPast ? '✓' : step.num}
              </motion.div>
              <span style={{
                fontSize: '13px',
                fontWeight: isActive ? 800 : 500,
                color: isActive ? '#1A1A1A' : isPast ? '#555' : '#AAA',
                whiteSpace: 'nowrap',
              }}>
                {step.label}
              </span>
            </div>
            {idx < STEPS.length - 1 && (
              <div style={{
                width: '28px', height: '1.5px',
                background: currentStep > step.num ? '#1A1A1A' : 'rgba(0,0,0,0.12)',
                borderRadius: '2px',
                transition: 'background 0.3s',
              }} />
            )}
          </React.Fragment>
        );
      })}
    </div>
  );
};

export default StepProgress;
