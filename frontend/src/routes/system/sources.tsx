import { createFileRoute } from "@tanstack/react-router";
import { RefreshCw } from "lucide-react";

import { useOperatorRealtimeStatus } from "@fb/operator-api";
import { OperatorSectionFrame } from "@fb/operator-ui";
import type { OperatorSeverity, OperatorWorkerState } from "@fb/shared/operator/contracts";
import {
  severityForDataState,
  snapshotForRealtimeState,
  workerStatusLabel,
} from "@fb/shared/operator/viewModel";

import { PageHeader } from "@/components/layout/PageHeader";
import { Button } from "@/components/ui/Button";
import { OperatorUnavailableState } from "@/components/layout/OperatorPageBoundary";
import { Skeleton } from "@/components/ui/Skeleton";
import { operatorProblemMessage, useOperatorSnapshot } from "@/lib/api/operator";

export const Route = createFileRoute("/system/sources")({
  component: OperatorSystemSourcesPage,
});

const SEVERITY_COLOR: Record<OperatorSeverity, string> = {
  ok: "var(--color-success)",
  warning: "var(--color-warning)",
  critical: "var(--color-danger)",
  unknown: "var(--color-bg-8)",
};

function OperatorSystemSourcesPage() {
  const realtimeStatus = useOperatorRealtimeStatus();
  const snapshotQuery = useOperatorSnapshot({ window: "today" });

  if (snapshotQuery.isLoading && !snapshotQuery.data) {
    return (
      <div>
        <PageHeader title="Источники и воркеры" />
        <div className="grid gap-4" aria-busy="true">
          <Skeleton className="h-28 w-full rounded-[var(--radius-3)]" />
          <Skeleton className="h-72 w-full rounded-[var(--radius-3)]" />
        </div>
      </div>
    );
  }

  if (snapshotQuery.isError || !snapshotQuery.data) {
    return (
      <div>
        <PageHeader title="Источники и воркеры" />
        <OperatorUnavailableState
          title="Операторский снимок недоступен"
          resource="операторский снимок"
          details={operatorProblemMessage(snapshotQuery.error)}
          onRetry={() => void snapshotQuery.refetch()}
        />
      </div>
    );
  }

  const snapshot = snapshotForRealtimeState(snapshotQuery.data, realtimeStatus === "connected");
  const stateTrusted = snapshot.system.state === "ready" || snapshot.system.state === "partial";

  return (
    <div>
      <PageHeader
        title="Источники и воркеры"
        action={
          <Button
            variant="secondary"
            leftIcon={
              <RefreshCw
                size={15}
                aria-hidden="true"
                className={snapshotQuery.isFetching ? "animate-spin" : undefined}
              />
            }
            onClick={() => void snapshotQuery.refetch()}
            disabled={snapshotQuery.isFetching}
          >
            Обновить снимок
          </Button>
        }
      />

      <OperatorSectionFrame
        section={snapshot.system}
        title="Контур управления"
        description="Один источник состояния для web, mobile и Telegram."
        empty={<p className="p-5 text-[14px] text-bg-9">Источники не настроены.</p>}
      >
        {(system) => (
          <div>
            <dl className="grid border-b border-[var(--color-hairline)] sm:grid-cols-3">
              <SystemFact
                label="Мониторинг"
                value={
                  !stateTrusted || system.monitoring_enabled === null
                    ? "Не подтверждено"
                    : system.monitoring_enabled
                      ? "Включён"
                      : "Выключен"
                }
              />
              <SystemFact
                label="Последний scan"
                value={
                  stateTrusted && system.last_scan_at
                    ? formatTimestamp(system.last_scan_at, snapshot.meta.timezone)
                    : "Не подтверждено"
                }
              />
              <SystemFact
                label="Следующий scan"
                value={
                  stateTrusted && system.next_scan_at
                    ? formatTimestamp(system.next_scan_at, snapshot.meta.timezone)
                    : "Не подтверждено"
                }
              />
            </dl>

            <div role="list" aria-label="Воркеры" className="grid sm:grid-cols-2 xl:grid-cols-3">
              {system.workers.map((worker) => (
                <WorkerCard
                  key={worker.id}
                  worker={worker}
                  sectionState={snapshot.system.state}
                  trusted={stateTrusted}
                />
              ))}
            </div>
          </div>
        )}
      </OperatorSectionFrame>
    </div>
  );
}

function SystemFact({ label, value }: { label: string; value: string }) {
  return (
    <div className="min-h-20 border-r border-[var(--color-hairline)] p-4 last:border-r-0">
      <dt className="text-[12px] uppercase tracking-[.08em] text-bg-8">{label}</dt>
      <dd className="mt-2 text-[16px] font-semibold text-bg-11">{value}</dd>
    </div>
  );
}

function WorkerCard({
  worker,
  sectionState,
  trusted,
}: {
  worker: OperatorWorkerState;
  sectionState: "ready" | "empty" | "partial" | "stale" | "unavailable";
  trusted: boolean;
}) {
  const severity = severityForDataState(worker.severity, sectionState);
  return (
    <div
      role="listitem"
      className="flex min-h-20 items-center gap-3 border-b border-r border-[var(--color-hairline)] p-4"
    >
      <span
        className="size-2.5 shrink-0 rounded-full"
        style={{ background: SEVERITY_COLOR[severity] }}
        data-severity={severity}
        aria-hidden="true"
      />
      <div className="min-w-0">
        <strong className="block truncate text-[14px] text-bg-11">{worker.label}</strong>
        <span className="mt-1 block truncate text-[12px] text-bg-9">
          {trusted ? workerStatusLabel(worker.status) : "Состояние не подтверждено"}
        </span>
      </div>
    </div>
  );
}

function formatTimestamp(value: string, timezone: string): string {
  return new Intl.DateTimeFormat("ru-RU", {
    day: "2-digit",
    month: "2-digit",
    hour: "2-digit",
    minute: "2-digit",
    timeZone: timezone,
  }).format(new Date(value));
}
