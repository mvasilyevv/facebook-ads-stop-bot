/**
 * Presentational status badge. Domain states are rendered only by typed
 * operator view-models; this primitive never guesses or normalizes status.
 */
import { cn } from "@/lib/cn";

export type BadgeVariant =
  | "normal"
  | "warning"
  | "stop"
  | "claimed"
  | "disabled"
  | "pending"
  | "running"
  | "done"
  | "failed"
  | "retrying"
  | "cancelled"
  | "neutral";

const VARIANT_STYLES: Record<BadgeVariant, string> = {
  normal: "bg-bg-3 text-bg-9",
  warning: "bg-warning-bg text-warning",
  stop: "bg-danger-bg text-danger",
  claimed: "bg-info-bg text-info",
  disabled: "bg-bg-3 text-bg-8",
  pending: "bg-accent-bg text-accent-muted",
  running: "bg-info-bg text-info",
  done: "bg-success-bg text-success",
  failed: "bg-danger-bg text-danger",
  retrying: "bg-warning-bg text-warning",
  cancelled: "bg-bg-3 text-bg-8",
  neutral: "bg-bg-3 text-bg-10",
};

export type BadgeSize = "sm" | "md";

const SIZE_STYLES: Record<BadgeSize, string> = {
  sm: "h-6 px-2 text-[12px]",
  md: "h-7 px-2.5 text-[12px]",
};

interface BadgeProps {
  variant?: BadgeVariant;
  size?: BadgeSize;
  /** Показать точку-индикатор перед текстом. */
  withDot?: boolean;
  children: React.ReactNode;
  className?: string;
}

export function Badge({
  variant = "neutral",
  size = "md",
  withDot = false,
  children,
  className,
}: BadgeProps) {
  return (
    <span
      className={cn(
        "inline-flex items-center gap-1.5 rounded-full",
        "font-display font-medium leading-none uppercase tracking-wide",
        SIZE_STYLES[size],
        VARIANT_STYLES[variant],
        className,
      )}
    >
      {withDot && (
        <span
          aria-hidden
          className="inline-block size-[6px] rounded-full bg-current"
        />
      )}
      {children}
    </span>
  );
}
