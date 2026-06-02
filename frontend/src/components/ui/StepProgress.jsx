import React from 'react';

const STEPS = [
  { num: 1, label: '맛 탐색' },
  { num: 2, label: '이미지 매칭' },
  { num: 3, label: '상세 추천' },
];

const StepProgress = ({ currentStep }) => {
  return (
    <div className="progress">
      {STEPS.map((step, idx) => {
        const isActive = currentStep === step.num;
        const isDone = currentStep > step.num;
        const cls = isActive ? 'active' : isDone ? 'done' : '';
        return (
          <React.Fragment key={step.num}>
            <div className={`progress-step ${cls}`}>
              <div className="progress-dot num">
                {isDone ? '✓' : step.num}
              </div>
              <span className="progress-label">{step.label}</span>
            </div>
            {idx < STEPS.length - 1 && <div className="progress-line" />}
          </React.Fragment>
        );
      })}
    </div>
  );
};

export default StepProgress;
