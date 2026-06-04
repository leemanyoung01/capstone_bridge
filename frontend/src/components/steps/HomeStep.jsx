import React, { useState } from 'react';
import { Search, ArrowRight } from 'lucide-react';

/* 히어로 사진 fallback (DB 이미지 없을 때) */
const HERO_FALLBACKS = [
  {
    src: 'https://images.unsplash.com/photo-1544025162-d76694265947?w=600&h=750&fit=crop&q=80',
    name: '화로 직화 삼겹살', tag: '불맛', className: 'art-a',
  },
  {
    src: 'https://images.unsplash.com/photo-1569718212165-3a8278d5f624?w=600&h=750&fit=crop&q=80',
    name: '진한 국물 라멘', tag: '감칠맛', className: 'art-b',
  },
  {
    src: 'https://images.unsplash.com/photo-1617196034183-421b4040ed20?w=600&h=750&fit=crop&q=80',
    name: '플레이팅 스시', tag: '비주얼', className: 'art-c',
  },
];

const normSrc = (src) => {
  if (!src) return '';
  if (src.startsWith('images/')) return `/${src}`;
  return src;
};

const HERO_RADIUS = 'clamp(18px, 2.5vw, 28px)';

const Photo = ({ src, name, tag, className = '', isPlaceholder = false, fallbackSrc = '' }) => {
  // 'primary' → (실패 시) 'fallback' → (또 실패 시) 'prep'
  const [stage, setStage] = useState('primary');

  const canFallback = fallbackSrc && fallbackSrc !== src;
  const activeSrc = stage === 'fallback' ? fallbackSrc : src;

  const handleError = () => {
    setStage(prev => (prev === 'primary' && canFallback ? 'fallback' : 'prep'));
  };

  // 준비중 표시: 로딩 전 임의 사진(isPlaceholder)이거나, 보여줄 이미지가 없거나, 모든 후보 로드 실패
  const showPrep = isPlaceholder || !activeSrc || stage === 'prep';

  return (
    <div
      className={`photo ${className}`}
      style={{ background: 'var(--bg-tint)', borderRadius: HERO_RADIUS, overflow: 'hidden' }}
    >
      {activeSrc && stage !== 'prep' && (
        <img
          src={normSrc(activeSrc)}
          alt={name}
          style={{
            position: 'absolute', inset: 0, width: '100%', height: '100%',
            objectFit: 'cover',
            borderRadius: HERO_RADIUS,   /* img에도 직접 적용해 클리핑 보장 */
            display: 'block',
          }}
          onError={handleError}
        />
      )}

      {showPrep ? (
        <div className="photo-prep">
          <span className="photo-prep-emoji" role="img" aria-label="요리 중">🍳</span>
          <span className="photo-prep-text">사진 준비중</span>
        </div>
      ) : (
        <>
          <div className="photo-stripes" style={{ borderRadius: HERO_RADIUS }} />
          <div className="photo-shade" />
          <div className="photo-label">
            <span className="photo-tag">{tag}</span>
            <span className="photo-name display">{name}</span>
          </div>
        </>
      )}
    </div>
  );
};

/* ── Category Data ───────────────────────────────────────── */
const CATEGORIES = [
  { id: 'meat',    label: '🥩 고기',      emoji: '🥩' },
  { id: 'korean',  label: '🍚 밥/한식',   emoji: '🍚' },
  { id: 'noodle',  label: '🍜 면요리',    emoji: '🍜' },
  { id: 'seafood', label: '🍣 일식/해물', emoji: '🍣' },
  { id: 'chinese', label: '🥡 중식',      emoji: '🥡' },
  { id: 'western', label: '🍕 양식',      emoji: '🍕' },
  { id: 'snack',   label: '🥟 분식/야식', emoji: '🥟' },
];

const MENU_ITEMS = [
  { name: '곱창',       categories: ['meat'] },
  { name: '닭갈비',     categories: ['meat'] },
  { name: '삼겹살',     categories: ['meat'] },
  { name: '소고기',     categories: ['meat'] },
  { name: '족발',       categories: ['meat'] },
  { name: '김치찌개',   categories: ['korean'] },
  { name: '덮밥',       categories: ['korean'] },
  { name: '뼈해장국',   categories: ['korean'] },
  { name: '순대국',     categories: ['korean'] },
  { name: '청국장',     categories: ['korean'] },
  { name: '콩나물국밥', categories: ['korean'] },
  { name: '마라탕',     categories: ['noodle', 'chinese'] },
  { name: '소바',       categories: ['noodle'] },
  { name: '우동',       categories: ['noodle'] },
  { name: '짜장면',     categories: ['noodle', 'chinese'] },
  { name: '짬뽕',       categories: ['noodle', 'chinese'] },
  { name: '칼국수',     categories: ['noodle'] },
  { name: '파스타',     categories: ['noodle'] },
  { name: '돈까스',     categories: ['seafood'] },
  { name: '스시',       categories: ['seafood'] },
  { name: '아구찜',     categories: ['korean', 'seafood'] },
  { name: '조개',       categories: ['seafood'] },
  { name: '회',         categories: ['seafood'] },
  { name: '버거',       categories: ['western'] },
  { name: '샐러드',     categories: ['western'] },
  { name: '스테이크',   categories: ['western'] },
  { name: '피자',       categories: ['western'] },
  { name: '김밥',       categories: ['snack'] },
  { name: '딤섬',       categories: ['snack', 'chinese'] },
  { name: '떡볶이',     categories: ['snack'] },
  { name: '치킨',       categories: ['snack'] },
  { name: '커피',       categories: ['snack'] },
  { name: '훠궈',       categories: ['snack', 'chinese'] },
];

const itemsForCategory = (catId) =>
  MENU_ITEMS.filter(m => m.categories.includes(catId)).map(m => m.name);

const HomeStep = ({ availableKeywords, defaultKeyword, onStart, heroImages = [] }) => {
  const [query, setQuery] = useState('');
  const [activeCat, setActiveCat] = useState(CATEGORIES[0].id);

  // DB 이미지가 있으면 사용, 없으면 fallback(로딩 전 임의 사진 + 준비중)
  // DB 이미지가 깨지면 Photo 내부에서 fallbackSrc(unsplash)로 자동 교체
  const displayPhotos = HERO_FALLBACKS.map((fb, i) => {
    const dbImg = heroImages[i];
    if (!dbImg?.image_src) {
      return { ...fb, fallbackSrc: fb.src, isPlaceholder: true };
    }
    return {
      src: dbImg.image_src,
      name: fb.name,
      tag: dbImg.label || dbImg.axis || fb.tag,
      className: fb.className,
      fallbackSrc: fb.src,
      isPlaceholder: false,
    };
  });

  const handleSubmit = (e) => {
    if (e?.preventDefault) e.preventDefault();
    if (!query.trim()) return alert('음식명을 입력해주세요!');
    onStart(query.trim());
  };

  const cat = CATEGORIES.find(c => c.id === activeCat) || CATEGORIES[0];
  const items = itemsForCategory(activeCat);

  return (
    <div className="home step-enter">

      {/* ---- HERO ---- */}
      <section className="home-hero">
        <div className="home-hero-copy">
          <h1 className="home-title display reveal" style={{ animationDelay: '.06s' }}>
            <span className="home-title-lead">오늘, 당신의 입맛에</span><br />
            <span className="accentline">
              <span className="emph-dot">꼭</span> 맞는 한 곳
            </span>
          </h1>
          <p className="home-sub muted reveal" style={{ animationDelay: '.12s' }}>
            먹고 싶은 음식과 취향을 알려주세요.<br className="br-m" />
            리뷰와 사진을 분석해 <b>나만의 맛집 Top 5</b>를 골라드려요.
          </p>

          <form className="home-search reveal" onSubmit={handleSubmit} style={{ animationDelay: '.18s' }}>
            <Search size={20} className="home-search-ico" />
            <input
              list="kw-list"
              value={query}
              onChange={e => setQuery(e.target.value)}
              placeholder="스시, 삼겹살, 마라탕…"
              aria-label="음식 이름 입력"
            />
            <datalist id="kw-list">
              {availableKeywords.map(k => <option key={k} value={k} />)}
            </datalist>
            <button type="submit" className="btn btn-primary home-cta" disabled={!query.trim()}>
              시작 <ArrowRight size={18} />
            </button>
          </form>

        </div>

        <div className="home-hero-art reveal" style={{ animationDelay: '.1s' }}>
          {displayPhotos.map(p => (
            <Photo key={`${p.className}|${p.src || ''}`} {...p} />
          ))}
        </div>
      </section>

      {/* ---- 카테고리 ---- */}
      <section className="home-cats reveal" style={{ animationDelay: '.2s' }}>
        <div className="home-cats-head">
          <h2 className="home-cats-title display">무엇이 당겨요?</h2>
          <span className="muted home-cats-hint">카테고리를 고르고 메뉴를 눌러 바로 시작</span>
        </div>

        <div className="cat-tabs" role="tablist">
          {CATEGORIES.map(c => (
            <button
              key={c.id}
              role="tab"
              aria-selected={activeCat === c.id}
              className={'cat-tab ' + (activeCat === c.id ? 'on' : '')}
              onClick={() => setActiveCat(c.id)}
            >
              {c.label}
            </button>
          ))}
        </div>

        <div className="cat-items" key={activeCat}>
          {items.map((item, i) => (
            <button
              key={item}
              className="cat-item"
              style={{ animationDelay: `${i * 0.03}s` }}
              onClick={() => onStart(item)}
            >
              <span>{item}</span>
              <ArrowRight size={15} className="cat-item-arrow" />
            </button>
          ))}
        </div>
      </section>
    </div>
  );
};

export default HomeStep;
