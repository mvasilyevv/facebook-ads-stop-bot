/**
 * PageHeader — стандартный header страницы.
 *
 *   [Eyebrow 01 / OPERATE]
 *   [Title-large]                         [primary action]
 *   [subtitle / meta-info]
 *
 *   [DisplayNumber справа сверху]
 */

import { type ReactNode } from "react";
import { Eyebrow } from "./Eyebrow";

interface PageHeaderProps {
  eyebrowNum?: string;
  eyebrow: string;
  title: string;
  subtitle?: ReactNode;
  action?: ReactNode;
  /** Большой decorativeNumber в правом верхнем углу. */
  displayNumber?: string;
}

export function PageHeader({
  eyebrowNum,
  eyebrow,
  title,
  subtitle,
  action,
  displayNumber,
}: PageHeaderProps) {
  return (
    <header className="relative mb-10">
      {displayNumber ? (
        <div aria-hidden="true" className="display-number absolute right-0 top-5">
          {displayNumber}
        </div>
      ) : null}
      <Eyebrow num={eyebrowNum}>{eyebrow}</Eyebrow>
      <div className="flex items-end justify-between gap-8 mb-2 mt-3">
        <h1 className="page-title">{title}</h1>
        {action ? <div className="relative z-[1]">{action}</div> : null}
      </div>
      {subtitle ? (
        <div className="text-[13px] text-bg-10 font-display tracking-tight">{subtitle}</div>
      ) : null}
    </header>
  );
}

/** Inline-сепаратор для subtitle: `<span> · </span>` стиль. */
export function HeaderSep() {
  return (
    <span aria-hidden="true" className="text-bg-7 mx-2.5">
      ·
    </span>
  );
}
