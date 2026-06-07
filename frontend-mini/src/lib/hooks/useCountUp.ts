/**
 * useCountUp — анимация числа 0 → target с cubic ease-out (~750ms) на mount.
 * Уважает prefers-reduced-motion (сразу target). Порт из web (единый канон).
 */
import { useEffect, useRef, useState } from "react";

const DEFAULT_DURATION = 750;

/**
 * Анимировать ли. Нет matchMedia (jsdom/SSR) или reduce-motion → нет анимации
 * (сразу финальное значение). В реальном браузере matchMedia всегда есть.
 */
function shouldAnimate(): boolean {
  if (typeof window === "undefined" || typeof window.matchMedia !== "function") return false;
  return !window.matchMedia("(prefers-reduced-motion: reduce)").matches;
}

/**
 * Возвращает текущее (анимируемое) значение, идущее от 0 к target.
 */
export function useCountUp(target: number, duration = DEFAULT_DURATION): number {
  const [value, setValue] = useState(0);
  const rafRef = useRef<number | null>(null);

  useEffect(() => {
    if (!shouldAnimate()) {
      setValue(target);
      return;
    }

    let start: number | null = null;
    const step = (t: number) => {
      if (start === null) start = t;
      const p = Math.min(1, (t - start) / duration);
      setValue(Math.round(target * (1 - Math.pow(1 - p, 3))));
      if (p < 1) rafRef.current = requestAnimationFrame(step);
    };

    rafRef.current = requestAnimationFrame(step);
    return () => {
      if (rafRef.current !== null) cancelAnimationFrame(rafRef.current);
    };
  }, [target, duration]);

  return value;
}
