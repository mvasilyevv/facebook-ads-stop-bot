/**
 * Card — контейнер с eyebrow/title/action/meta.
 * Направление A+C: bg-1 + hairline-граница + мягкий радиус (radius-3).
 */
import { type HTMLAttributes, type ReactNode } from "react";
import { cn } from "@/lib/utils/cn";

interface CardProps extends Omit<HTMLAttributes<HTMLDivElement>, "title"> {
  /** Маленький uppercase-лейбл (например "01 / OVERVIEW"). */
  eyebrow?: ReactNode;
  title?: ReactNode;
  /** Доп. контент справа в header (chart-tabs, actions). */
  action?: ReactNode;
  /** Мета-информация рядом с title ("12 open"). */
  meta?: ReactNode;
  /** false — снимает внутренние отступы (для таблиц). */
  padded?: boolean;
  /** Nested — чуть темнее. */
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
        "border border-[var(--color-hairline)] rounded-[var(--radius-3)]",
        nested ? "bg-bg-2" : "bg-bg-1",
        padded ? "p-6" : "p-0",
        className,
      )}
      {...rest}
    >
      {(eyebrow || title || action || meta) && (
        <div className={cn("flex items-baseline justify-between", padded ? "mb-5" : "p-6 pb-0")}>
          <div>
            {eyebrow ? (
              <div className="text-[12px] font-display tracking-[0.14em] uppercase text-bg-9 mb-1.5">
                {eyebrow}
              </div>
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
            <span className="text-[12px] font-display text-bg-9 tracking-wider">{meta}</span>
          ) : null}
        </div>
      )}
      {children}
    </div>
  );
}
