/**
 * PageHeader — стандартный header страницы (канон design_handoff/templates.jsx).
 *
 * Структура (PageHead из templates.jsx — единый для всех страниц):
 *   [Eyebrow "0N / РАЗДЕЛ"]
 *   [h1 — mono 30px, weight 500, ls -0.02em, БЕЗ точки]   [action-slot]
 *   [subtitle — 13px, separators]
 *
 * Без ghost-числа и trailing-точки — это рудимент старого макета,
 * канон их запрещает (см. README design_handoff).
 *
 * SectionTitleRow — eyebrow + 22px title + right action.
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

export function PageHeader({
  eyebrowNum,
  eyebrow,
  title,
  subtitle,
  action,
}: PageHeaderProps) {
  return (
    <header className="mb-8">
      <Eyebrow num={eyebrowNum}>{eyebrow}</Eyebrow>

      {/* Title row — канон: 30px mono, weight 500, без точки */}
      <div className="flex items-end justify-between gap-8 mb-1.5 mt-2">
        <h1
          className="font-display font-medium leading-[1.05] text-bg-11 m-0"
          style={{ fontSize: 30, letterSpacing: "-0.02em" }}
        >
          {title}
        </h1>
        {action ? <div className="relative z-[1] shrink-0">{action}</div> : null}
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
      <div className="flex items-center justify-between gap-4 mt-2">
        <h2 className="font-display text-[22px] font-medium leading-[1.15] tracking-[-0.02em] text-bg-11 m-0">
          {title}
        </h2>
        {action ? <div className="shrink-0">{action}</div> : null}
      </div>
    </div>
  );
}
