import React from 'react';

const StepProgress = ({ currentStep }) => {
  const steps = [
    { num: 1, label: '맛 탐색' },
    { num: 2, label: '이미지 매칭' },
    { num: 3, label: '상세 추천' }
  ];

  return (
    <div style={{ display: 'flex', justifyContent: 'center', alignItems: 'center', gap: '8px', padding: '20px 0', borderBottom: '1px solid var(--border-color)' }}>
      {steps.map((step, idx) => {
        const isActive = currentStep === step.num;
        const isPast = currentStep > step.num;
        
        let bgColor = 'var(--bg-color)';
        let color = 'var(--text-gray)';
        let borderColor = 'var(--border-color)';
        let fontWeight = '500';

        if (isActive) {
          bgColor = 'var(--primary)';
          color = 'white';
          borderColor = 'var(--primary)';
          fontWeight = '700';
        } else if (isPast) {
          bgColor = 'var(--primary-light)';
          color = 'var(--primary)';
          borderColor = 'var(--primary-light)';
        }

        return (
          <React.Fragment key={step.num}>
            <div style={{ display: 'flex', alignItems: 'center', gap: '6px' }}>
              <div style={{
                width: '28px', height: '28px', borderRadius: '50%',
                display: 'flex', alignItems: 'center', justifyContent: 'center',
                backgroundColor: bgColor, color, border: `1px solid ${borderColor}`,
                fontSize: '14px', fontWeight: 'bold'
              }}>
                {step.num}
              </div>
              <span style={{ fontSize: '14px', fontWeight, color: isActive ? 'var(--text-dark)' : 'var(--text-gray)' }}>
                {step.label}
              </span>
            </div>
            {idx < steps.length - 1 && (
              <div style={{ width: '20px', height: '1px', backgroundColor: 'var(--border-color)', margin: '0 4px' }} />
            )}
          </React.Fragment>
        );
      })}
    </div>
  );
};

export default StepProgress;
