import { useRef, useState, type ReactNode } from "react";
import { createFileRoute, useNavigate } from "@tanstack/react-router";
import { Filter } from "lucide-react";

import type { OperatorActionState } from "@fb/shared/operator/contracts";
import {
  operatorCabinetOptions,
  parseOperatorActionsRouteSearch,
  type OperatorActionsRouteSearch,
  type OperatorCabinetOption,
} from "@fb/shared/operator/routeFilters";
import { actionsForRealtimeState } from "@fb/shared/operator/viewModel";
import { useOperatorRealtimeStatus } from "@fb/operator-api";
import { DataStateBadge, DataStateNotice } from "@fb/operator-ui";

import { Button } from "@/components/ui/Button";
import { Sheet } from "@/components/ui/Sheet";
import { MiniActions } from "@/features/operator/OperatorMiniDashboard";
import {
  operatorProblemMessage,
  useOperatorActions,
  useOperatorSnapshot,
} from "@/lib/operatorApi";
import { haptic } from "@/lib/tg";

export const Route = createFileRoute("/actions/")({
  component: MiniActionsPage,
  validateSearch: parseOperatorActionsRouteSearch,
});

const ACTION_STATES: Array<{ value: OperatorActionState | ""; label: string }> =
  [
    { value: "", label: "Все состояния" },
    { value: "queued", label: "В очереди" },
    { value: "running", label: "В работе" },
    { value: "unknown", label: "Уточняются" },
    { value: "failed", label: "Ошибки" },
    { value: "cancelled", label: "Отменены" },
    { value: "confirmed", label: "Готово" },
  ];

function MiniActionsPage() {
  const search = Route.useSearch();
  const navigate = useNavigate({ from: "/actions/" });
  const [filtersOpen, setFiltersOpen] = useState(false);
  const filterTriggerRef = useRef<HTMLButtonElement>(null);
  const realtimeStatus = useOperatorRealtimeStatus();
  const snapshot = useOperatorSnapshot({ window: "today" });
  const cabinets = operatorCabinetOptions(snapshot.data);
  const query = useOperatorActions({
    account_id: search.account_id,
    state: search.state ? [search.state] : [],
  });
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
  const activeFilterCount =
    Number(Boolean(search.state)) + Number(Boolean(search.account_id));

  function patchSearch(next: Partial<OperatorActionsRouteSearch>) {
    haptic.selection();
    void navigate({
      search: (previous) => ({ ...previous, ...next }),
      replace: true,
    });
  }

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

      <Button
        ref={filterTriggerRef}
        type="button"
        variant="secondary"
        fullWidth
        aria-label="Открыть фильтры действий"
        aria-haspopup="dialog"
        aria-expanded={filtersOpen}
        onClick={() => {
          haptic.selection();
          setFiltersOpen(true);
        }}
      >
        <Filter size={16} aria-hidden="true" />
        Фильтры{activeFilterCount ? ` · ${activeFilterCount}` : ""}
      </Button>

      <Sheet
        open={filtersOpen}
        onClose={() => setFiltersOpen(false)}
        eyebrow="ОПЕРАТОРСКИЙ КОНТУР"
        title="Фильтры действий"
        returnFocusRef={filterTriggerRef}
      >
        <ActionFilterFields
          search={search}
          cabinets={cabinets}
          onChange={patchSearch}
        />
      </Sheet>

      <section className="mt-3 rounded-[var(--radius-3)] border border-[var(--color-hairline)] bg-bg-1 p-4">
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

function ActionFilterFields({
  search,
  cabinets,
  onChange,
}: {
  search: OperatorActionsRouteSearch;
  cabinets: OperatorCabinetOption[];
  onChange: (next: Partial<OperatorActionsRouteSearch>) => void;
}) {
  return (
    <div className="grid gap-4 pb-4">
      <FilterSelect
        label="Кабинет"
        value={search.account_id ?? ""}
        onChange={(value) => onChange({ account_id: value || undefined })}
      >
        <option value="">Все кабинеты</option>
        {cabinets.map((cabinet) => (
          <option key={cabinet.value} value={cabinet.value}>
            {cabinet.label}
          </option>
        ))}
      </FilterSelect>
      <FilterSelect
        label="Состояние действия"
        value={search.state ?? ""}
        onChange={(value) =>
          onChange({
            state: (value || undefined) as OperatorActionState | undefined,
          })
        }
      >
        {ACTION_STATES.map((state) => (
          <option key={state.value || "all"} value={state.value}>
            {state.label}
          </option>
        ))}
      </FilterSelect>
    </div>
  );
}

function FilterSelect({
  label,
  value,
  onChange,
  children,
}: {
  label: string;
  value: string;
  onChange: (value: string) => void;
  children: ReactNode;
}) {
  return (
    <label className="grid gap-1.5 text-[14px] text-bg-9">
      <span>{label}</span>
      <select
        aria-label={label}
        value={value}
        onChange={(event) => onChange(event.target.value)}
        className="min-h-11 w-full rounded-[var(--radius-2)] border border-[var(--color-hairline-strong)] bg-bg-1 px-3 text-[16px] text-bg-11 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-accent"
      >
        {children}
      </select>
    </label>
  );
}
