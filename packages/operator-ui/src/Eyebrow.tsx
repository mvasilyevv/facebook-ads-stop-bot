/**
 * Eyebrow — мелкий uppercase mono-лейбл, опционально нумерованный.
 *
 * Номер группы отображается приглушённым акцентом перед лейблом.
 * Внешние отступы задаёт родитель.
 *
 * Единый источник для обоих фронтов (раньше был файлом-близнецом в
 * frontend/src/components/data и frontend-mini/src/components/data).
 * Отдельно от layout/PageHeader-специфичных обёрток каждого фронта.
 */

import { type CSSProperties, type ReactNode } from "react";

export interface EyebrowProps {
  /** Номер группы ("01", "02"…), рендерится в accent-muted. */
  num?: string;
  children: ReactNode;
  className?: string;
  style?: CSSProperties;
}

export function Eyebrow({ num, children, className, style }: EyebrowProps) {
  const classes = [
    "inline-flex items-center gap-1.5",
    "font-display text-[12px] font-semibold uppercase tracking-[0.12em] text-bg-9",
    className,
  ]
    .filter(Boolean)
    .join(" ");
  return (
    <span className={classes} style={style}>
      {num ? (
        <>
          <span className="text-accent-muted tabular-nums">{num}</span>
          <span className="text-bg-8">/</span>
        </>
      ) : null}
      {children}
    </span>
  );
}
