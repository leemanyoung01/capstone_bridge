import { useCallback, useState } from 'react';
import { AnimatePresence, motion } from 'framer-motion';
import { Share2, Link2, Check } from 'lucide-react';

const TOAST_MS = 2200;

// Build a per-card share URL — no hardcoded host so this works on
// localhost, preview deploys, and prod alike. The current SPA path is
// preserved so the link resolves to the running app; downstream code can
// read `?restaurant=` to render a recipient view.
const buildShareUrl = (item) => {
  const url = new URL(window.location.origin);
  url.pathname = window.location.pathname;
  if (item?.name) url.searchParams.set('restaurant', item.name);
  return url.toString();
};

const ShareButton = ({ item }) => {
  const [toast, setToast] = useState(null); // 'copied' | 'error' | null

  const flashToast = useCallback((kind) => {
    setToast(kind);
    window.setTimeout(() => setToast(null), TOAST_MS);
  }, []);

  const handleShare = useCallback(async () => {
    const shareUrl = buildShareUrl(item);
    const name = item?.name || '오늘의 추천';
    const shareText = `오늘의 추천: ${name}`;
    const shareTitle = '나만의 맛 스니펫';

    // Case A — native share sheet (mobile, some desktops)
    if (typeof navigator !== 'undefined' && typeof navigator.share === 'function') {
      try {
        await navigator.share({ title: shareTitle, text: shareText, url: shareUrl });
        return;
      } catch (err) {
        // User-cancelled share is not an error.
        if (err && err.name === 'AbortError') return;
        // Other failures fall through to the clipboard fallback.
      }
    }

    // Case B — clipboard fallback (desktop, unsupported browsers)
    try {
      if (navigator.clipboard && navigator.clipboard.writeText) {
        await navigator.clipboard.writeText(shareUrl);
      } else {
        // Last-resort fallback for non-secure contexts.
        const ta = document.createElement('textarea');
        ta.value = shareUrl;
        ta.style.position = 'fixed';
        ta.style.opacity = '0';
        document.body.appendChild(ta);
        ta.select();
        document.execCommand('copy');
        document.body.removeChild(ta);
      }
      flashToast('copied');
    } catch {
      flashToast('error');
    }
  }, [item, flashToast]);

  return (
    <>
      <motion.button
        type="button"
        onClick={handleShare}
        whileHover={{ y: -1 }}
        whileTap={{ scale: 0.96 }}
        aria-label={item?.name ? `${item.name} 공유하기` : '결과 공유하기'}
        style={{
          // Mirrors the Naver-place link pill so they read as a button group.
          display: 'inline-flex', alignItems: 'center', gap: '6px',
          fontSize: '12px', fontWeight: 700, color: 'var(--text-dark)',
          background: 'var(--white)',
          border: '1.5px solid var(--border-color)',
          borderRadius: '999px', padding: '7px 14px',
          cursor: 'pointer', fontFamily: 'inherit',
          lineHeight: 1,
        }}
      >
        <Share2 size={12} /> 공유하기
      </motion.button>

      <AnimatePresence>
        {toast && (
          <motion.div
            key={toast}
            role="status"
            aria-live="polite"
            initial={{ opacity: 0, y: 24 }}
            animate={{ opacity: 1, y: 0 }}
            exit={{ opacity: 0, y: 24 }}
            transition={{ duration: 0.25, ease: 'easeOut' }}
            style={{
              position: 'fixed',
              left: '50%',
              bottom: '32px',
              transform: 'translateX(-50%)',
              display: 'inline-flex', alignItems: 'center', gap: '8px',
              background: 'rgba(26,26,26,0.94)', color: 'white',
              padding: '12px 20px', borderRadius: '999px',
              fontSize: '14px', fontWeight: 700,
              boxShadow: '0 10px 28px rgba(0,0,0,0.22)',
              zIndex: 1000,
              maxWidth: 'calc(100vw - 32px)',
              whiteSpace: 'nowrap',
              pointerEvents: 'none',
            }}
          >
            {toast === 'copied' ? (
              <>
                <Check size={15} /> 링크가 클립보드에 복사되었습니다!
              </>
            ) : (
              <>
                <Link2 size={15} /> 복사에 실패했어요. 주소창에서 직접 복사해 주세요.
              </>
            )}
          </motion.div>
        )}
      </AnimatePresence>
    </>
  );
};

export default ShareButton;
