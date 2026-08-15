/**
 * PageHeader — единый header operator-страниц.
 *
 * Структура:
 *   [Eyebrow "0N / РАЗДЕЛ"]
 *   [h1 — mono 30px, weight 500, ls -0.02em, БЕЗ точки]   [action-slot]
 *   [subtitle — 13px, separators]
 *
 * Надзаголовок выводится из маршрута, а не печатается на экране руками:
 * номер и название раздела берутся из того же места, что и меню, поэтому
 * разойтись с ним не могут.
 *
 * Без ghost-числа и trailing-точки — это рудимент старого макета,
 * текущая система их не использует.
 *
 * HeaderSep — разделитель · в subtitle.
 */

import { type ReactNode } from "react";
import { Eyebrow } from "@/components/data/Eyebrow";
import { sectionForPath } from "@/lib/navigation";

// ─── PageHeader ───────────────────────────────────────────────────────────────

interface PageHeaderProps {
  title: string;
  subtitle?: ReactNode;
  action?: ReactNode;
  /**
   * Уточнение к разделу для экранов, где заголовок называет запись:
   * «GH_AVI» под «РЕКЛАМА · ОФФЕРЫ». Раздел и его номер задаёт маршрут.
   */
  detail?: string;
}

/**
 * Надзаголовок раздела для экранов с собственной вёрсткой шапки.
 * Ничего не рисует за пределами продуктовых маршрутов.
 *
 * Адрес берётся из `location`, а не из состояния роутера: раздел — это то,
 * где оператор находится, и знать об этом через контекст навигации незачем.
 * Экран перерисовывается при переходе, потому что переход его и монтирует.
 */
export function SectionEyebrow({ detail, className }: { detail?: string; className?: string }) {
  const pathname = typeof window === "undefined" ? "/" : window.location.pathname;
  const section = sectionForPath(pathname);
  const text = section
    ? [section.name, section.crumb, detail].filter(Boolean).join(" · ")
    : (detail ?? "");

  if (!text) return null;

  return (
    <Eyebrow num={section?.num} className={className}>
      {text}
    </Eyebrow>
  );
}

export function PageHeader({ title, subtitle, action, detail }: PageHeaderProps) {
  return (
    <header className="mb-8">
      <SectionEyebrow detail={detail} />

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
