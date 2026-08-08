/**
 * PageHeader — единый header operator-страниц.
 *
 * Структура:
 *   [Eyebrow "0N / РАЗДЕЛ"]
 *   [h1 — mono 30px, weight 500, ls -0.02em, БЕЗ точки]   [action-slot]
 *   [subtitle — 13px, separators]
 *
 * Без ghost-числа и trailing-точки — это рудимент старого макета,
 * текущая система их не использует.
 *
 * HeaderSep — разделитель · в subtitle.
 */

import { type ReactNode } from "react";
import { Eyebrow } from "@/components/data/Eyebrow";

// ─── PageHeader ───────────────────────────────────────────────────────────────

interface PageHeaderProps {
  eyebrowNum?: string;
  eyebrow: string;
  title: string;
  subtitle?: ReactNode;
  action?: ReactNode;
}

export function PageHeader({ eyebrowNum, eyebrow, title, subtitle, action }: PageHeaderProps) {
  return (
    <header className="mb-8">
      <Eyebrow num={eyebrowNum}>{eyebrow}</Eyebrow>

      {/* Title row — канон: 30px mono, weight 500, без точки */}
      <div className="mb-1.5 mt-2 flex flex-col items-start justify-between gap-4 sm:flex-row sm:items-end sm:gap-8">
        <h1
          className="font-display font-medium leading-[1.05] text-bg-11 m-0"
          style={{ fontSize: 30, letterSpacing: "-0.02em" }}
        >
          {title}
        </h1>
        {action ? <div className="relative z-[1] max-w-full shrink-0">{action}</div> : null}
      </div>

      {/* Subtitle */}
      {subtitle ? (
        <div className="text-[13px] text-bg-9 font-display tracking-tight">{subtitle}</div>
      ) : null}
    </header>
  );
}

/** Inline-разделитель для subtitle: пробел–точка–пробел. */
export function HeaderSep() {
  return (
    <span aria-hidden="true" className="text-bg-8 mx-2.5">
      ·
    </span>
  );
}
