/**
 * WorkerRow — строка воркера на Health-экране.
 * Dot ONLINE/OFFLINE + имя + TTL.
 * Локальный компонент.
 */
import type { HealthDetails } from "@fb/shared";
import { formatRelativeTime } from "@fb/shared";
import { cn } from "@/lib/cn";

export type WorkerStatus = HealthDetails["workers"][number];

const WORKER_LABELS: Record<string, string> = {
  observer: "Observer",
  telegram_poller: "Telegram Poller",
  meta_api: "Meta API Worker",
  enable_recommendation_worker: "Enable Recommendation",
  health_watchdog: "Health Watchdog",
  cleanup: "Cleanup Worker",
  reconciler: "Reconciler",
  digest_scheduler: "Digest Scheduler",
  creator: "Creator Worker",
  creator_recorder: "Creator Recorder",
  cabinet_scheduler: "Cabinet Scheduler",
  tracker_aggregator: "Tracker Aggregator",
};

interface WorkerRowProps {
  worker: WorkerStatus;
}

export function WorkerRow({ worker }: WorkerRowProps) {
  const online = worker.status === "ONLINE";
  return (
    <div className="flex items-center gap-3 py-3 border-b border-[var(--color-bg-5)] last:border-0">
      {/* Heartbeat dot */}
      <span
        className={cn(
          "shrink-0 w-2 h-2 rounded-full",
          online ? "bg-[var(--color-success)]" : "bg-[var(--color-danger)]",
        )}
        aria-label={online ? "онлайн" : "офлайн"}
      />

      {/* Имя + TTL */}
      <div className="flex-1 min-w-0">
        <p className="text-[14px] font-medium text-[var(--color-bg-11)] leading-tight">
          {WORKER_LABELS[worker.name] ?? worker.name}
        </p>
        <p className="text-[11px] text-[var(--color-bg-9)] font-mono">
          {online
            ? `heartbeat ${worker.ttl_seconds != null ? `${worker.ttl_seconds}s TTL` : ""}`
            : worker.last_heartbeat_at
            ? `послед. ${formatRelativeTime(worker.last_heartbeat_at)}`
            : "нет данных"}
        </p>
      </div>

      {/* Статус */}
      <span
        className={cn(
          "text-[11px] font-mono font-semibold",
          online ? "text-[var(--color-success)]" : "text-[var(--color-danger)]",
        )}
      >
        {online ? "ONLINE" : "OFFLINE"}
      </span>
    </div>
  );
}
