/**
 * HealthTab — статус воркеров из Redis heartbeat.
 * Список воркеров (ONLINE/OFFLINE) + общий вердикт HEALTHY/DEGRADED/CRITICAL.
 * Автообновление каждые 30 секунд.
 */

import { type FC } from "react";
import { Card } from "@/components/ui/Card";
import { Badge } from "@/components/ui/Badge";
import { Button } from "@/components/ui/Button";
import { Skeleton } from "@/components/ui/Skeleton";
import { ErrorState } from "@/components/ui/ErrorState";
import { useHealthDetails, useObserverStatus } from "@/lib/api/settings";
import { formatRelativeTime, formatDuration } from "@fb/shared";
import { RefreshCw } from "lucide-react";

/** Цвет вердикта → Badge variant. */
function verdictVariant(v: string): "success" | "warning" | "stop" {
  if (v === "HEALTHY") return "success";
  if (v === "DEGRADED") return "warning";
  return "stop";
}

/** Читаемое имя воркера. */
function workerLabel(name: string): string {
  const labels: Record<string, string> = {
    observer: "Observer",
    meta_api: "Meta API Worker",
    telegram_poller: "Telegram Poller",
    cleanup: "Cleanup Worker",
    reconciler: "Reconciler",
    enable: "Enable Worker",
    disable: "Disable Worker",
    creator: "Creator Worker",
    creator_recorder: "Creator Recorder",
    cabinet_scheduler: "Cabinet Scheduler",
    digest_scheduler: "Digest Scheduler",
    tracker_aggregator: "Tracker Aggregator",
    health_watchdog: "Health Watchdog",
    // Ключ — РЕАЛЬНОЕ heartbeat-имя воркера (enable_recommendation_worker пишет
    // worker:heartbeat:enable_reco). Раньше был 'enable_recommendation' → не матчился,
    // юзер видел сырое 'enable_reco' (F2).
    enable_reco: "Enable Recommendation",
  };
  return labels[name] ?? name;
}

export const HealthTab: FC = () => {
  const { data, isLoading, error, refetch, isFetching } = useHealthDetails();
  const observerQ = useObserverStatus();
  const obs = observerQ.data;

  if (isLoading) {
    return (
      <div className="space-y-3 max-w-xl">
        {Array.from({ length: 6 }).map((_, i) => (
          <Skeleton key={i} className="h-10 w-full" />
        ))}
      </div>
    );
  }

  if (error) {
    return <ErrorState error={error} onRetry={() => void refetch()} />;
  }

  const verdict = data?.overall ?? "CRITICAL";
  const workers = data?.workers ?? [];

  const onlineCount = workers.filter((w) => w.status === "ONLINE").length;
  const offlineCount = workers.filter((w) => w.status === "OFFLINE").length;

  return (
    <div className="space-y-5 max-w-xl">
      {/* Общий вердикт */}
      <Card eyebrow="Состояние системы" padded>
        <div className="flex items-center justify-between">
          <div>
            <Badge
              variant={verdictVariant(verdict)}
              size="md"
              className="text-[12px]"
            >
              {verdict}
            </Badge>
            <div className="mt-2 text-[11px] text-bg-9 font-display">
              {onlineCount} из {workers.length} воркеров в сети
              {offlineCount > 0 && (
                <span className="text-danger ml-2">· {offlineCount} недоступны</span>
              )}
            </div>
          </div>

          <Button
            size="icon"
            variant="ghost"
            onClick={() => void refetch()}
            aria-label="Обновить статус"
            disabled={isFetching}
          >
            <RefreshCw
              size={14}
              aria-hidden="true"
              className={isFetching ? "animate-spin" : ""}
            />
          </Button>
        </div>
      </Card>

      {/* Observer Runtime (раньше был отдельный таб Workers — слит сюда) */}
      <Card eyebrow="Observer Runtime" padded>
        <div className="space-y-2">
          <div className="flex items-center justify-between">
            <span className="text-[13px] text-bg-10">Статус</span>
            <Badge
              variant={
                obs?.status === "running"
                  ? "success"
                  : obs?.status === "paused"
                    ? "warning"
                    : "neutral"
              }
              size="sm"
            >
              {obs?.status ?? "unknown"}
            </Badge>
          </div>
          {obs?.last_scan_at && (
            <div className="flex items-center justify-between">
              <span className="text-[13px] text-bg-10">Последний скан</span>
              <span className="font-display text-[12px] text-bg-9">
                {formatRelativeTime(obs.last_scan_at)}
              </span>
            </div>
          )}
          {obs?.interval_seconds && (
            <div className="flex items-center justify-between">
              <span className="text-[13px] text-bg-10">Интервал</span>
              <span className="font-display text-[12px] text-bg-9 tabular-nums">
                {formatDuration(obs.interval_seconds)}
              </span>
            </div>
          )}
        </div>
      </Card>

      {/* Список воркеров */}
      <Card eyebrow="Воркеры" padded={false}>
        <div role="list" aria-label="Список воркеров">
          {workers.length === 0 ? (
            <div className="p-6 text-[13px] text-bg-9 font-display text-center">
              Данных о воркерах нет
            </div>
          ) : (
            workers.map((w, idx) => (
              <div
                key={w.name}
                role="listitem"
                className={`flex items-center justify-between px-6 py-4 ${
                  idx < workers.length - 1 ? "border-b border-[var(--hairline)]" : ""
                }`}
              >
                {/* Имя воркера */}
                <div>
                  <div className="font-display text-[13px] text-bg-11">
                    {workerLabel(w.name)}
                  </div>
                  <div className="font-display text-[10px] text-bg-7 mt-0.5 tabular-nums">
                    {w.name}
                  </div>
                  {w.last_heartbeat_at && (
                    <div className="font-display text-[10px] text-bg-8 mt-0.5">
                      Последний heartbeat: {formatRelativeTime(w.last_heartbeat_at)}
                    </div>
                  )}
                </div>

                {/* Статус */}
                <Badge
                  variant={w.status === "ONLINE" ? "success" : "neutral"}
                  size="sm"
                  aria-label={`Воркер ${workerLabel(w.name)}: ${w.status}`}
                >
                  {w.status}
                </Badge>
              </div>
            ))
          )}
        </div>
      </Card>

      {/* Автообновление */}
      <div className="text-[11px] text-bg-7 font-display">
        Обновляется автоматически каждые 30 секунд.
      </div>
    </div>
  );
};
