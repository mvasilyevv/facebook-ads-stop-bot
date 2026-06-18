/**
 * WorkersTab — сводка воркеров + observer status.
 * Использует useHealthDetails (те же данные, что Health-таб, но другой угол зрения).
 * Показывает последний scan-time, interval и runtime-payload observer'а.
 */

import { type FC } from "react";
import { Card } from "@/components/ui/Card";
import { Skeleton } from "@/components/ui/Skeleton";
import { ErrorState } from "@/components/ui/ErrorState";
import { Badge } from "@/components/ui/Badge";
import { useHealthDetails, useObserverStatus } from "@/lib/api/settings";
import { formatRelativeTime, formatDuration } from "@fb/shared";

export const WorkersTab: FC = () => {
  const healthQ = useHealthDetails();
  const observerQ = useObserverStatus();

  if (healthQ.isLoading || observerQ.isLoading) {
    return (
      <div className="space-y-3 max-w-xl">
        {Array.from({ length: 5 }).map((_, i) => (
          <Skeleton key={i} className="h-10 w-full" />
        ))}
      </div>
    );
  }

  if (healthQ.error) {
    return <ErrorState error={healthQ.error} onRetry={() => void healthQ.refetch()} />;
  }

  const obs = observerQ.data;
  const workers = healthQ.data?.workers ?? [];

  return (
    <div className="space-y-5 max-w-xl">
      {/* Observer runtime */}
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

      {/* Воркеры */}
      <Card eyebrow="Все воркеры" padded={false}>
        <div role="list">
          {workers.map((w, idx) => (
            <div
              key={w.name}
              role="listitem"
              className={`flex items-center justify-between px-6 py-3 ${
                idx < workers.length - 1 ? "border-b border-[var(--hairline)]" : ""
              }`}
            >
              <div className="font-display text-[13px] text-bg-11">{w.name}</div>
              <div className="flex items-center gap-3">
                {w.last_heartbeat_at && (
                  <span className="font-display text-[10px] text-bg-8 tabular-nums">
                    {formatRelativeTime(w.last_heartbeat_at)}
                  </span>
                )}
                <Badge
                  variant={w.status === "ONLINE" ? "success" : "neutral"}
                  size="sm"
                >
                  {w.status}
                </Badge>
              </div>
            </div>
          ))}
        </div>
      </Card>
    </div>
  );
};
