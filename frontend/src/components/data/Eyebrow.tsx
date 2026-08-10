/**
 * Eyebrow — мелкий uppercase mono-лейбл, опционально нумерованный.
 *
 * Номер группы отображается приглушённым акцентом перед лейблом.
 * Внешние отступы задаёт родитель.
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
        "font-display text-[12px] font-semibold uppercase tracking-[0.12em] text-bg-9",
        className,
      )}
      style={style}
    >
      {num ? (
        <>
          <span className="text-accent-muted">{num}</span>
          <span className="text-bg-8">/</span>
        </>
      ) : null}
      {children}
    </span>
  );
}
