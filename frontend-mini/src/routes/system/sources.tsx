import { createFileRoute } from "@tanstack/react-router";
import { RefreshCw } from "lucide-react";

import { useOperatorRealtimeStatus } from "@fb/operator-api";
import { OperatorSectionFrame } from "@fb/operator-ui";
import {
  severityForDataState,
  snapshotForRealtimeState,
  workerStatusLabel,
} from "@fb/shared/operator/viewModel";
import type {
  DataState,
  OperatorSeverity,
  OperatorWorkerState,
} from "@fb/shared/operator/contracts";

import { MiniHeader } from "@/components/layout/MiniHeader";
import { Button, Skeleton } from "@/components/ui";
import {
  operatorProblemMessage,
  useOperatorSnapshot,
} from "@/lib/operatorApi";

export const Route = createFileRoute("/system/sources")({
  component: SystemSourcesPage,
});

const SEVERITY_COLOR: Record<OperatorSeverity, string> = {
  ok: "var(--color-success)",
  warning: "var(--color-warning)",
  critical: "var(--color-danger)",
  unknown: "var(--color-bg-8)",
};

function SystemSourcesPage() {
  const realtimeStatus = useOperatorRealtimeStatus();
  const snapshotQuery = useOperatorSnapshot({ window: "today" });

  if (snapshotQuery.isLoading && !snapshotQuery.data) {
    return (
      <div className="flex flex-col">
        <MiniHeader eyebrowNum="04" eyebrow="СИСТЕМА" title="Источники и воркеры" />
        <div className="flex flex-col gap-3 p-4" aria-busy="true">
          <Skeleton className="h-24 w-full rounded-[var(--radius-3)]" />
          <Skeleton className="h-48 w-full rounded-[var(--radius-3)]" />
        </div>
      </div>
    );
  }

  if (snapshotQuery.isError || !snapshotQuery.data) {
    return (
      <div className="flex flex-col">
        <MiniHeader eyebrowNum="04" eyebrow="СИСТЕМА" title="Источники и воркеры" />
        <div
          role="alert"
          className="m-4 rounded-[var(--radius-3)] border border-danger/40 bg-danger-bg p-4"
        >
          <strong className="text-[16px] text-bg-11">Снимок недоступен</strong>
          <p className="mt-2 text-[14px] leading-5 text-bg-10">
            {operatorProblemMessage(snapshotQuery.error)}
          </p>
          <Button
            className="mt-4 min-h-11"
            onClick={() => void snapshotQuery.refetch()}
          >
            Повторить
          </Button>
        </div>
      </div>
    );
  }

  const snapshot = snapshotForRealtimeState(
    snapshotQuery.data,
    realtimeStatus === "connected",
  );
  const stateTrusted =
    snapshot.system.state === "ready" || snapshot.system.state === "partial";

  return (
    <div className="flex min-w-0 flex-col pb-20">
      <MiniHeader
        eyebrowNum="04"
        eyebrow="СИСТЕМА · ЕДИНЫЙ СНИМОК"
        title="Источники и воркеры"
        right={
          <button
            type="button"
            aria-label="Обновить снимок"
            onClick={() => void snapshotQuery.refetch()}
            disabled={snapshotQuery.isFetching}
            className="inline-flex size-11 items-center justify-center rounded-[var(--radius-2)] text-bg-9 focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-accent disabled:opacity-40"
          >
            <RefreshCw
              size={18}
              aria-hidden="true"
              className={snapshotQuery.isFetching ? "animate-spin" : undefined}
            />
          </button>
        }
      />

      <div className="p-4">
        <OperatorSectionFrame
          section={snapshot.system}
          title="Контур управления"
          description="Статус берётся из того же снимка, что главная и Telegram."
          empty={<p className="p-4 text-[14px] text-bg-9">Источники не настроены.</p>}
        >
          {(system) => (
            <div className="p-4 pt-2">
              <dl className="mb-4 grid grid-cols-2 gap-2">
                <div className="rounded-[var(--radius-2)] bg-bg-2 p-3">
                  <dt className="text-[12px] uppercase tracking-[.08em] text-bg-8">
                    Мониторинг
                  </dt>
                  <dd className="mt-1 text-[14px] font-semibold text-bg-11">
                    {!stateTrusted || system.monitoring_enabled === null
                      ? "Не подтверждено"
                      : system.monitoring_enabled
                        ? "Включён"
                        : "Выключен"}
                  </dd>
                </div>
                <div className="rounded-[var(--radius-2)] bg-bg-2 p-3">
                  <dt className="text-[12px] uppercase tracking-[.08em] text-bg-8">
                    Последний scan
                  </dt>
                  <dd className="mt-1 text-[14px] font-semibold text-bg-11">
                    {stateTrusted && system.last_scan_at
                      ? new Intl.DateTimeFormat("ru-RU", {
                          hour: "2-digit",
                          minute: "2-digit",
                          timeZone: snapshot.meta.timezone,
                        }).format(new Date(system.last_scan_at))
                      : "Не подтверждено"}
                  </dd>
                </div>
              </dl>

              <h2 className="mb-2 text-[12px] font-semibold uppercase tracking-[.08em] text-bg-8">
                Сканирование кабинетов
              </h2>
              <WorkerList
                workers={system.workers}
                label="Сканирование кабинетов"
                sectionState={snapshot.system.state}
                stateTrusted={stateTrusted}
              />

              <h2 className="mb-2 mt-4 text-[12px] font-semibold uppercase tracking-[.08em] text-bg-8">
                Фоновые воркеры
              </h2>
              <WorkerList
                workers={system.background_workers}
                label="Фоновые воркеры"
                sectionState={snapshot.system.state}
                stateTrusted={stateTrusted}
              />
            </div>
          )}
        </OperatorSectionFrame>
      </div>
    </div>
  );
}

function WorkerList({
  workers,
  label,
  sectionState,
  stateTrusted,
}: {
  workers: OperatorWorkerState[];
  label: string;
  sectionState: DataState;
  stateTrusted: boolean;
}) {
  return (
    <ul className="divide-y divide-[var(--color-hairline)]" aria-label={label}>
      {workers.map((worker) => {
        const severity = severityForDataState(worker.severity, sectionState);
        const status = stateTrusted
          ? workerStatusLabel(worker.status)
          : "Состояние не подтверждено";
        return (
          <li key={worker.id} className="flex min-h-14 items-center gap-3 py-3">
            <span
              className="size-2.5 shrink-0 rounded-full"
              style={{ background: SEVERITY_COLOR[severity] }}
              data-severity={severity}
              aria-hidden="true"
            />
            <div className="min-w-0 flex-1">
              <strong className="block truncate text-[14px] text-bg-11">{worker.label}</strong>
              <span className="block truncate text-[12px] text-bg-9">{status}</span>
            </div>
          </li>
        );
      })}
    </ul>
  );
}
