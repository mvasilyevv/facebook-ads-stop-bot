/**
 * useCountUp — анимация числа 0 → target с cubic ease-out (~750ms) на mount.
 *
 * Портировано из design_handoff/dashboard-shared.jsx (useCountUp).
 * Уважает prefers-reduced-motion: при reduce сразу ставит target без анимации.
 * При смене target — перезапускает анимацию с текущего значения 0 (как в прототипе).
 */

import { useEffect, useRef, useState } from "react";

/** Длительность анимации по умолчанию (мс). */
const DEFAULT_DURATION = 750;

function prefersReducedMotion(): boolean {
  return (
    typeof window !== "undefined" &&
    typeof window.matchMedia === "function" &&
    window.matchMedia("(prefers-reduced-motion: reduce)").matches
  );
}

/**
 * Возвращает текущее (анимируемое) значение, идущее от 0 к target.
 * @param target конечное число
 * @param duration длительность анимации (мс)
 */
export function useCountUp(target: number, duration = DEFAULT_DURATION): number {
  const [value, setValue] = useState(0);
  const rafRef = useRef<number | null>(null);

  useEffect(() => {
    if (prefersReducedMotion()) {
      setValue(target);
      return;
    }

    let start: number | null = null;
    const step = (t: number) => {
      if (start === null) start = t;
      const p = Math.min(1, (t - start) / duration);
      // cubic ease-out: 1 - (1-p)^3
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
