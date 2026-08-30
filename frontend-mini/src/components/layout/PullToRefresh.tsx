/**
 * PullToRefresh — минимальная реализация pull-to-refresh на touch-событиях
 * (без сторонней библиотеки). Срабатывает только когда жест начат при
 * scrollTop страницы === 0 — иначе это обычный скролл контента вниз.
 *
 * Telegram по умолчанию перехватывает вертикальный свайп сверху как жест
 * закрытия приложения. Пока этот компонент смонтирован, мы вызываем
 * disableVerticalSwipes() (Bot API 7.7+, feature-detect) и одновременно
 * preventDefault() на touchmove — старые клиенты без этого API всё ещё
 * могут состязаться с нативным жестом, это известное ограничение.
 */
import {
  useEffect,
  useRef,
  useState,
  type PropsWithChildren,
} from "react";
import { RefreshCw } from "lucide-react";
import { disableVerticalSwipes, enableVerticalSwipes, haptic } from "@/lib/tg";
import { cn } from "@/lib/cn";

const PULL_THRESHOLD_PX = 64;
const MAX_PULL_PX = 96;
const RESISTANCE = 0.5;

interface PullToRefreshProps {
  onRefresh: () => Promise<unknown> | void;
}

function pageScrollTop(): number {
  return document.scrollingElement?.scrollTop ?? window.scrollY ?? 0;
}

export function PullToRefresh({
  onRefresh,
  children,
}: PropsWithChildren<PullToRefreshProps>) {
  const [pull, setPull] = useState(0);
  const [refreshing, setRefreshing] = useState(false);
  const pullRef = useRef(0);
  const refreshingRef = useRef(false);
  const armed = useRef(false);
  const startY = useRef<number | null>(null);
  const onRefreshRef = useRef(onRefresh);
  useEffect(() => {
    onRefreshRef.current = onRefresh;
  });

  useEffect(() => {
    disableVerticalSwipes();
    return () => enableVerticalSwipes();
  }, []);

  useEffect(() => {
    function setPullValue(value: number) {
      pullRef.current = value;
      setPull(value);
    }

    function handleTouchStart(event: TouchEvent) {
      if (refreshingRef.current) return;
      if (pageScrollTop() > 0) {
        armed.current = false;
        return;
      }
      startY.current = event.touches[0]?.clientY ?? null;
      armed.current = true;
    }

    function handleTouchMove(event: TouchEvent) {
      if (!armed.current || startY.current === null || refreshingRef.current) {
        return;
      }
      const currentY = event.touches[0]?.clientY ?? startY.current;
      const delta = currentY - startY.current;
      if (delta <= 0 || pageScrollTop() > 0) {
        armed.current = false;
        setPullValue(0);
        return;
      }
      // Пассивным listener нельзя — гасим нативный bounce/scroll на время жеста.
      event.preventDefault();
      setPullValue(Math.min(MAX_PULL_PX, delta * RESISTANCE));
    }

    function handleTouchEnd() {
      if (!armed.current) {
        setPullValue(0);
        return;
      }
      armed.current = false;
      startY.current = null;
      if (pullRef.current >= PULL_THRESHOLD_PX) {
        haptic.impact("light");
        refreshingRef.current = true;
        setRefreshing(true);
        setPullValue(PULL_THRESHOLD_PX);
        void Promise.resolve(onRefreshRef.current()).finally(() => {
          refreshingRef.current = false;
          setRefreshing(false);
          setPullValue(0);
        });
      } else {
        setPullValue(0);
      }
    }

    window.addEventListener("touchstart", handleTouchStart, { passive: true });
    window.addEventListener("touchmove", handleTouchMove, { passive: false });
    window.addEventListener("touchend", handleTouchEnd, { passive: true });
    window.addEventListener("touchcancel", handleTouchEnd, { passive: true });
    return () => {
      window.removeEventListener("touchstart", handleTouchStart);
      window.removeEventListener("touchmove", handleTouchMove);
      window.removeEventListener("touchend", handleTouchEnd);
      window.removeEventListener("touchcancel", handleTouchEnd);
    };
  }, []);

  const indicatorHeight = refreshing ? PULL_THRESHOLD_PX : pull;
  const spinProgress = Math.min(1, pull / PULL_THRESHOLD_PX);

  return (
    <div>
      <div
        aria-hidden="true"
        className="flex items-center justify-center overflow-hidden"
        style={{
          height: indicatorHeight,
          transition: refreshing || pull === 0 ? "height 150ms ease" : undefined,
        }}
      >
        {indicatorHeight > 0 ? (
          <RefreshCw
            size={20}
            className={cn(refreshing ? "animate-spin text-accent" : "text-bg-8")}
            style={
              refreshing
                ? undefined
                : { transform: `rotate(${spinProgress * 180}deg)` }
            }
          />
        ) : null}
      </div>
      <p role="status" aria-live="polite" className="sr-only">
        {refreshing ? "Обновляем снимок…" : ""}
      </p>
      {children}
    </div>
  );
}
