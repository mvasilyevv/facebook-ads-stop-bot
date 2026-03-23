import { useEffect, useRef } from "react";

type ReloadHandler = (silent?: boolean) => Promise<void>;

type UseAutoRefreshOptions = {
  enabled?: boolean;
  intervalMs?: number;
  refreshOnFocus?: boolean;
};

export const DEFAULT_AUTO_REFRESH_INTERVAL_MS = 15_000;

export function useAutoRefresh(
  reload: ReloadHandler,
  {
    enabled = true,
    intervalMs = DEFAULT_AUTO_REFRESH_INTERVAL_MS,
    refreshOnFocus = true,
  }: UseAutoRefreshOptions = {},
): void {
  const reloadRef = useRef(reload);
  const inFlightRef = useRef(false);

  useEffect(() => {
    reloadRef.current = reload;
  }, [reload]);

  useEffect(() => {
    if (!enabled || intervalMs <= 0) {
      return;
    }

    let disposed = false;

    async function triggerRefresh() {
      if (disposed || inFlightRef.current || document.visibilityState === "hidden") {
        return;
      }

      inFlightRef.current = true;
      try {
        await reloadRef.current(true);
      } finally {
        inFlightRef.current = false;
      }
    }

    function handleFocusRefresh() {
      void triggerRefresh();
    }

    const timerId = window.setInterval(() => {
      void triggerRefresh();
    }, intervalMs);

    if (refreshOnFocus) {
      window.addEventListener("focus", handleFocusRefresh);
      document.addEventListener("visibilitychange", handleFocusRefresh);
    }

    return () => {
      disposed = true;
      window.clearInterval(timerId);
      if (refreshOnFocus) {
        window.removeEventListener("focus", handleFocusRefresh);
        document.removeEventListener("visibilitychange", handleFocusRefresh);
      }
    };
  }, [enabled, intervalMs, refreshOnFocus]);
}
