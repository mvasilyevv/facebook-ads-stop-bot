import { useEffect, useEffectEvent, useRef } from 'react';

export function useAsyncPolling(callback, { enabled, intervalMs, runImmediately = false }) {
  const isRunningRef = useRef(false);
  const run = useEffectEvent(async () => {
    if (isRunningRef.current) return;
    isRunningRef.current = true;
    try {
      await callback();
    } finally {
      isRunningRef.current = false;
    }
  });

  useEffect(() => {
    if (!enabled || !intervalMs) return undefined;

    let disposed = false;
    const tick = async () => {
      if (disposed) return;
      await run();
    };

    if (runImmediately) {
      void tick();
    }

    const timerId = window.setInterval(() => {
      void tick();
    }, intervalMs);

    return () => {
      disposed = true;
      window.clearInterval(timerId);
    };
  }, [enabled, intervalMs, runImmediately, run]);
}
