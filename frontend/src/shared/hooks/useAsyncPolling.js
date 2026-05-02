import { useEffect, useEffectEvent, useRef } from 'react';

export function useAsyncPolling(callback, { enabled, intervalMs, runImmediately = false, errorMultiplier = 3 }) {
  const isRunningRef = useRef(false);
  // controllerRef хранит текущий AbortController для отмены активного запроса
  const controllerRef = useRef(null);

  const run = useEffectEvent(async () => {
    if (isRunningRef.current) return;
    isRunningRef.current = true;
    // Создаём новый контроллер для каждого вызова
    const controller = new AbortController();
    controllerRef.current = controller;
    try {
      await callback(controller.signal);
    } finally {
      isRunningRef.current = false;
      controllerRef.current = null;
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
          // Игнорируем отмену запроса при unmount
          if (e?.name === 'AbortError') return;
          hasError = true;
        }
        if (!disposed) scheduleNext();
      }, delay);
    };

    if (runImmediately) {
      void run().then(() => {
        hasError = false;
        if (!disposed) scheduleNext();
      }).catch((e) => {
        // Игнорируем отмену запроса при unmount
        if (e?.name === 'AbortError') return;
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
      // Отменяем текущий активный запрос при unmount
      if (controllerRef.current) {
        controllerRef.current.abort();
        controllerRef.current = null;
      }
    };
  }, [enabled, intervalMs, runImmediately, run, errorMultiplier]);
}
