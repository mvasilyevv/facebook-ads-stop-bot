import { useEffect, useEffectEvent, useRef } from 'react';

export function useAsyncPolling(callback, { enabled, intervalMs, runImmediately = false, errorMultiplier = 3 }) {
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
    let hasError = false;
    let timeoutId = null;

    const scheduleNext = () => {
      const delay = hasError ? intervalMs * errorMultiplier : intervalMs;
      timeoutId = window.setTimeout(async () => {
        if (disposed) return;
        try {
          await run();
          hasError = false;
        } catch (e) {
          hasError = true;
        }
        if (!disposed) scheduleNext();
      }, delay);
    };

    if (runImmediately) {
      void run().then(() => {
        hasError = false;
        if (!disposed) scheduleNext();
      }).catch(() => {
        hasError = true;
        if (!disposed) scheduleNext();
      });
    } else {
      scheduleNext();
    }

    return () => {
      disposed = true;
      if (timeoutId !== null) {
        window.clearTimeout(timeoutId);
      }
    };
  }, [enabled, intervalMs, runImmediately, run, errorMultiplier]);
}
