/**
 * Eyebrow (canon) — мелкий uppercase mono-лейбл, опционально нумерованный.
 *
 * Канон design_handoff: 10px, weight 600, letter-spacing 0.08–0.12em, uppercase.
 * Номер группы — в accent-muted, затем «/», затем сам лейбл (как в прототипе
 * components.jsx). Без внешнего margin — раскладку задаёт родитель.
 *
 * Отдельно от layout/Eyebrow (тот завязан на PageHeader других страниц).
 */

import { type CSSProperties, type ReactNode } from "react";
import { cn } from "@/lib/utils/cn";

interface EyebrowProps {
  /** Номер группы ("01", "02"…), рендерится в accent-muted. */
  num?: string;
  children: ReactNode;
  className?: string;
  style?: CSSProperties;
}

export function Eyebrow({ num, children, className, style }: EyebrowProps) {
  return (
    <span
      className={cn(
        "inline-flex items-center gap-1.5",
        "font-display text-[10px] font-semibold uppercase tracking-[0.12em] text-bg-9",
        className,
      )}
      style={style}
    >
      {num ? (
        <>
          <span className="text-accent-muted">{num}</span>
          <span className="text-bg-7">/</span>
        </>
      ) : null}
      {children}
    </span>
  );
}
