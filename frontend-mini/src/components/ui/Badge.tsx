/**
 * Badge — статусный бейдж для alert_state, task_status и кастомных вариантов.
 * Варианты совпадают с domain/badge.ts из @fb/shared.
 * Размер: compact (inline текст), размер шрифта 11px.
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
  normal:    "bg-[var(--color-bg-3)] text-[var(--color-bg-9)]",
  warning:   "bg-[var(--color-warning-bg)] text-[var(--color-warning)]",
  stop:      "bg-[var(--color-danger-bg)] text-[var(--color-danger)]",
  claimed:   "bg-[var(--color-info-bg)] text-[var(--color-info)]",
  disabled:  "bg-[var(--color-bg-3)] text-[var(--color-bg-8)]",
  pending:   "bg-[var(--color-accent-bg)] text-[var(--color-accent-muted)]",
  running:   "bg-[var(--color-info-bg)] text-[var(--color-info)]",
  done:      "bg-[var(--color-success-bg)] text-[var(--color-success)]",
  failed:    "bg-[var(--color-danger-bg)] text-[var(--color-danger)]",
  retrying:  "bg-[var(--color-warning-bg)] text-[var(--color-warning)]",
  cancelled: "bg-[var(--color-bg-3)] text-[var(--color-bg-8)]",
  neutral:   "bg-[var(--color-bg-3)] text-[var(--color-bg-10)]",
};

interface BadgeProps {
  variant?: BadgeVariant;
  children: React.ReactNode;
  className?: string;
}

export function Badge({ variant = "neutral", children, className }: BadgeProps) {
  return (
    <span
      className={cn(
        "inline-flex items-center font-mono text-[11px] font-medium leading-none",
        "px-[6px] py-[3px] uppercase tracking-wide",
        VARIANT_STYLES[variant],
        className,
      )}
    >
      {children}
    </span>
  );
}

// ─── Удобные фабрики ──────────────────────────────────────────────────────

/** Badge из alert_state (lowercase canonical или UPPERCASE из TMA-API). */
export function AlertStateBadge({ state, className }: { state: string; className?: string }) {
  const normalized = normalizeAlertState(state) as AlertState;
  const variant = alertStateToBadgeVariant(normalized);
  return (
    <Badge variant={variant} className={className}>
      {ALERT_STATE_LABELS[normalized]}
    </Badge>
  );
}

/** Badge из task_status (UPPERCASE canonical). */
export function TaskStatusBadge({ status, className }: { status: string; className?: string }) {
  const normalized = normalizeTaskStatus(status) as TaskStatus;
  const variant = taskStatusToBadgeVariant(normalized);
  return (
    <Badge variant={variant} className={className}>
      {TASK_STATUS_LABELS[normalized]}
    </Badge>
  );
}
