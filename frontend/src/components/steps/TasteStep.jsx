import React from 'react';
import { AnimatePresence, motion } from 'framer-motion';
import { RotateCcw, ArrowRight, Sparkles } from 'lucide-react';
import RadarChartViz from '../ui/RadarChartViz';

const GROUP_PRIORITY = ['공통', '맛', '식감', '기타', '메타'];

const isSpecialtyGroup = (label) =>
  typeof label === 'string' && label.endsWith('특화');

const AXIS_DISPLAY_LABELS = {
  커피: { 온도: '따뜻함' },
  김밥: { 밥의간과양: '밥의 간과 양' },
  버거: { 번식감: '빵 식감', 패티육즙: '패티 육즙' },
};

const GROUP_DISPLAY_LABELS = {
  김밥특화: '김밥 특화',
  커피특화: '커피 특화',
  버거특화: '버거 특화',
};

const getAxisDisplayName = (keyword, axis, config) => {
  if (config?.axis_label_map?.[axis]) return config.axis_label_map[axis];
  return AXIS_DISPLAY_LABELS?.[keyword]?.[axis] || axis;
};

const getGroupDisplayName = (label) => GROUP_DISPLAY_LABELS[label] || label;

const TasteStep = ({ keyword, config, scores, onChipClick, onScoreChange, onNext, onReset }) => {
  let { groups = {} } = config;

  if (keyword === '뼈해장국' && groups['국밥탕특화']) {
    groups = { ...groups };
    delete groups['국밥탕특화'];
  }

  const used = new Set();
  const groupOrder = [];
  Object.keys(groups).forEach(label => {
    if (isSpecialtyGroup(label) && !used.has(label)) {
      groupOrder.push({ label, axes: groups[label] });
      used.add(label);
    }
  });
  GROUP_PRIORITY.forEach(label => {
    if (groups[label] && !used.has(label)) {
      groupOrder.push({ label, axes: groups[label] });
      used.add(label);
    }
  });
  Object.keys(groups).forEach(label => {
    if (!used.has(label)) {
      groupOrder.push({ label, axes: groups[label] });
      used.add(label);
    }
  });

  const hasGenericTaste = Boolean(groups['맛'] || groups['식감']);
  const headingSuffix = hasGenericTaste ? '맛 취향 탐색' : '취향 탐색';

  const selectedAxisKeys = Object.keys(scores).filter(ax => (scores[ax] || 0) > 0);
  const radarData = selectedAxisKeys.reduce((acc, ax) => { acc[ax] = scores[ax] || 0; return acc; }, {});
  const radarLabelMap = selectedAxisKeys.reduce((acc, ax) => {
    acc[ax] = getAxisDisplayName(keyword, ax, config);
    return acc;
  }, {});
  const selectedCount = Object.values(scores).filter(v => v > 0).length;

  return (
    <div className="step-enter">
      {/* Header */}
      <div className="taste-head">
        <div>
          <span className="eyebrow">Step 1</span>
          <h2 className="taste-title display">
            <span className="taste-kw">{keyword}</span>{' '}
            <span className="muted" style={{ fontWeight: 500 }}>{headingSuffix}</span>
          </h2>
          <p className="taste-desc muted">
            끌리는 단어를 자유롭게 선택해주세요
            <AnimatePresence>
              {selectedCount > 0 && (
                <motion.span
                  key="badge"
                  initial={{ opacity: 0, scale: 0.7 }}
                  animate={{ opacity: 1, scale: 1 }}
                  exit={{ opacity: 0, scale: 0.7 }}
                  className="badge-count"
                  style={{ marginLeft: 12 }}
                >
                  <Sparkles size={13} /> {selectedCount}개 선택됨
                </motion.span>
              )}
            </AnimatePresence>
          </p>
        </div>
      </div>

      {/* Two-column body */}
      <div className="taste-body">
        {/* Left: chip groups */}
        <div>
          {groupOrder.map((group, gIdx) => {
            const isLast = gIdx === groupOrder.length - 1;
            return (
              <div
                key={group.label}
                className="taste-group"
                style={{ borderBottom: isLast ? 'none' : 'var(--bw) solid var(--line)', padding: '20px 0 28px' }}
              >
                <div className="taste-group-label">
                  <span>{getGroupDisplayName(group.label)}</span>
                </div>
                <div className="taste-chips">
                  {group.axes.map(axis => {
                    const score = scores[axis];
                    const isSelected = score !== undefined && score !== 0;
                    return (
                      <div
                        key={axis}
                        className={`taste-chip ${isSelected ? 'on' : ''}`}
                      >
                        <button
                          className="taste-chip-main"
                          onClick={() => onChipClick(axis)}
                        >
                          {getAxisDisplayName(keyword, axis, config)}
                        </button>
                        {isSelected && (
                          <div
                            className="taste-stepper"
                            onClick={e => e.stopPropagation()}
                          >
                            <button
                              onClick={() => {
                                const val = (parseInt(score) || 0) - 1;
                                onScoreChange(axis, val <= 0 ? 0 : val);
                              }}
                            >−</button>
                            <input
                              type="number"
                              className="num"
                              value={score}
                              onChange={e => onScoreChange(axis, e.target.value)}
                              onBlur={e => {
                                const val = parseInt(e.target.value);
                                if (isNaN(val) || val < 1) onScoreChange(axis, 1);
                                else if (val > 10) onScoreChange(axis, 10);
                              }}
                            />
                            <button
                              onClick={() => onScoreChange(axis, (parseInt(score) || 0) + 1)}
                              disabled={(parseInt(score) || 0) >= 10}
                            >+</button>
                          </div>
                        )}
                      </div>
                    );
                  })}
                </div>
              </div>
            );
          })}
        </div>

        {/* Right: sticky radar panel */}
        <div className="taste-panel">
          <div className="card taste-radar-card">
            <div className="taste-radar-head">
              <span className="eyebrow">나의 취향 프로필</span>
              {selectedCount > 0 && (
                <span className="taste-radar-count num">{selectedCount}</span>
              )}
            </div>

            {selectedCount === 0 ? (
              <div className="taste-radar-empty">
                <div className="taste-radar-ghost">
                  <Sparkles size={28} />
                </div>
                <p className="muted">취향 키워드를 선택하면<br />그래프가 나타나요</p>
              </div>
            ) : (
              <RadarChartViz
                data={radarData}
                axes={selectedAxisKeys}
                labelMap={radarLabelMap}
              />
            )}

            <div className="taste-actions">
              <button className="btn btn-ghost" onClick={onReset} style={{ flex: 1 }}>
                <RotateCcw size={15} /> 초기화
              </button>
              <button className="btn btn-primary" onClick={onNext} style={{ flex: 2 }}>
                다음 단계 <ArrowRight size={16} />
              </button>
            </div>
          </div>
        </div>
      </div>
    </div>
  );
};

export default TasteStep;
