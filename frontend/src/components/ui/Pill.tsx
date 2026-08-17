/**
 * Pill — filter-pill (кликабельный тэг) + Chip (removable с ×).
 * Активный filter-pill получает акцентные border и text.
 */
import { type ButtonHTMLAttributes, type HTMLAttributes, type ReactNode } from "react";
import { X } from "lucide-react";
import { cn } from "@/lib/utils/cn";

// ─── FilterPill ────────────────────────────────────────────────────────────────

interface FilterPillProps extends ButtonHTMLAttributes<HTMLButtonElement> {
  active?: boolean;
  leftIcon?: ReactNode;
}

/** Кликабельный pill для строки фильтров. */
export function FilterPill({ children, active, leftIcon, className, ...rest }: FilterPillProps) {
  return (
    <button
      type="button"
      className={cn(
        "inline-flex min-h-11 items-center gap-1.5 px-3.5",
        "rounded-full border font-display text-[12px] uppercase tracking-wider",
        "transition-colors duration-[120ms] cursor-pointer",
        "focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-accent",
        active
          ? "border-accent text-accent bg-accent-bg"
          : "bg-bg-1 border-[var(--color-hairline-strong)] text-bg-10 hover:border-bg-7 hover:text-bg-11",
        className,
      )}
      aria-pressed={active}
      {...rest}
    >
      {leftIcon ? <span aria-hidden="true">{leftIcon}</span> : null}
      {children}
    </button>
  );
}

// ─── Chip ─────────────────────────────────────────────────────────────────────

interface ChipProps extends HTMLAttributes<HTMLSpanElement> {
  onRemove?: () => void;
}

/**
 * Chip — активный фильтр-тэг с кнопкой ×.
 * Спека: bg-accent-bg, border rgba(245,241,232,0.2), accent-цвет.
 */
export function Chip({ children, onRemove, className, ...rest }: ChipProps) {
  return (
    <span
      className={cn(
        "inline-flex items-center gap-1.5",
        "pl-2.5 pr-1 py-1",
        "rounded-full border",
        "bg-accent-bg border-[rgba(245,241,232,0.2)] text-accent",
        "font-display text-[12px] tracking-[0.02em]",
        className,
      )}
      {...rest}
    >
      {children}
      {onRemove ? (
        <button
          type="button"
          aria-label="Удалить"
          onClick={(e) => {
            e.stopPropagation();
            onRemove();
          }}
          className={cn(
            "inline-flex items-center justify-center",
            "size-[18px] rounded-full",
            "bg-[rgba(245,241,232,0.1)] text-accent",
            "hover:bg-[rgba(245,241,232,0.2)]",
            "transition-colors duration-[120ms]",
            "focus-visible:outline-2 focus-visible:outline-offset-1 focus-visible:outline-accent",
          )}
        >
          <X size={10} aria-hidden="true" />
        </button>
      ) : null}
    </span>
  );
}

// ─── Обычный Pill (Display) ────────────────────────────────────────────────────

interface PillProps extends HTMLAttributes<HTMLSpanElement> {
  leftIcon?: ReactNode;
}

/** Pill для display-лейблов (некликабельный, timeline и т.п.). */
export function Pill({ children, leftIcon, className, ...rest }: PillProps) {
  return (
    <span
      className={cn(
        "inline-block",
        "bg-bg-3 border border-[var(--color-hairline)] rounded-[var(--radius-1)]",
        "px-1.5 py-px",
        "font-display text-[12px] tracking-[0.04em] text-bg-10",
        className,
      )}
      {...rest}
    >
      {leftIcon ? (
        <span aria-hidden="true" className="mr-1">
          {leftIcon}
        </span>
      ) : null}
      {children}
    </span>
  );
}
