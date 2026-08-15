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

import { ActionList } from "@/features/operator/OperatorDashboard";
import { PageHeader } from "@/components/layout/PageHeader";
import { Button } from "@/components/ui/Button";
import { Drawer } from "@/components/ui/Drawer";
import {
  OperatorListSkeleton,
  OperatorPageBoundary,
  OperatorUnavailableState,
} from "@/components/layout/OperatorPageBoundary";
import {
  operatorProblemMessage,
  useOperatorActions,
  useOperatorSnapshot,
} from "@/lib/api/operator";

export const Route = createFileRoute("/actions/")({
  component: ActionsPage,
  validateSearch: parseOperatorActionsRouteSearch,
});

const ACTION_STATES: Array<{ value: OperatorActionState | ""; label: string }> = [
  { value: "", label: "Все состояния" },
  { value: "queued", label: "В очереди" },
  { value: "running", label: "Выполняются" },
  { value: "unknown", label: "Уточняются" },
  { value: "failed", label: "Ошибки" },
  { value: "cancelled", label: "Отменены" },
  { value: "confirmed", label: "Подтверждены" },
];

function ActionsPage() {
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
    actionsForRealtimeState(page, realtimeStatus === "connected" && !query.isError),
  );
  const items = projections?.flatMap((page) => page.items) ?? [];
  const projection = projections?.[0];
  const dataState = projection?.state ?? (query.isPending ? "stale" : "unavailable");
  const activeFilterCount = Number(Boolean(search.state)) + Number(Boolean(search.account_id));

  function patchSearch(next: Partial<OperatorActionsRouteSearch>) {
    void navigate({
      search: (previous) => ({ ...previous, ...next }),
      replace: true,
    });
  }

  if (query.isError && !query.data) {
    return (
      <OperatorPageBoundary
        title="Действия"
        subtitle="Очередь, выполнение и подтверждённый результат"
      >
        <OperatorUnavailableState
          title="Действия недоступны"
          resource="историю действий"
          details={operatorProblemMessage(query.error)}
          onRetry={() => void query.refetch()}
        />
      </OperatorPageBoundary>
    );
  }

  return (
    <div className="mx-auto max-w-5xl">
      <PageHeader
        title="Действия"
        subtitle="Очередь, выполнение и подтверждённый результат"
        action={<DataStateBadge state={dataState} />}
      />

      <div className="mb-4 md:hidden">
        <Button
          ref={filterTriggerRef}
          type="button"
          variant="secondary"
          className="min-h-11 w-full"
          leftIcon={<Filter aria-hidden="true" />}
          aria-label="Открыть фильтры действий"
          aria-haspopup="dialog"
          aria-expanded={filtersOpen}
          onClick={() => setFiltersOpen(true)}
        >
          Фильтры{activeFilterCount ? ` · ${activeFilterCount}` : ""}
        </Button>
        <Drawer
          open={filtersOpen}
          onOpenChange={setFiltersOpen}
          title="Фильтры действий"
          description="Кабинет и состояние действия"
          width={480}
          returnFocusRef={filterTriggerRef}
        >
          <ActionFilterFields search={search} cabinets={cabinets} onChange={patchSearch} stacked />
        </Drawer>
      </div>

      <section
        aria-label="Фильтры действий"
        className="mb-4 hidden rounded-[var(--radius-3)] border border-[var(--color-hairline)] bg-bg-1 p-4 md:block"
      >
        <ActionFilterFields search={search} cabinets={cabinets} onChange={patchSearch} />
      </section>

      <section className="rounded-[var(--radius-3)] border border-[var(--color-hairline)] bg-bg-1 p-5">
        <h2 className="m-0 font-display text-[20px] text-bg-11">Очередь и история</h2>
        <p className="mt-1 text-[14px] text-bg-9">
          Неизвестный результат означает проверку фактического результата, а не успешное завершение.
        </p>
        {dataState !== "ready" && !query.isPending ? (
          <DataStateNotice state={dataState} issues={projection?.issues ?? []} />
        ) : null}
        {query.isPending && !items.length ? (
          <OperatorListSkeleton label="Загрузка действий" />
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

function ActionFilterFields({
  search,
  cabinets,
  onChange,
  stacked = false,
}: {
  search: OperatorActionsRouteSearch;
  cabinets: OperatorCabinetOption[];
  onChange: (next: Partial<OperatorActionsRouteSearch>) => void;
  stacked?: boolean;
}) {
  return (
    <div className={stacked ? "grid gap-4" : "grid gap-3 md:grid-cols-2"}>
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
          onChange({ state: (value || undefined) as OperatorActionState | undefined })
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
        className="min-h-11 w-full rounded-[var(--radius-2)] border border-[var(--color-hairline-strong)] bg-bg-0 px-3 text-[14px] text-bg-11 outline-none focus:border-accent focus:ring-1 focus:ring-accent"
      >
        {children}
      </select>
    </label>
  );
}
