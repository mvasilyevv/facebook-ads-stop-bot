/**
 * Badge — статусный бейдж для alert_state, task_status и кастомных вариантов.
 * Канон: pill (rounded-full), 10px mono uppercase, опц. dot (withDot).
 * Варианты совпадают с domain/badge.ts из @fb/shared.
 */
import type { AlertState, TaskStatus } from "@fb/shared";
import {
  alertStateToBadgeVariant,
  ALERT_STATE_LABELS,
  normalizeAlertState,
  normalizeTaskStatus,
  TASK_STATUS_LABELS,
  taskStatusToBadgeVariant,
} from "@fb/shared";
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
  normal:    "bg-bg-3 text-bg-9",
  warning:   "bg-warning-bg text-warning",
  stop:      "bg-danger-bg text-danger",
  claimed:   "bg-info-bg text-info",
  disabled:  "bg-bg-3 text-bg-8",
  pending:   "bg-accent-bg text-accent-muted",
  running:   "bg-info-bg text-info",
  done:      "bg-success-bg text-success",
  failed:    "bg-danger-bg text-danger",
  retrying:  "bg-warning-bg text-warning",
  cancelled: "bg-bg-3 text-bg-8",
  neutral:   "bg-bg-3 text-bg-10",
};

export type BadgeSize = "sm" | "md";

const SIZE_STYLES: Record<BadgeSize, string> = {
  sm: "h-[20px] px-2 text-[10px]",
  md: "h-[22px] px-2.5 text-[10px]",
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
        <span aria-hidden className="inline-block size-[6px] rounded-full bg-current" />
      )}
      {children}
    </span>
  );
}

// ─── Удобные фабрики ──────────────────────────────────────────────────────

/** Badge из alert_state (lowercase canonical или UPPERCASE из TMA-API). */
export function AlertStateBadge({
  state,
  size,
  withDot,
  className,
}: {
  state: string;
  size?: BadgeSize;
  withDot?: boolean;
  className?: string;
}) {
  const normalized = normalizeAlertState(state) as AlertState;
  const variant = alertStateToBadgeVariant(normalized);
  return (
    <Badge variant={variant} size={size} withDot={withDot} className={className}>
      {ALERT_STATE_LABELS[normalized]}
    </Badge>
  );
}

/** Badge из task_status (UPPERCASE canonical). */
export function TaskStatusBadge({
  status,
  size,
  className,
}: {
  status: string;
  size?: BadgeSize;
  className?: string;
}) {
  const normalized = normalizeTaskStatus(status) as TaskStatus;
  const variant = taskStatusToBadgeVariant(normalized);
  return (
    <Badge variant={variant} size={size} className={className}>
      {TASK_STATUS_LABELS[normalized]}
    </Badge>
  );
}
