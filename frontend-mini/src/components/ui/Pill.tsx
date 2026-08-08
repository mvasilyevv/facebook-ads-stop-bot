/**
 * Pill — маленький тег/метка (код правила, offer_code, тег кампании).
 * Меньше Badge — нет uppercase, нет tracking.
 */
import { cn } from "@/lib/cn";

export type PillVariant = "default" | "stop" | "warning" | "info" | "accent";

const PILL_STYLES: Record<PillVariant, string> = {
  default: "bg-[var(--color-bg-3)] text-[var(--color-bg-10)]",
  stop:    "bg-[var(--color-danger-bg)] text-[var(--color-danger)]",
  warning: "bg-[var(--color-warning-bg)] text-[var(--color-warning)]",
  info:    "bg-[var(--color-info-bg)] text-[var(--color-info)]",
  accent:  "bg-[var(--color-accent-bg)] text-[var(--color-accent-muted)]",
};

interface PillProps {
  variant?: PillVariant;
  children: React.ReactNode;
  className?: string;
}

export function Pill({ variant = "default", children, className }: PillProps) {
  return (
    <span
      className={cn(
        "inline-block font-mono text-[12px] font-semibold px-[5px] py-[2px] leading-none rounded-[var(--radius-1)]",
        PILL_STYLES[variant],
        className,
      )}
    >
      {children}
    </span>
  );
}
