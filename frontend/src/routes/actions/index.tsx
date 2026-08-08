import { useState } from "react";
import { createFileRoute } from "@tanstack/react-router";

import type { OperatorActionState } from "@fb/shared/operator/contracts";
import {
  ACTION_STATE_LABEL,
  actionsForRealtimeState,
} from "@fb/shared/operator/viewModel";
import { useOperatorRealtimeStatus } from "@fb/operator-api";
import { DataStateBadge, DataStateNotice } from "@fb/operator-ui";

import { ActionList } from "@/features/operator/OperatorDashboard";
import { Button } from "@/components/ui/Button";
import { ErrorState } from "@/components/ui/ErrorState";
import { operatorProblemMessage, useOperatorActions } from "@/lib/api/operator";

export const Route = createFileRoute("/actions/")({ component: ActionsPage });

const FILTERS: Array<{ value?: OperatorActionState; label: string }> = [
  { label: "Все" },
  { value: "queued", label: "В очереди" },
  { value: "running", label: "Выполняются" },
  { value: "unknown", label: "Уточняются" },
  { value: "failed", label: "Ошибки" },
  { value: "confirmed", label: "Подтверждены" },
];

function ActionsPage() {
  const [state, setState] = useState<OperatorActionState | undefined>();
  const realtimeStatus = useOperatorRealtimeStatus();
  const query = useOperatorActions({ state: state ? [state] : [] });
  const projections = query.data?.pages.map((page) =>
    actionsForRealtimeState(
      page,
      realtimeStatus === "connected" && !query.isError,
    ),
  );
  const items = projections?.flatMap((page) => page.items) ?? [];
  const projection = projections?.[0];
  const dataState =
    projection?.state ?? (query.isPending ? "stale" : "unavailable");

  if (query.isError && !query.data) {
    return (
      <ErrorState
        title="Действия недоступны"
        error={operatorProblemMessage(query.error)}
        onRetry={() => void query.refetch()}
      />
    );
  }

  return (
    <div className="mx-auto max-w-5xl">
      <header className="mb-5 flex flex-col gap-4 sm:flex-row sm:items-end sm:justify-between">
        <div>
          <div className="font-display text-[12px] uppercase tracking-[.08em] text-bg-8">
            Операторский контур
          </div>
          <h1 className="m-0 mt-2 font-display text-[clamp(28px,4vw,42px)] font-medium text-bg-11">
            Действия
          </h1>
          <p className="mt-2 text-[16px] text-bg-9">
            Lifecycle от постановки в очередь до подтверждённого результата.
          </p>
        </div>
        <DataStateBadge state={dataState} />
      </header>

      <div
        className="mb-4 flex gap-2 overflow-x-auto pb-2"
        role="group"
        aria-label="Фильтр действий по состоянию"
      >
        {FILTERS.map((filter) => {
          const active = filter.value === state;
          return (
            <button
              key={filter.label}
              type="button"
              aria-pressed={active}
              onClick={() => setState(filter.value)}
              className={`min-h-11 shrink-0 rounded-[var(--radius-full)] border px-4 text-[14px] font-semibold focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-accent ${
                active
                  ? "border-accent bg-accent-bg text-accent"
                  : "border-[var(--color-hairline-strong)] bg-bg-1 text-bg-9 hover:text-bg-11"
              }`}
            >
              {filter.value ? ACTION_STATE_LABEL[filter.value] : filter.label}
            </button>
          );
        })}
      </div>

      <section className="rounded-[var(--radius-3)] border border-[var(--color-hairline)] bg-bg-1 p-5">
        <h2 className="m-0 font-display text-[20px] text-bg-11">Очередь и история</h2>
        <p className="mt-1 text-[14px] text-bg-9">
          Unknown означает проверку фактического результата, а не успешное завершение.
        </p>
        {dataState !== "ready" && !query.isPending ? (
          <DataStateNotice state={dataState} issues={projection?.issues ?? []} />
        ) : null}
        {query.isPending && !items.length ? (
          <div role="status" className="py-12 text-center text-[16px] text-bg-9">
            Загрузка действий…
          </div>
        ) : (
          <ActionList items={items} />
        )}
        {query.hasNextPage ? (
          <Button
            variant="secondary"
            className="mt-5 min-h-11 w-full"
            loading={query.isFetchingNextPage}
            onClick={() => void query.fetchNextPage()}
          >
            Показать предыдущие
          </Button>
        ) : null}
      </section>
    </div>
  );
}
