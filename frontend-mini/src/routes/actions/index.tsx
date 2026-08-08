import { useState } from "react";
import { createFileRoute } from "@tanstack/react-router";

import type { OperatorActionState } from "@fb/shared/operator/contracts";
import {
  ACTION_STATE_LABEL,
  actionsForRealtimeState,
} from "@fb/shared/operator/viewModel";
import { useOperatorRealtimeStatus } from "@fb/operator-api";
import { DataStateBadge, DataStateNotice } from "@fb/operator-ui";

import { Button } from "@/components/ui/Button";
import { MiniActions } from "@/features/operator/OperatorMiniDashboard";
import { operatorProblemMessage, useOperatorActions } from "@/lib/operatorApi";

export const Route = createFileRoute("/actions/")({
  component: MiniActionsPage,
});

const FILTERS: Array<{ value?: OperatorActionState; label: string }> = [
  { label: "Все" },
  { value: "queued", label: "В очереди" },
  { value: "running", label: "В работе" },
  { value: "unknown", label: "Уточняются" },
  { value: "failed", label: "Ошибки" },
  { value: "confirmed", label: "Готово" },
];

function MiniActionsPage() {
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
      <div
        role="alert"
        className="m-4 rounded-[var(--radius-2)] bg-danger-bg p-4 text-[14px] text-danger"
      >
        {operatorProblemMessage(query.error)}
      </div>
    );
  }

  return (
    <div className="px-4 pb-5 pt-3">
      <header className="mb-4">
        <div className="font-display text-[12px] uppercase tracking-[.08em] text-bg-8">
          Операторский контур
        </div>
        <div className="mt-2 flex items-start justify-between gap-3">
          <h1 className="m-0 font-display text-[30px] font-medium text-bg-11">
            Действия
          </h1>
          <DataStateBadge state={dataState} compact />
        </div>
        <p className="mt-2 text-[14px] leading-5 text-bg-9">
          Очередь, выполнение и подтверждённый результат.
        </p>
      </header>

      <div
        className="mb-3 flex gap-2 overflow-x-auto pb-2"
        role="group"
        aria-label="Фильтр действий"
      >
        {FILTERS.map((filter) => (
          <button
            key={filter.label}
            type="button"
            aria-pressed={filter.value === state}
            onClick={() => setState(filter.value)}
            className={`min-h-11 shrink-0 rounded-full border px-4 text-[14px] font-semibold ${
              filter.value === state
                ? "border-accent bg-accent-bg text-accent"
                : "border-[var(--color-hairline-strong)] bg-bg-1 text-bg-9"
            }`}
          >
            {filter.value ? ACTION_STATE_LABEL[filter.value] : filter.label}
          </button>
        ))}
      </div>

      <section className="rounded-[var(--radius-3)] border border-[var(--color-hairline)] bg-bg-1 p-4">
        {dataState !== "ready" && !query.isPending ? (
          <DataStateNotice
            state={dataState}
            issues={projection?.issues ?? []}
            compact
          />
        ) : null}
        {query.isPending && !items.length ? (
          <div
            role="status"
            className="py-10 text-center text-[14px] text-bg-9"
          >
            Загрузка…
          </div>
        ) : (
          <MiniActions items={items} />
        )}
        {query.hasNextPage ? (
          <Button
            variant="secondary"
            fullWidth
            className="mt-4 min-h-11"
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
