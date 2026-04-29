import React, { useState } from 'react';
import { Search } from 'lucide-react';
import Button from '../ui/Button';
import { motion } from 'framer-motion';

const HomeStep = ({ availableKeywords, defaultKeyword, onStart }) => {
  const [query, setQuery] = useState(defaultKeyword || '');

  const handleSubmit = (e) => {
    e.preventDefault();
    if (!query.trim()) return alert('음식명을 입력해주세요!');
    if (!availableKeywords.includes(query.trim())) {
      return alert(`'${query}' 데이터가 없습니다.`);
    }
    onStart(query.trim());
  };

  return (
    <motion.div 
      initial={{ opacity: 0, y: 20 }} animate={{ opacity: 1, y: 0 }}
      style={{
        display: 'flex', flexDirection: 'column', alignItems: 'center',
        padding: '60px 20px', textAlign: 'center'
      }}
    >
      <h1 style={{ fontSize: '32px', fontWeight: '800', marginBottom: '12px' }}>
        🍽️ 나의 맛 취향 분석기
      </h1>
      <p style={{ fontSize: '15px', color: 'var(--text-gray)', marginBottom: '40px' }}>
        오늘 당신이 가장 끌리는 맛은 무엇인가요?
      </p>

      <form onSubmit={handleSubmit} style={{
        display: 'flex', width: '100%', maxWidth: '500px',
        backgroundColor: 'var(--white)', borderRadius: 'var(--radius-lg)',
        boxShadow: 'var(--shadow-md)', padding: '8px', 
        alignItems: 'center'
      }}>
        <Search size={20} color="var(--text-gray)" style={{ margin: '0 12px' }} />
        <input 
          type="text"
          value={query}
          onChange={(e) => setQuery(e.target.value)}
          placeholder="예: 삼겹살, 스시, 아구찜"
          list="keywords-list"
          style={{
            flex: 1, border: 'none', outline: 'none', 
            fontSize: '16px', padding: '12px 0', background: 'transparent'
          }}
        />
        <datalist id="keywords-list">
          {availableKeywords.map(kw => <option key={kw} value={kw} />)}
        </datalist>
        <Button type="submit" variant="primary" style={{ padding: '10px 20px', borderRadius: 'var(--radius-md)' }}>
          시작하기
        </Button>
      </form>
    </motion.div>
  );
};

export default HomeStep;
