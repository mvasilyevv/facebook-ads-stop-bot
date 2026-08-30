import { useState } from "react";
import { createFileRoute, useNavigate } from "@tanstack/react-router";
import {
  ArrowRight,
  ChevronLeft,
  ChevronRight,
  Filter,
  ShieldCheck,
} from "lucide-react";

import { formatZonedDateTime } from "@fb/shared/format/time";
import type {
  OperatorIncidentItem,
  OperatorIncidentStatus,
  OperatorSeverity,
} from "@fb/shared/operator/contracts";
import {
  operatorIncidentCountLabel,
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
import {
  DataStateBadge,
  DataStateNotice,
  OperatorIncidentStatusBadge,
} from "@fb/operator-ui";

import { MiniHeader } from "@/components/layout/MiniHeader";
import {
  Button,
  EmptyState,
  ErrorState,
  Sheet,
  Skeleton,
} from "@/components/ui";
import { MiniSeverityBadge } from "@/features/operator/OperatorAds";
import {
  operatorIncidentProblemMessage,
  useAcknowledgeOperatorIncident,
  useOperatorIncidents,
  useOperatorSnapshot,
} from "@/lib/operatorApi";
import { storeResolvedNavigation } from "@/lib/transientNavigation";
import { haptic } from "@/lib/tg";

export const Route = createFileRoute("/incidents/")({
  component: MiniIncidentsPage,
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

function MiniIncidentsPage() {
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
  const payload = incidents.data;
  const displayState = payload
    ? operatorIncidentDataState(
        payload.state,
        realtimeStatus === "connected" && !incidents.isError,
      )
    : undefined;
  const page = search.page ?? 1;
  const activeFilterCount =
    Number(Boolean(search.account_id)) +
    Number(Boolean(search.severity)) +
    Number(Boolean(search.status));

  function patchSearch(next: Partial<OperatorIncidentsRouteSearch>) {
    haptic.selection();
    void navigate({
      search: (previous) => ({ ...previous, ...next }),
      replace: true,
    });
  }

  function resetFilters() {
    haptic.selection();
    void navigate({ search: {}, replace: true });
  }

  async function openIncident(item: OperatorIncidentItem) {
    haptic.selection();
    storeResolvedNavigation({ target_kind: "incident", target_id: item.id });
    await navigate({ to: "/open" });
  }

  async function acknowledgeIncident(item: OperatorIncidentItem) {
    setActionError(null);
    setAcknowledgingId(item.id);
    haptic.impact("medium");
    try {
      await acknowledge.mutateAsync({
        params: {
          path: { incident_id: item.id },
          header: { "X-Operator-Principal": "operator:tma" },
        },
      });
      haptic.notify("success");
      await incidents.refetch();
    } catch (error) {
      haptic.notify("error");
      setActionError(operatorIncidentProblemMessage(error));
    } finally {
      setAcknowledgingId(null);
    }
  }

  return (
    <div className="flex flex-col pb-5">
      <MiniHeader
        title="Инциденты"
        right={
          displayState ? <DataStateBadge state={displayState} compact /> : null
        }
      />

      <div className="px-4 pb-3">
        <p className="mb-3 text-[14px] leading-5 text-bg-9">
          Полный журнал сигналов и подтверждений получения.
        </p>
        <p
          className="mb-3 font-numeric text-[13px] text-bg-8"
          aria-live="polite"
        >
          {payload
            ? operatorIncidentCountLabel(payload.total)
            : incidents.isError
              ? "Не удалось загрузить"
              : "Загрузка…"}
        </p>
        <Button
          type="button"
          variant="secondary"
          fullWidth
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
      </div>

      <Sheet
        open={filtersOpen}
        onClose={() => setFiltersOpen(false)}
        eyebrow="ЖУРНАЛ"
        title="Фильтры инцидентов"
      >
        <IncidentFilterFields
          search={search}
          cabinets={cabinets}
          onChange={patchSearch}
        />
      </Sheet>

      {payload &&
      displayState &&
      displayState !== "ready" &&
      displayState !== "empty" ? (
        <div className="px-4 pb-3">
          <DataStateNotice
            state={displayState}
            issues={payload.issues}
            compact
          />
        </div>
      ) : null}
      {actionError ? (
        <p
          role="alert"
          className="mx-4 mb-3 border-y border-danger/35 bg-danger-bg px-3 py-3 text-[14px] text-danger"
        >
          {actionError}
        </p>
      ) : null}

      <section
        aria-label="Журнал инцидентов"
        className="border-y border-[var(--color-hairline)]"
      >
        {incidents.isError && !payload ? (
          <div className="px-4 py-5">
            <ErrorState
              message={operatorIncidentProblemMessage(incidents.error)}
              onRetry={() => void incidents.refetch()}
            />
          </div>
        ) : incidents.isPending && !payload ? (
          <div
            role="status"
            aria-label="Загрузка инцидентов"
            className="grid gap-px bg-bg-3"
          >
            {Array.from({ length: 5 }, (_, index) => (
              <Skeleton key={index} className="h-44 w-full rounded-none" />
            ))}
          </div>
        ) : payload?.items.length ? (
          <ol className="m-0 divide-y divide-[var(--color-hairline)] p-0">
            {payload.items.map((item) => (
              <MiniIncidentCard
                key={item.id}
                item={item}
                scope={payload.scope}
                timezone={payload.scope.display_timezone}
                acknowledging={acknowledgingId === item.id}
                onOpen={openIncident}
                onAcknowledge={acknowledgeIncident}
              />
            ))}
          </ol>
        ) : displayState === "empty" ? (
          <div className="px-4">
            <EmptyState
              title="Инцидентов не найдено"
              description={
                activeFilterCount
                  ? "Сервер подтвердил пустой результат для выбранных условий."
                  : "Сервер подтвердил, что новых инцидентов нет."
              }
              action={
                activeFilterCount
                  ? { label: "Сбросить фильтры", onClick: resetFilters }
                  : undefined
              }
            />
          </div>
        ) : (
          <div className="px-4">
            <EmptyState
              title="Журнал не подтверждён"
              description="Обновите данные. Неизвестный список не считается пустым."
            />
          </div>
        )}
      </section>

      {payload && payload.pages > 1 ? (
        <nav
          aria-label="Страницы инцидентов"
          className="mt-4 flex items-center justify-between gap-2 px-4"
        >
          <Button
            variant="secondary"
            disabled={page <= 1 || incidents.isFetching}
            onClick={() => patchSearch({ page: page - 1 })}
          >
            <ChevronLeft size={16} aria-hidden="true" /> Назад
          </Button>
          <span className="text-[14px] text-bg-9" aria-live="polite">
            {page} / {payload.pages}
          </span>
          <Button
            variant="secondary"
            disabled={page >= payload.pages || incidents.isFetching}
            onClick={() => patchSearch({ page: page + 1 })}
          >
            Далее <ChevronRight size={16} aria-hidden="true" />
          </Button>
        </nav>
      ) : null}
    </div>
  );
}

function MiniIncidentCard({
  item,
  scope,
  timezone,
  acknowledging,
  onOpen,
  onAcknowledge,
}: {
  item: OperatorIncidentItem;
  scope: Parameters<typeof operatorIncidentCopy>[1];
  timezone: string;
  acknowledging: boolean;
  onOpen: (item: OperatorIncidentItem) => Promise<void>;
  onAcknowledge: (item: OperatorIncidentItem) => Promise<void>;
}) {
  const copy = operatorIncidentCopy(item, scope);
  return (
    <li className="bg-bg-0 px-4 py-4">
      <div className="flex flex-wrap items-center gap-2">
        <MiniSeverityBadge severity={item.severity} />
        <OperatorIncidentStatusBadge status={item.status} />
      </div>
      <p className="mt-3 text-[14px] text-bg-9">
        {operatorIncidentTargetLabel(item)}
      </p>
      <h2 className="m-0 mt-1 text-[18px] font-semibold leading-6 text-bg-11">
        {copy.title}
      </h2>
      {copy.summary ? (
        <p className="mt-2 text-[14px] leading-5 text-bg-10">{copy.summary}</p>
      ) : null}
      {copy.reason ? (
        <p className="mt-2 text-[14px] leading-5 text-bg-9">
          Причина: {copy.reason}
        </p>
      ) : null}
      <time
        dateTime={item.occurred_at}
        className="mt-3 block font-numeric text-[12px] text-bg-8"
      >
        {formatZonedDateTime(item.occurred_at, timezone)}
      </time>
      <div
        className={`mt-4 grid gap-2 ${item.status === "open" ? "grid-cols-2" : "grid-cols-1"}`}
      >
        <Button variant="secondary" onClick={() => void onOpen(item)}>
          Открыть <ArrowRight size={15} aria-hidden="true" />
        </Button>
        {item.status === "open" ? (
          <Button
            variant="secondary"
            loading={acknowledging}
            onClick={() => void onAcknowledge(item)}
          >
            <ShieldCheck size={16} aria-hidden="true" /> Принять
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
}: {
  search: OperatorIncidentsRouteSearch;
  cabinets: OperatorCabinetOption[];
  onChange: (next: Partial<OperatorIncidentsRouteSearch>) => void;
}) {
  return (
    <div className="grid gap-4 pb-4">
      <FilterSelect
        label="Кабинет"
        value={search.account_id ?? ""}
        onChange={(value) =>
          onChange({ account_id: value || undefined, page: undefined })
        }
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
            page: undefined,
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
            page: undefined,
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
        className="min-h-11 w-full rounded-[var(--radius-2)] border border-[var(--color-hairline-strong)] bg-bg-1 px-3 text-[16px] text-bg-11 focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-accent"
      >
        {children}
      </select>
    </label>
  );
}
