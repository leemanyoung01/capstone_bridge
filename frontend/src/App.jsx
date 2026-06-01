import React, { useState, useEffect } from 'react';
import './App.css';
import { Utensils } from 'lucide-react';
import StepProgress from './components/ui/StepProgress';
import HomeStep from './components/steps/HomeStep';
import TasteStep from './components/steps/TasteStep';
import GalleryStep from './components/steps/GalleryStep';
import InferenceStep from './components/steps/InferenceStep';
import EvalDashboard from './components/ui/EvalDashboard';

function TopBar({ onHome }) {
  return (
    <header className="topbar">
      <div className="topbar-inner">
        <div className="brand" onClick={onHome}>
          <span className="brand-mark"><Utensils size={19} /></span>
          <span className="brand-name">Bridge<span className="accent">.</span></span>
        </div>
      </div>
    </header>
  );
}

function App() {
  // Pop 방향 · Vermilion 팔레트 · 라이트 모드 고정
  useEffect(() => {
    document.documentElement.setAttribute('data-dir',     'pop');
    document.documentElement.setAttribute('data-palette', 'vermilion');
    document.documentElement.setAttribute('data-mode',    'light');
  }, []);

  const [step, setStep] = useState(0);
  const [isLoading, setIsLoading] = useState(true);

  const [hash, setHash] = useState(typeof window !== 'undefined' ? window.location.hash : '');
  useEffect(() => {
    const onHash = () => setHash(window.location.hash);
    window.addEventListener('hashchange', onHash);
    return () => window.removeEventListener('hashchange', onHash);
  }, []);

  const [availableKeywords, setAvailableKeywords] = useState([]);
  const [defaultKeyword, setDefaultKeyword] = useState('');
  const [heroImages, setHeroImages] = useState([]);
  const [currentKeyword, setCurrentKeyword] = useState('');
  const [config, setConfig] = useState(null);
  const [scores, setScores] = useState({});
  const [repImages, setRepImages] = useState([]);
  const [selectedImageIdx, setSelectedImageIdx] = useState([]);
  const [inferenceResults, setInferenceResults] = useState(null);
  const [inferenceError, setInferenceError] = useState(null);

  useEffect(() => {
    fetch('/api/health')
      .then(res => res.json())
      .then(data => {
        const keywords = (data.keywords || []).filter(k => !String(k).endsWith('_multimodal'));
        setAvailableKeywords(keywords);
        setDefaultKeyword(data.default_keyword || keywords[0] || '');
        setIsLoading(false);
        // 히어로 사진: 키워드별 대표 이미지 1장씩 수집
        Promise.allSettled(
          keywords.slice(0, 6).map(kw =>
            fetch(`/api/representative_images?keyword=${encodeURIComponent(kw)}`)
              .then(r => r.json())
              .then(d => (d.images || [])[0])
          )
        ).then(results => {
          const imgs = results
            .filter(r => r.status === 'fulfilled' && r.value?.image_src)
            .map(r => r.value);
          if (imgs.length > 0)
            setHeroImages([0, 1, 2].map(i => imgs[i % imgs.length]));
        }).catch(() => {});
      })
      .catch(() => setIsLoading(false));
  }, []);

  const handleStart = async (keyword) => {
    try {
      const res = await fetch(`/api/config?keyword=${encodeURIComponent(keyword)}`);
      if (!res.ok) {
        const errData = await res.json().catch(() => null);
        throw new Error(errData?.error || '설정을 불러올 수 없습니다.');
      }
      const data = await res.json();
      setConfig(data);
      setCurrentKeyword(data.keyword || keyword);
      const initialScores = {};
      const groups = data.groups || {};
      Object.keys(groups).forEach(g => { groups[g].forEach(ax => { initialScores[ax] = 0; }); });
      (data.axes || []).forEach(ax => { if (initialScores[ax] === undefined) initialScores[ax] = 0; });
      setScores(initialScores);
      setStep(1);
      window.scrollTo({ top: 0 });
    } catch (err) {
      alert(err.message);
    }
  };

  const handleChipClick = (axis) => {
    setScores(prev => ({ ...prev, [axis]: (prev[axis] || 0) > 0 ? 0 : 5 }));
  };

  const handleScoreChange = (axis, value) => {
    setScores(prev => ({
      ...prev,
      [axis]: value === '' ? '' : Math.max(0, Math.min(10, parseInt(value) || 0))
    }));
  };

  const resetScores = () => {
    const fresh = {};
    Object.keys(scores).forEach(k => fresh[k] = 0);
    setScores(fresh);
  };

  const filterImagesByKeyword = (imgs, kw) => {
    if (!Array.isArray(imgs) || !kw) return imgs || [];
    const raw = `/reviews/${kw}/`;
    const enc = `/reviews/${encodeURIComponent(kw)}/`;
    return imgs.filter(img => {
      const src = img?.image_src ? String(img.image_src) : '';
      if (!src || !src.includes('/reviews/')) return true;
      return src.includes(raw) || src.includes(enc);
    });
  };

  const loadGallery = async () => {
    const total = Object.values(scores).reduce((a, b) => a + (parseInt(b) || 0), 0);
    if (total === 0) { alert('최소 1개 이상 키워드를 선택해주세요!'); return; }
    setStep(2);
    window.scrollTo({ top: 0 });
    try {
      const res = await fetch(`/api/representative_images?keyword=${encodeURIComponent(currentKeyword)}`);
      const data = await res.json();
      setRepImages(filterImagesByKeyword(data.images || [], currentKeyword));
      setSelectedImageIdx([]);
    } catch {
      setRepImages([]);
    }
  };

  const toggleImageSelection = (idx) => {
    setSelectedImageIdx(prev =>
      prev.includes(idx) ? prev.filter(i => i !== idx) : [...prev, idx]
    );
  };

  const fetchRecommendations = async (useImageFusion = true) => {
    setStep(3);
    setInferenceResults(null);
    setInferenceError(null);
    window.scrollTo({ top: 0 });
    const userPrefs = {};
    Object.keys(scores).forEach(axis => {
      if (scores[axis] > 0) userPrefs[axis] = Math.round((scores[axis] / 10) * 5 * 10) / 10;
    });
    const selectedImages = useImageFusion
      ? selectedImageIdx.map(idx => ({
          index: idx,
          axis: repImages[idx]?.axis || '',
          clip_vector: repImages[idx]?.clip_vector || {}
        }))
      : [];
    try {
      const res = await fetch('/api/recommend', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          _keyword: currentKeyword,
          text_preferences: userPrefs,
          selected_images: selectedImages,
          use_image_fusion: selectedImages.length > 0
        })
      });
      const data = await res.json();
      if (data.error) throw new Error(data.error);
      setInferenceResults(data.results || []);
    } catch (err) {
      setInferenceError(err.message || '추천 중 오류가 발생했습니다.');
    }
  };

  const handleRestart = () => {
    setStep(0);
    setCurrentKeyword('');
    setConfig(null);
    setScores({});
    setSelectedImageIdx([]);
    setRepImages([]);
    setInferenceResults(null);
    window.scrollTo({ top: 0 });
  };

  if (hash === '#eval') return <EvalDashboard />;

  if (isLoading) {
    return (
      <div className="app-shell" style={{ justifyContent: 'center', alignItems: 'center' }}>
        <span className="muted eyebrow">로딩 중...</span>
      </div>
    );
  }

  return (
    <div className="app-shell">
      <TopBar onHome={handleRestart} />
      <main className="app-main">
        {step > 0 && <StepProgress currentStep={step} />}

        {step === 0 && (
          <HomeStep
            availableKeywords={availableKeywords}
            defaultKeyword={defaultKeyword}
            onStart={handleStart}
            heroImages={heroImages}
          />
        )}
        {step === 1 && config && (
          <TasteStep
            keyword={currentKeyword}
            config={config}
            scores={scores}
            onChipClick={handleChipClick}
            onScoreChange={handleScoreChange}
            onReset={resetScores}
            onNext={loadGallery}
          />
        )}
        {step === 2 && (
          <GalleryStep
            keyword={currentKeyword}
            images={repImages}
            selectedImageIdx={selectedImageIdx}
            onImageToggle={toggleImageSelection}
            onPrev={() => { setStep(1); window.scrollTo({ top: 0 }); }}
            onSkip={() => { setSelectedImageIdx([]); fetchRecommendations(false); }}
            onNext={() => fetchRecommendations(true)}
          />
        )}
        {step === 3 && (
          <InferenceStep
            results={inferenceResults}
            error={inferenceError}
            scores={scores}
            config={config}
            keyword={currentKeyword}
            onRestart={handleRestart}
          />
        )}
      </main>
    </div>
  );
}

export default App;
