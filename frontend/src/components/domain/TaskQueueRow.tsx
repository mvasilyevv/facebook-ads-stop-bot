/**
 * TaskQueueRow — строка в Disable / Enable queue списках на Dashboard.
 *   [icon] [ad name + age] [status badge] [attempts ×N/M]
 */

import { XCircle, Check, Play } from "lucide-react";
import { Badge } from "@/components/ui/Badge";
import { formatRelativeTime } from "@/lib/utils/format";
import { cn } from "@/lib/utils/cn";
import type { TaskQueueRow as TaskQueueRowData } from "@/lib/types/api";

interface TaskQueueRowProps {
  task: TaskQueueRowData;
}

function statusVariant(status: string): "warning" | "normal" | "success" | "stop" | "neutral" {
  switch (status) {
    case "RUNNING":
      return "warning";
    // Бэкенд (status_mapper.to_frontend_task_status) присылает SUCCEEDED;
    // DONE оставлен для обратной совместимости.
    case "DONE":
    case "SUCCEEDED":
      return "success";
    case "FAILED":
    case "CANCELLED":
      return "stop";
    case "RETRYING":
      return "warning";
    default:
      return "normal";
  }
}

/** Человекочитаемый лейбл статуса (бэкенд SUCCEEDED → "done" в духе мока). */
function statusLabel(status: string): string {
  if (status === "SUCCEEDED" || status === "DONE") return "done";
  return status.toLowerCase();
}

export function TaskQueueRow({ task }: TaskQueueRowProps) {
  const isDone = task.status === "DONE" || task.status === "SUCCEEDED";
  const isEnable = task.task_type === "enable";
  return (
    <div
      className={cn(
        "grid grid-cols-[24px_1fr_auto_auto] gap-3.5 items-center py-3",
        "border-b border-bg-3 last:border-b-0",
      )}
    >
      <div
        aria-hidden="true"
        className={cn(
          "size-6 border flex items-center justify-center",
          isDone
            ? "text-success border-[rgba(126,180,122,0.3)]"
            : isEnable
              ? "text-success border-[rgba(126,180,122,0.3)] bg-bg-3"
              : "text-danger border-[rgba(199,98,92,0.3)] bg-bg-3",
        )}
      >
        {isDone ? <Check size={12} /> : isEnable ? <Play size={12} /> : <XCircle size={12} />}
      </div>
      <div className="font-display text-[13px] text-bg-11 truncate tracking-tight">
        {task.ad_name ?? task.fb_ad_id ?? "—"}
        <span className="text-bg-9 text-[11px] ml-2">· {formatRelativeTime(task.created_at)}</span>
      </div>
      <Badge variant={statusVariant(task.status)}>{statusLabel(task.status)}</Badge>
      <span className="font-display text-[11px] text-bg-9 tracking-wider tabular-nums">
        <span className="text-bg-7">×</span>
        {task.attempt_count}/{task.max_attempts}
      </span>
    </div>
  );
}
