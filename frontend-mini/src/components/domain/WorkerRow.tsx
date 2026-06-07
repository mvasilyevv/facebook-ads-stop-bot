/**
 * WorkerRow — строка воркера на Health-экране.
 * PulseDot (ONLINE=success, OFFLINE=danger) + имя mono + relative time + Badge ONLINE/OFFLINE.
 * borderBottom border-bg-5, min-h 44px. Имена воркеров — технические, не переводятся.
 */
import type { HealthDetails } from "@fb/shared";
import { formatRelativeTime } from "@fb/shared";
import { PulseDot } from "@/components/data";
import { Badge } from "@/components/ui";

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

  const heartbeatInfo = online
    ? worker.ttl_seconds != null
      ? `TTL ${worker.ttl_seconds}s`
      : "онлайн"
    : worker.last_heartbeat_at
      ? `${formatRelativeTime(worker.last_heartbeat_at)} назад`
      : "нет данных";

  return (
    <div className="flex items-center gap-3 px-0 py-2.5 min-h-[44px] border-b border-bg-5 last:border-0">
      {/* Пульс-точка */}
      <PulseDot
        size={8}
        color={online ? "var(--success)" : "var(--danger)"}
        aria-label={online ? "онлайн" : "офлайн"}
      />

      {/* Имя + подзаголовок */}
      <div className="flex-1 min-w-0">
        <p className="font-display text-[13px] text-bg-11 leading-tight truncate">
          {WORKER_LABELS[worker.name] ?? worker.name}
        </p>
        <p className="font-display tabular-nums text-[11px] text-bg-9 mt-0.5">
          {heartbeatInfo}
        </p>
      </div>

      {/* Статус-бейдж */}
      <Badge
        variant={online ? "done" : "failed"}
        size="sm"
        withDot
      >
        {online ? "ONLINE" : "OFFLINE"}
      </Badge>
    </div>
  );
}
