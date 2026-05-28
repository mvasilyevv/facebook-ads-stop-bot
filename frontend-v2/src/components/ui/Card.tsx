/**
 * Card — карточка-контейнер с eyebrow/title/content/action.
 * Default: bg-1 + bg-5 border, radius-0.
 */

import { type HTMLAttributes, type ReactNode } from "react";
import { cn } from "@/lib/utils/cn";

interface CardProps extends Omit<HTMLAttributes<HTMLDivElement>, "title"> {
  /** Маленький uppercase-лейбл сверху "01 / OVERVIEW". */
  eyebrow?: ReactNode;
  /** Заголовок карточки. */
  title?: ReactNode;
  /** Доп. контент справа в header (например chart-tabs). */
  action?: ReactNode;
  /** Мета-информация рядом с title справа в header (например "12 open"). */
  meta?: ReactNode;
  /** Если false — снимает внутренние отступы (для таблиц). */
  padded?: boolean;
  /** Nested card — чуть темнее (для drill-down). */
  nested?: boolean;
}

export function Card({
  eyebrow,
  title,
  action,
  meta,
  padded = true,
  nested = false,
  className,
  children,
  ...rest
}: CardProps) {
  return (
    <div
      className={cn(
        "border border-bg-5",
        nested ? "bg-bg-2" : "bg-bg-1",
        padded ? "p-6" : "p-0",
        className,
      )}
      {...rest}
    >
      {(eyebrow || title || action || meta) && (
        <div className="flex items-baseline justify-between mb-5">
          <div>
            {eyebrow ? (
              <div className="eyebrow mb-1.5">{eyebrow}</div>
            ) : null}
            {title ? (
              <h3 className="text-[13px] font-display font-medium tracking-wider text-bg-11 m-0">
                {title}
              </h3>
            ) : null}
          </div>
          {action ? (
            <div>{action}</div>
          ) : meta ? (
            <span className="text-[11px] font-display text-bg-9 tracking-wider">{meta}</span>
          ) : null}
        </div>
      )}
      {children}
    </div>
  );
}
