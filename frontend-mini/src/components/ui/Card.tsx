/**
 * Card — блок-контейнер с опциональным eyebrow-заголовком.
 * Airy-вид: hairline-граница, скругление radius-3, фон bg-1.
 */
import type { HTMLAttributes, ReactNode } from "react";
import { cn } from "@/lib/cn";

interface CardProps extends HTMLAttributes<HTMLDivElement> {
  /** Eyebrow-маркер: верхняя метка над заголовком (маленькая, мутная). */
  eyebrow?: string;
  title?: string;
  /** Правый слот заголовка (badge, кнопка). */
  titleRight?: ReactNode;
  padding?: "none" | "sm" | "md";
}

export function Card({
  eyebrow,
  title,
  titleRight,
  padding = "md",
  className,
  children,
  ...rest
}: CardProps) {
  const paddingClass = padding === "none" ? "" : padding === "sm" ? "p-3" : "p-4";

  return (
    <div
      {...rest}
      className={cn(
        "bg-[var(--color-bg-1)] border border-[var(--color-hairline)] rounded-[var(--radius-3)]",
        paddingClass,
        className,
      )}
    >
      {(eyebrow || title || titleRight) && (
        <div className={cn("mb-3", (eyebrow || title) ? "" : "")}>
          {eyebrow && (
            <p className="text-[12px] uppercase tracking-[0.08em] text-[var(--color-bg-9)] mb-1">
              {eyebrow}
            </p>
          )}
          {(title || titleRight) && (
            <div className="flex items-center justify-between gap-2">
              {title && (
                <h2 className="text-[14px] font-semibold text-[var(--color-bg-11)] leading-tight font-display">
                  {title}
                </h2>
              )}
              {titleRight}
            </div>
          )}
        </div>
      )}
      {children}
    </div>
  );
}
