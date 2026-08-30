import { useState } from "react";
import { createFileRoute, Link, useNavigate } from "@tanstack/react-router";
import { ArrowRight, Filter, ShieldCheck } from "lucide-react";

import { formatShownOfRussianCount } from "@fb/shared";
import { formatZonedDateTime } from "@fb/shared/format/time";
import type {
  OperatorIncidentItem,
  OperatorIncidentStatus,
  OperatorSeverity,
} from "@fb/shared/operator/contracts";
import {
  OPERATOR_INCIDENT_STATUS_LABEL,
  operatorIncidentCopy,
  operatorIncidentDataState,
  operatorIncidentsQuery,
  operatorIncidentTargetLabel,
  parseOperatorIncidentsRouteSearch,
  type OperatorIncidentsRouteSearch,
} from "@fb/shared/operator/incidentViewModel";
import { useOperatorRealtimeStatus } from "@fb/operator-api";
import {
  operatorCabinetOptions,
  type OperatorCabinetOption,
} from "@fb/shared/operator/routeFilters";
import { DataStateBadge, DataStateNotice, OperatorIncidentStatusBadge } from "@fb/operator-ui";

import { Button } from "@/components/ui/Button";
import { Drawer } from "@/components/ui/Drawer";
import { EmptyState } from "@/components/ui/EmptyState";
import { Skeleton } from "@/components/ui/Skeleton";
import {
  OperatorPageBoundary,
  OperatorUnavailableState,
} from "@/components/layout/OperatorPageBoundary";
import { OperatorSeverityBadge } from "@/features/operator/OperatorAds";
import {
  operatorIncidentProblemMessage,
  useAcknowledgeOperatorIncident,
  useOperatorIncidents,
  useOperatorSnapshot,
} from "@/lib/api/operator";

export const Route = createFileRoute("/incidents/")({
  component: OperatorIncidentsPage,
  validateSearch: parseOperatorIncidentsRouteSearch,
});

const SEVERITIES: Array<{ value: OperatorSeverity | ""; label: string }> = [
  { value: "", label: "Все уровни" },
  { value: "critical", label: "Критические" },
  { value: "warning", label: "Предупреждения" },
  { value: "unknown", label: "Неизвестно" },
  { value: "ok", label: "Восстановления" },
];

const STATUSES: Array<{ value: OperatorIncidentStatus | ""; label: string }> = [
  { value: "", label: "Все статусы" },
  ...Object.entries(OPERATOR_INCIDENT_STATUS_LABEL).map(([value, label]) => ({
    value: value as OperatorIncidentStatus,
    label,
  })),
];

function OperatorIncidentsPage() {
  const search = Route.useSearch();
  const navigate = useNavigate({ from: "/incidents/" });
  const [filtersOpen, setFiltersOpen] = useState(false);
  const [acknowledgingId, setAcknowledgingId] = useState<string | null>(null);
  const [actionError, setActionError] = useState<string | null>(null);
  const realtimeStatus = useOperatorRealtimeStatus();
  const snapshot = useOperatorSnapshot({ window: "today" });
  const cabinets = operatorCabinetOptions(snapshot.data);
  const incidents = useOperatorIncidents(operatorIncidentsQuery(search, 30));
  const acknowledge = useAcknowledgeOperatorIncident();
  // Курсорное «Показать ещё» (issue #340): накопленные страницы держит
  // инфинит-запрос, а не URL. Последняя загруженная порция даёт самые свежие
  // total/scope — они относятся ко всей выборке, а не к одной странице.
  const pages = incidents.data?.pages;
  const payload = pages?.at(-1) ?? null;
  const items = pages?.flatMap((page) => page.items) ?? [];
  const displayState = payload
    ? operatorIncidentDataState(payload.state, realtimeStatus === "connected" && !incidents.isError)
    : undefined;
  const activeFilterCount =
    Number(Boolean(search.account_id)) +
    Number(Boolean(search.severity)) +
    Number(Boolean(search.status));

  function patchSearch(next: Partial<OperatorIncidentsRouteSearch>) {
    void navigate({
      search: (previous) => ({ ...previous, ...next }),
      replace: true,
    });
  }

  function resetFilters() {
    void navigate({ search: {}, replace: true });
  }

  async function acknowledgeIncident(item: OperatorIncidentItem) {
    setActionError(null);
    setAcknowledgingId(item.id);
    try {
      await acknowledge.mutateAsync({
        params: {
          path: { incident_id: item.id },
          header: { "X-Operator-Principal": "operator:web" },
        },
      });
      await incidents.refetch();
    } catch (error) {
      setActionError(operatorIncidentProblemMessage(error));
    } finally {
      setAcknowledgingId(null);
    }
  }

  if (incidents.isError && !incidents.data) {
    return (
      <OperatorPageBoundary
        title="Инциденты"
        subtitle="Полный журнал, статусы и подтверждения получения"
      >
        <OperatorUnavailableState
          title="Журнал инцидентов недоступен"
          resource="журнал инцидентов"
          details={operatorIncidentProblemMessage(incidents.error)}
          onRetry={() => void incidents.refetch()}
        />
      </OperatorPageBoundary>
    );
  }

  return (
    <div className="mx-auto max-w-6xl">
      <header className="mb-5 flex flex-col gap-4 sm:flex-row sm:items-end sm:justify-between">
        <div>
          <h1 className="m-0 font-display text-[clamp(30px,4vw,44px)] font-medium text-bg-11">
            Инциденты
          </h1>
          <p className="mt-2 max-w-2xl text-[16px] leading-6 text-bg-9">
            Лента внимания показывает срочное. Здесь — полный журнал, статусы и подтверждения
            получения.
          </p>
        </div>
        <div className="flex items-center gap-3">
          {displayState ? <DataStateBadge state={displayState} /> : null}
          <span className="font-numeric text-[14px] text-bg-9">
            {payload
              ? formatShownOfRussianCount(items.length, payload.total, "запись", "записи", "записей")
              : "Загрузка…"}
          </span>
        </div>
      </header>

      <div className="mb-4 md:hidden">
        <Button
          type="button"
          variant="secondary"
          className="min-h-11 w-full"
          leftIcon={<Filter aria-hidden="true" />}
          aria-haspopup="dialog"
          aria-expanded={filtersOpen}
          onClick={() => setFiltersOpen(true)}
        >
          Фильтры{activeFilterCount ? ` · ${activeFilterCount}` : ""}
        </Button>
        <Drawer
          open={filtersOpen}
          onOpenChange={setFiltersOpen}
          title="Фильтры инцидентов"
          description="Кабинет, уровень и жизненный цикл"
          width={480}
        >
          <IncidentFilterFields
            search={search}
            cabinets={cabinets}
            onChange={patchSearch}
            stacked
          />
        </Drawer>
      </div>

      <section
        aria-label="Фильтры инцидентов"
        className="mb-4 hidden border-y border-[var(--color-hairline)] bg-bg-1 px-4 py-3 md:block"
      >
        <IncidentFilterFields search={search} cabinets={cabinets} onChange={patchSearch} />
      </section>

      {payload && displayState && displayState !== "ready" && displayState !== "empty" ? (
        <div className="mb-4">
          <DataStateNotice state={displayState} issues={payload.issues} />
        </div>
      ) : null}
      {actionError ? (
        <p
          role="alert"
          className="mb-4 border-y border-danger/35 bg-danger-bg px-4 py-3 text-[14px] text-danger"
        >
          {actionError}
        </p>
      ) : null}

      <section aria-label="Журнал инцидентов" className="border-y border-[var(--color-hairline)]">
        {incidents.isPending && !payload ? (
          <div role="status" aria-label="Загрузка инцидентов" className="grid gap-px bg-bg-3">
            {Array.from({ length: 6 }, (_, index) => (
              <Skeleton key={index} className="h-32 w-full rounded-none" />
            ))}
          </div>
        ) : payload && items.length ? (
          <ol className="m-0 divide-y divide-[var(--color-hairline)] p-0">
            {items.map((item) => (
              <IncidentLedgerRow
                key={item.id}
                item={item}
                scope={payload.scope}
                timezone={payload.scope.display_timezone}
                acknowledging={acknowledgingId === item.id}
                onAcknowledge={acknowledgeIncident}
              />
            ))}
          </ol>
        ) : displayState === "empty" ? (
          <EmptyState
            title="Инцидентов не найдено"
            description={
              activeFilterCount
                ? "Сервер подтвердил пустой результат для выбранных условий."
                : "Сервер подтвердил, что новых инцидентов нет."
            }
            action={
              activeFilterCount ? (
                <Button variant="secondary" onClick={resetFilters}>
                  Сбросить фильтры
                </Button>
              ) : undefined
            }
          />
        ) : (
          <EmptyState
            title="Журнал не подтверждён"
            description="Обновите данные. Неподтверждённый список не считается пустым."
          />
        )}
      </section>

      {incidents.hasNextPage ? (
        <Button
          variant="secondary"
          className="mt-4 min-h-11 w-full"
          loading={incidents.isFetchingNextPage}
          onClick={() => void incidents.fetchNextPage()}
        >
          Показать ещё
        </Button>
      ) : null}
    </div>
  );
}

function IncidentLedgerRow({
  item,
  scope,
  timezone,
  acknowledging,
  onAcknowledge,
}: {
  item: OperatorIncidentItem;
  scope: Parameters<typeof operatorIncidentCopy>[1];
  timezone: string;
  acknowledging: boolean;
  onAcknowledge: (item: OperatorIncidentItem) => Promise<void>;
}) {
  const copy = operatorIncidentCopy(item, scope);
  return (
    <li className="grid gap-4 bg-bg-0 px-4 py-4 sm:grid-cols-[minmax(0,1fr)_auto] sm:px-5">
      <div className="min-w-0">
        <div className="flex flex-wrap items-center gap-2">
          <OperatorSeverityBadge severity={item.severity} />
          <OperatorIncidentStatusBadge status={item.status} />
          <span className="text-[14px] text-bg-9">{operatorIncidentTargetLabel(item)}</span>
        </div>
        <h2 className="m-0 mt-3 text-[18px] font-semibold leading-6 text-bg-11">{copy.title}</h2>
        {copy.summary ? (
          <p className="mt-2 max-w-3xl text-[16px] leading-6 text-bg-10">{copy.summary}</p>
        ) : null}
        {copy.reason ? (
          <p className="mt-2 text-[14px] leading-5 text-bg-9">Причина: {copy.reason}</p>
        ) : null}
        <time dateTime={item.occurred_at} className="mt-3 block font-numeric text-[12px] text-bg-8">
          {formatZonedDateTime(item.occurred_at, timezone)}
        </time>
      </div>
      <div className="flex items-end gap-2 sm:flex-col sm:items-stretch sm:justify-end">
        <Link
          to="/incidents/$incidentId"
          params={{ incidentId: item.id }}
          className="inline-flex min-h-11 flex-1 items-center justify-center gap-2 rounded-[var(--radius-2)] border border-[var(--color-hairline-strong)] px-4 text-[14px] font-semibold text-bg-11 no-underline hover:bg-bg-2 focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-accent sm:flex-none"
        >
          Открыть <ArrowRight size={15} aria-hidden="true" />
        </Link>
        {item.status === "open" ? (
          <Button
            variant="secondary"
            className="min-h-11 flex-1 sm:flex-none"
            leftIcon={<ShieldCheck aria-hidden="true" />}
            loading={acknowledging}
            onClick={() => void onAcknowledge(item)}
          >
            Принять
          </Button>
        ) : null}
      </div>
    </li>
  );
}

function IncidentFilterFields({
  search,
  cabinets,
  onChange,
  stacked = false,
}: {
  search: OperatorIncidentsRouteSearch;
  cabinets: OperatorCabinetOption[];
  onChange: (next: Partial<OperatorIncidentsRouteSearch>) => void;
  stacked?: boolean;
}) {
  return (
    <div className={stacked ? "grid gap-4" : "grid gap-3 md:grid-cols-3"}>
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
        label="Уровень"
        value={search.severity ?? ""}
        onChange={(value) =>
          onChange({
            severity: (value || undefined) as OperatorSeverity | undefined,
          })
        }
      >
        {SEVERITIES.map((item) => (
          <option key={item.value || "all"} value={item.value}>
            {item.label}
          </option>
        ))}
      </FilterSelect>
      <FilterSelect
        label="Статус"
        value={search.status ?? ""}
        onChange={(value) =>
          onChange({
            status: (value || undefined) as OperatorIncidentStatus | undefined,
          })
        }
      >
        {STATUSES.map((item) => (
          <option key={item.value || "all"} value={item.value}>
            {item.label}
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
  children: React.ReactNode;
}) {
  return (
    <label className="grid gap-2 text-[14px] font-semibold text-bg-10">
      <span>{label}</span>
      <select
        value={value}
        onChange={(event) => onChange(event.target.value)}
        className="min-h-11 w-full rounded-[var(--radius-2)] border border-[var(--color-hairline-strong)] bg-bg-0 px-3 text-[16px] text-bg-11 outline-none focus:border-accent focus:ring-1 focus:ring-accent"
      >
        {children}
      </select>
    </label>
  );
}
