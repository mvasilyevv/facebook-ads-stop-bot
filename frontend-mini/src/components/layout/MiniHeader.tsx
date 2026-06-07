/**
 * MiniHeader — компактная шапка страницы.
 * Eyebrow (маленькая метка) + title + опциональный правый слот.
 * Прилипает к верху контента (не fixed — TabBar уже fixed снизу).
 */
import type { ReactNode } from "react";
import { cn } from "@/lib/cn";

interface MiniHeaderProps {
  eyebrow?: string;
  title: string;
  /** Правый слот: badge, кнопка обновления и т.п. */
  right?: ReactNode;
  className?: string;
}

export function MiniHeader({ eyebrow, title, right, className }: MiniHeaderProps) {
  return (
    <header
      className={cn(
        "flex items-end justify-between gap-3",
        "px-4 pt-3 pb-3",
        "border-b border-[var(--color-bg-5)]",
        "bg-[var(--color-bg-0)]",
        className,
      )}
    >
      <div>
        {eyebrow && (
          <p className="text-[10px] uppercase tracking-[0.08em] text-[var(--color-bg-9)] font-mono mb-1 leading-none">
            {eyebrow}
          </p>
        )}
        <h1 className="text-[18px] font-display font-semibold text-[var(--color-bg-11)] leading-tight">
          {title}
        </h1>
      </div>
      {right && <div className="shrink-0">{right}</div>}
    </header>
  );
}
