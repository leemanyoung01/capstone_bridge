import React, { useState, useEffect } from 'react';
import './App.css';
import StepProgress from './components/ui/StepProgress';
import HomeStep from './components/steps/HomeStep';
import TasteStep from './components/steps/TasteStep';
import GalleryStep from './components/steps/GalleryStep';
import InferenceStep from './components/steps/InferenceStep';

function App() {
  const [step, setStep] = useState(0); // 0: Home, 1: Taste, 2: Gallery, 3: Inference
  const [isLoading, setIsLoading] = useState(true);
  
  // Data from API
  const [availableKeywords, setAvailableKeywords] = useState([]);
  const [defaultKeyword, setDefaultKeyword] = useState('');
  
  // User state
  const [currentKeyword, setCurrentKeyword] = useState('');
  const [config, setConfig] = useState(null);
  const [scores, setScores] = useState({});
  const [repImages, setRepImages] = useState([]);
  const [selectedImageIdx, setSelectedImageIdx] = useState([]);
  
  const [inferenceResults, setInferenceResults] = useState(null);
  const [inferenceError, setInferenceError] = useState(null);

  // 1. Initial Load: Check server health and get keywords
  useEffect(() => {
    fetch('/api/health')
      .then(res => res.json())
      .then(data => {
        const keywords = (data.keywords || []).filter(k => !String(k).endsWith('_multimodal'));
        setAvailableKeywords(keywords);
        setDefaultKeyword(data.default_keyword || keywords[0] || '');
        setIsLoading(false);
      })
      .catch(err => {
        console.error('Failed to load initial data:', err);
        setIsLoading(false);
      });
  }, []);

  // 2. Fetch config when keyword is selected
  const handleStart = async (keyword) => {
    try {
      const res = await fetch(`/api/config?keyword=${encodeURIComponent(keyword)}`);
      if (!res.ok) throw new Error('설정을 불러올 수 없습니다.');
      const data = await res.json();
      
      setConfig(data);
      setCurrentKeyword(keyword);
      
      // Initialize scores shape based on all axes
      let initialScores = {};
      const groups = data.groups || {};
      Object.keys(groups).forEach(g => {
        groups[g].forEach(ax => { initialScores[ax] = 0; });
      });
      (data.axes || []).forEach(ax => {
        if (initialScores[ax] === undefined) initialScores[ax] = 0;
      });
      
      setScores(initialScores);
      setStep(1);
    } catch (err) {
      alert(err.message);
    }
  };

  const handleChipClick = (axis) => {
    setScores(prev => ({
      ...prev,
      [axis]: (prev[axis] || 0) > 0 ? 0 : 5
    }));
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

  // 프론트 prefix 방어: image_src가 S3 URL이면 `/reviews/<keyword>/`
  // (raw 또는 URL-encoded) 가 포함된 이미지만 통과. `/reviews/`가 없는
  // 로컬·dev 데이터는 통과. 핵심 필터는 백엔드 담당, 여기는 이중 방어.
  const filterImagesByKeyword = (imgs, kw) => {
    if (!Array.isArray(imgs) || !kw) return imgs || [];
    const raw = `/reviews/${kw}/`;
    const enc = `/reviews/${encodeURIComponent(kw)}/`;
    return imgs.filter(img => {
      const src = img && img.image_src ? String(img.image_src) : '';
      if (!src) return true;
      if (!src.includes('/reviews/')) return true;
      return src.includes(raw) || src.includes(enc);
    });
  };

  const loadGallery = async () => {
    // Before moving to step 2, we must have at least one selection
    const total = Object.values(scores).reduce((a, b) => a + (parseInt(b) || 0), 0);
    if (total === 0) {
       alert('최소 1개 이상 키워드를 선택해주세요!');
       return;
    }

    setStep(2);
    try {
      const res = await fetch(`/api/representative_images?keyword=${encodeURIComponent(currentKeyword)}`);
      const data = await res.json();
      const raw = data.images || [];
      const cleaned = filterImagesByKeyword(raw, currentKeyword);
      if (raw.length !== cleaned.length) {
        console.warn(`[GalleryStep] keyword=${currentKeyword} prefix-mismatch dropped ${raw.length - cleaned.length} images`);
      }
      setRepImages(cleaned);
      setSelectedImageIdx([]);
    } catch (err) {
      console.error(err);
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
    
    const maxValue = 10;
    const userPrefs = {};
    Object.keys(scores).forEach(axis => {
      if (scores[axis] > 0) {
        userPrefs[axis] = Math.round((scores[axis] / maxValue) * 5 * 10) / 10;
      }
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
  };

  if (isLoading) {
    return <div style={{ display: 'flex', justifyContent: 'center', alignItems: 'center', height: '100vh', color: 'var(--text-gray)' }}>로딩 중...</div>;
  }

  return (
    <div className={`App${step === 0 ? ' home' : ''}`}>
      {step > 0 && <StepProgress currentStep={step} />}
      
      {step === 0 && (
         <HomeStep 
            availableKeywords={availableKeywords} 
            defaultKeyword={defaultKeyword} 
            onStart={handleStart} 
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
            onPrev={() => setStep(1)}
            onSkip={() => { setSelectedImageIdx([]); fetchRecommendations(false); }}
            onNext={() => fetchRecommendations(true)}
         />
      )}
      
      {step === 3 && (
         <InferenceStep 
            results={inferenceResults}
            error={inferenceError}
            onRestart={handleRestart}
         />
      )}
    </div>
  );
}

export default App;
