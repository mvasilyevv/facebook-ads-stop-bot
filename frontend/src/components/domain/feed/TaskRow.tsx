/**
 * TaskRow — строка задачи в TaskQueueCard.
 *
 * Макет (dashboard.html):
 *   [icon danger✕/success✓/play ▶] [name+age] [status badge] [×n/5 attempts]
 *
 * icon: SUCCEEDED/DONE → ✓ success, enable → ▶ success, else → ✕ danger.
 * badge: через taskStatusToBadgeVariant из @fb/shared.
 */

import { XCircle, Check, Play } from "lucide-react";
import { Badge } from "@/components/ui/Badge";
import {
  taskStatusToBadgeVariant,
  TASK_STATUS_LABELS,
  taskTypeLabel,
  formatRelativeTime,
  normalizeTaskStatus,
} from "@fb/shared";
import type { TaskQueueRow as TaskQueueRowData } from "@fb/shared";
import { cn } from "@/lib/utils/cn";

interface TaskRowProps {
  task: TaskQueueRowData;
}

export function TaskRow({ task }: TaskRowProps) {
  const status = normalizeTaskStatus(task.status);
  const isDone = status === "SUCCEEDED";
  const isEnable = task.task_type === "enable" || task.task_type === "activate_ad";
  const badgeVariant = taskStatusToBadgeVariant(status);
  // SUCCEEDED → "done" label
  const statusLabel =
    status === "SUCCEEDED"
      ? (TASK_STATUS_LABELS["SUCCEEDED"] ?? "Выполнено")
      : (TASK_STATUS_LABELS[status] ?? task.status);
  const typeLabel = taskTypeLabel(task.task_type);

  return (
    <div
      className={cn(
        "grid items-center gap-3.5 py-3",
        "border-b border-bg-3 last:border-b-0",
      )}
      style={{ gridTemplateColumns: "24px 1fr auto auto" }}
    >
      {/* Icon */}
      <div
        role="img"
        aria-label={typeLabel}
        title={typeLabel}
        className={cn(
          "size-6 border flex items-center justify-center shrink-0",
          isDone
            ? "text-success border-[rgba(126,180,122,0.3)]"
            : isEnable
              ? "text-success border-[rgba(126,180,122,0.3)] bg-bg-3"
              : "text-danger border-[rgba(199,98,92,0.3)] bg-bg-3",
        )}
      >
        {isDone ? (
          <Check size={12} aria-hidden="true" />
        ) : isEnable ? (
          <Play size={12} aria-hidden="true" />
        ) : (
          <XCircle size={12} aria-hidden="true" />
        )}
      </div>

      {/* Ad name + age */}
      <div className="font-display text-[13px] text-bg-11 truncate tracking-tight min-w-0">
        {task.ad_name ?? task.fb_ad_id ?? "—"}
        <span className="text-bg-9 text-[11px] ml-2 tabular-nums">
          · {formatRelativeTime(task.created_at)}
        </span>
      </div>

      {/* Status badge */}
      <Badge variant={badgeVariant} size="sm">
        {statusLabel}
      </Badge>

      {/* Attempts counter */}
      <span
        className="font-display text-[11px] text-bg-9 tracking-wider tabular-nums shrink-0"
        title={`Попытка ${task.attempt_count} из ${task.max_attempts}`}
      >
        <span className="text-bg-7">×</span>
        {task.attempt_count}/{task.max_attempts}
      </span>
    </div>
  );
}
