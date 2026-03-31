import { useEffect, useEffectEvent } from 'react';

export function useRefreshOnResume(callback, enabled = true) {
  const onResume = useEffectEvent(() => {
    if (document.visibilityState === 'hidden') return;
    callback();
  });

  useEffect(() => {
    if (!enabled) return undefined;

    const handleResume = () => onResume();
    window.addEventListener('focus', handleResume);
    document.addEventListener('visibilitychange', handleResume);

    return () => {
      window.removeEventListener('focus', handleResume);
      document.removeEventListener('visibilitychange', handleResume);
    };
  }, [enabled, onResume]);
}
