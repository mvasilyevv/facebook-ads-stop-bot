/**
 * PageHeader — стандартный header страницы.
 *
 * Структура (из handoff-макета):
 *   [Eyebrow "01 OPERATE"]
 *   [page-title 56px trailing "."]      [action-slot]
 *   [subtitle — live-dot · separators]
 *   [displayNumber — 100px ghost absolute right]
 *
 * SectionTitleRow — eyebrow + 22px title + right action.
 * HeaderSep — разделитель · в subtitle.
 */

import { type ReactNode } from "react";
import { Eyebrow } from "./Eyebrow";

// ─── PageHeader ───────────────────────────────────────────────────────────────

interface PageHeaderProps {
  eyebrowNum?: string;
  eyebrow: string;
  title: string;
  /** Точка ставится автоматически — не добавляй в title. */
  trailingDot?: boolean;
  subtitle?: ReactNode;
  action?: ReactNode;
  /** Большое ghost-число absolute-positioned справа (100px). */
  displayNumber?: string;
}

export function PageHeader({
  eyebrowNum,
  eyebrow,
  title,
  trailingDot = true,
  subtitle,
  action,
  displayNumber,
}: PageHeaderProps) {
  return (
    <header className="relative mb-10">
      {/* Ghost display number */}
      {displayNumber ? (
        <div
          aria-hidden="true"
          className="absolute right-0 top-5 font-display text-[100px] font-medium leading-none text-bg-2 select-none pointer-events-none"
        >
          {displayNumber}
        </div>
      ) : null}

      <Eyebrow num={eyebrowNum}>{eyebrow}</Eyebrow>

      {/* Title row */}
      <div className="flex items-end justify-between gap-8 mb-2 mt-3">
        <h1 className="font-display text-[56px] font-medium leading-[0.95] tracking-[-0.03em] text-bg-11 m-0">
          {title}
          {trailingDot && (
            <span aria-hidden="true" className="text-bg-7">
              .
            </span>
          )}
        </h1>
        {action ? <div className="relative z-[1] shrink-0">{action}</div> : null}
      </div>

      {/* Subtitle */}
      {subtitle ? (
        <div className="text-[13px] text-bg-10 font-display tracking-tight">{subtitle}</div>
      ) : null}
    </header>
  );
}

/** Inline-разделитель для subtitle: пробел–точка–пробел. */
export function HeaderSep() {
  return (
    <span aria-hidden="true" className="text-bg-7 mx-2.5">
      ·
    </span>
  );
}

/** Live-dot для subtitle — пульсирующий зелёный индикатор. */
export function LiveDot() {
  return (
    <span
      aria-hidden="true"
      className="inline-block size-[6px] rounded-full bg-success mr-1.5 align-[1px] animate-pulse"
    />
  );
}

// ─── SectionTitleRow ──────────────────────────────────────────────────────────

interface SectionTitleRowProps {
  eyebrowNum?: string;
  eyebrow: string;
  title: string;
  action?: ReactNode;
  className?: string;
}

export function SectionTitleRow({
  eyebrowNum,
  eyebrow,
  title,
  action,
  className,
}: SectionTitleRowProps) {
  return (
    <div className={className}>
      <Eyebrow num={eyebrowNum}>{eyebrow}</Eyebrow>
      <div className="flex items-center justify-between gap-4">
        <h2 className="font-display text-[22px] font-medium leading-[1.15] tracking-[-0.02em] text-bg-11 m-0">
          {title}
        </h2>
        {action ? <div className="shrink-0">{action}</div> : null}
      </div>
    </div>
  );
}
