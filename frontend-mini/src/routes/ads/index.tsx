import {
  useEffect,
  useRef,
  useState,
  type FormEvent,
  type ReactNode,
} from "react";
import { createFileRoute, useNavigate } from "@tanstack/react-router";
import { ChevronLeft, ChevronRight, Filter, Search } from "lucide-react";

import type { OperatorSeverity } from "@fb/shared/operator/contracts";
import { confirmedOperatorCurrency } from "@fb/shared/operator/adsViewModel";
import {
  operatorAdsQuerySort,
  operatorCabinetOptions,
  parseOperatorAdsRouteSearch,
  OPERATOR_ADS_STOP_PROXIMITY_SORT,
  type OperatorAdsDirection,
  type OperatorAdsRouteSearch,
  type OperatorAdsRouteSort,
  type OperatorCabinetOption,
} from "@fb/shared/operator/routeFilters";
import { adsForRealtimeState } from "@fb/shared/operator/viewModel";
import { DataStateBadge, DataStateNotice } from "@fb/operator-ui";
import { useOperatorRealtimeStatus } from "@fb/operator-api";

import { MiniHeader } from "@/components/layout/MiniHeader";
import { Button, EmptyState, ErrorState, Skeleton } from "@/components/ui";
import { Sheet } from "@/components/ui/Sheet";
import { MiniOperatorAdCard } from "@/features/operator/OperatorAds";
import {
  operatorProblemMessage,
  useOperatorAds,
  useOperatorSnapshot,
} from "@/lib/operatorApi";
import { haptic } from "@/lib/tg";

export const Route = createFileRoute("/ads/")({
  component: AdsPage,
  validateSearch: parseOperatorAdsRouteSearch,
});

const SEVERITIES: Array<{ value: OperatorSeverity | ""; label: string }> = [
  { value: "", label: "Все состояния" },
  { value: "critical", label: "Опасность" },
  { value: "warning", label: "Внимание" },
  { value: "ok", label: "Норма" },
  { value: "unknown", label: "Неизвестно" },
];

const SORTS: Array<{ value: OperatorAdsRouteSort; label: string }> = [
  { value: OPERATOR_ADS_STOP_PROXIMITY_SORT, label: "Близость к стопу" },
  { value: "updated", label: "Обновление" },
  { value: "spend", label: "Расход" },
  { value: "clicks", label: "Клики" },
  { value: "registrations", label: "Регистрации" },
  { value: "ftd", label: "FTD" },
  { value: "name", label: "Название" },
];

function AdsPage() {
  const search = Route.useSearch();
  const navigate = useNavigate({ from: "/ads/" });
  const realtimeStatus = useOperatorRealtimeStatus();
  const [draftSearch, setDraftSearch] = useState(search.q ?? "");
  const [filtersOpen, setFiltersOpen] = useState(false);
  const filterTriggerRef = useRef<HTMLButtonElement>(null);
  const snapshot = useOperatorSnapshot({ window: "today" });
  const cabinets = operatorCabinetOptions(snapshot.data);
  const page = search.page ?? 1;
  const query = useOperatorAds({
    search: search.q,
    account_id: search.account_id,
    severity: search.severity,
    sort: operatorAdsQuerySort(search.sort),
    direction: search.direction ?? "desc",
    page,
    page_size: 30,
  });
  const payload = query.data;
  const displayPayload = payload
    ? adsForRealtimeState(
        payload,
        realtimeStatus === "connected" && !query.isError,
      )
    : null;
  const displayState = displayPayload?.state;
  // Порядок задаёт сервер, включая сортировку по близости к стопу.
  const displayRows = displayPayload?.rows;
  const currency = confirmedOperatorCurrency(displayPayload?.scope);
  const confirmedEmpty =
    realtimeStatus === "connected" &&
    !query.isError &&
    displayState === "empty";
  const activeFilterCount =
    Number(Boolean(search.q)) +
    Number(Boolean(search.account_id)) +
    Number(Boolean(search.severity)) +
    Number(
      Boolean(search.sort && search.sort !== OPERATOR_ADS_STOP_PROXIMITY_SORT),
    ) +
    Number(Boolean(search.direction && search.direction !== "desc"));

  useEffect(() => setDraftSearch(search.q ?? ""), [search.q]);

  function patchSearch(next: Partial<OperatorAdsRouteSearch>) {
    haptic.selection();
    void navigate({
      search: (previous) => ({ ...previous, ...next }),
      replace: true,
    });
  }

  function submit(event: FormEvent) {
    event.preventDefault();
    patchSearch({ q: draftSearch.trim() || undefined, page: undefined });
    setFiltersOpen(false);
  }

  function resetFilters() {
    setDraftSearch("");
    haptic.selection();
    void navigate({ search: {}, replace: true });
  }

  return (
    <div className="flex flex-col pb-5">
      <MiniHeader
        eyebrowNum="04"
        eyebrow="УПРАВЛЕНИЕ"
        title="Объявления"
        right={
          displayState ? <DataStateBadge state={displayState} compact /> : null
        }
      />

      <div className="border-b border-[var(--color-hairline)] px-4 py-3">
        <Button
          ref={filterTriggerRef}
          type="button"
          variant="secondary"
          fullWidth
          aria-label="Открыть фильтры объявлений"
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
        eyebrow="УПРАВЛЕНИЕ"
        title="Фильтры объявлений"
        returnFocusRef={filterTriggerRef}
      >
        <AdsFilterFields
          search={search}
          draftSearch={draftSearch}
          cabinets={cabinets}
          onDraftSearch={setDraftSearch}
          onChange={patchSearch}
          onSubmit={submit}
        />
      </Sheet>

      {payload && displayState && displayState !== "ready" ? (
        <div className="px-4 pt-3">
          <DataStateNotice
            state={displayState}
            issues={displayPayload?.issues ?? []}
            compact
          />
        </div>
      ) : null}

      <section className="grid gap-3 px-4 pt-4" aria-label="Объявления">
        {query.isError && !payload ? (
          <ErrorState
            message={operatorProblemMessage(query.error)}
            onRetry={() => void query.refetch()}
          />
        ) : query.isPending && !payload ? (
          <div
            role="status"
            aria-label="Загрузка объявлений"
            className="grid gap-3"
          >
            {Array.from({ length: 5 }, (_, index) => (
              <Skeleton key={index} className="h-40 w-full" />
            ))}
          </div>
        ) : displayRows?.length ? (
          displayRows.map((ad) => (
            <MiniOperatorAdCard key={ad.id} ad={ad} currency={currency} />
          ))
        ) : confirmedEmpty ? (
          <EmptyState
            title="Объявлений не найдено"
            description={
              activeFilterCount
                ? "Сервер подтвердил пустой результат для выбранных условий."
                : "Сервер подтвердил, что в каталоге пока нет объявлений."
            }
            action={
              activeFilterCount
                ? { label: "Сбросить фильтры", onClick: resetFilters }
                : undefined
            }
          />
        ) : (
          <EmptyState
            title="Список не подтверждён"
            description="Дождитесь сверки live-снимка. Неподтверждённый результат не считается нулём."
          />
        )}
      </section>

      {displayPayload && displayPayload.pages > 1 ? (
        <nav
          aria-label="Страницы объявлений"
          className="mt-4 flex items-center justify-between gap-2 px-4"
        >
          <Button
            variant="secondary"
            disabled={page <= 1 || query.isFetching}
            onClick={() => patchSearch({ page: page - 1 })}
          >
            <ChevronLeft aria-hidden="true" size={16} /> Назад
          </Button>
          <span className="text-[14px] text-bg-9" aria-live="polite">
            {page} / {displayPayload.pages}
          </span>
          <Button
            variant="secondary"
            disabled={page >= displayPayload.pages || query.isFetching}
            onClick={() => patchSearch({ page: page + 1 })}
          >
            Далее <ChevronRight aria-hidden="true" size={16} />
          </Button>
        </nav>
      ) : null}
    </div>
  );
}

function AdsFilterFields({
  search,
  draftSearch,
  cabinets,
  onDraftSearch,
  onChange,
  onSubmit,
}: {
  search: OperatorAdsRouteSearch;
  draftSearch: string;
  cabinets: OperatorCabinetOption[];
  onDraftSearch: (value: string) => void;
  onChange: (next: Partial<OperatorAdsRouteSearch>) => void;
  onSubmit: (event: FormEvent) => void;
}) {
  return (
    <form onSubmit={onSubmit} className="grid gap-4 pb-4">
      <label className="grid gap-1.5 text-[14px] text-bg-9">
        <span>Поиск объявлений</span>
        <span className="relative block">
          <Search
            aria-hidden="true"
            className="absolute left-3 top-1/2 -translate-y-1/2 text-bg-8"
            size={16}
          />
          <input
            type="search"
            aria-label="Поиск объявлений"
            value={draftSearch}
            onChange={(event) => onDraftSearch(event.target.value)}
            placeholder="Название, кампания или ID"
            className="min-h-11 w-full rounded-[var(--radius-2)] border border-[var(--color-hairline-strong)] bg-bg-1 pl-10 pr-3 text-[16px] text-bg-11 outline-none placeholder:text-bg-8 focus:border-accent"
          />
        </span>
      </label>
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
        label="Риск"
        value={search.severity ?? ""}
        onChange={(value) =>
          onChange({
            severity: (value || undefined) as OperatorSeverity | undefined,
            page: undefined,
          })
        }
      >
        {SEVERITIES.map((severity) => (
          <option key={severity.value || "all"} value={severity.value}>
            {severity.label}
          </option>
        ))}
      </FilterSelect>
      <FilterSelect
        label="Сортировка"
        value={search.sort ?? OPERATOR_ADS_STOP_PROXIMITY_SORT}
        onChange={(value) =>
          onChange({ sort: value as OperatorAdsRouteSort, page: undefined })
        }
      >
        {SORTS.map((sort) => (
          <option key={sort.value} value={sort.value}>
            {sort.label}
          </option>
        ))}
      </FilterSelect>
      <FilterSelect
        label="Направление"
        value={search.direction ?? "desc"}
        onChange={(value) =>
          onChange({
            direction: value as OperatorAdsDirection,
            page: undefined,
          })
        }
      >
        <option value="desc">По убыванию</option>
        <option value="asc">По возрастанию</option>
      </FilterSelect>
      <Button type="submit" fullWidth>
        Применить поиск
      </Button>
    </form>
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
