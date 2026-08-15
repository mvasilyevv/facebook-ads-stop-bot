import { useEffect, useRef, useState, type FormEvent, type ReactNode } from "react";
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

import { Eyebrow } from "@/components/data/Eyebrow";
import { Button } from "@/components/ui/Button";
import { Drawer } from "@/components/ui/Drawer";
import { EmptyState } from "@/components/ui/EmptyState";
import { Skeleton } from "@/components/ui/Skeleton";
import {
  OperatorPageBoundary,
  OperatorUnavailableState,
} from "@/components/layout/OperatorPageBoundary";
import { OperatorAdCards, OperatorAdsTable } from "@/features/operator/OperatorAds";
import { operatorProblemMessage, useOperatorAds, useOperatorSnapshot } from "@/lib/api/operator";
import { formatRussianCount } from "@/lib/utils/russianCount";

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

export const Route = createFileRoute("/ads/")({
  component: AdsPage,
  validateSearch: parseOperatorAdsRouteSearch,
});

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
    page_size: 50,
  });
  const payload = query.data;
  const displayPayload = payload
    ? adsForRealtimeState(payload, realtimeStatus === "connected" && !query.isError)
    : null;
  const displayState = displayPayload?.state;
  // Порядок задаёт сервер, включая сортировку по близости к стопу: клиент
  // видит только текущую страницу и переупорядочивать её не должен.
  const displayRows = displayPayload?.rows;
  const currency = confirmedOperatorCurrency(displayPayload?.scope);
  const hasConfirmedCount = displayState === "ready" || displayState === "empty";
  const activeFilterCount =
    Number(Boolean(search.q)) +
    Number(Boolean(search.account_id)) +
    Number(Boolean(search.severity)) +
    Number(Boolean(search.sort && search.sort !== OPERATOR_ADS_STOP_PROXIMITY_SORT)) +
    Number(Boolean(search.direction && search.direction !== "desc"));

  useEffect(() => setDraftSearch(search.q ?? ""), [search.q]);

  function patchSearch(next: Partial<OperatorAdsRouteSearch>) {
    void navigate({ search: (previous) => ({ ...previous, ...next }), replace: true });
  }

  function submitSearch(event: FormEvent) {
    event.preventDefault();
    patchSearch({ q: draftSearch.trim() || undefined, page: undefined });
    setFiltersOpen(false);
  }

  function resetFilters() {
    setDraftSearch("");
    void navigate({ search: {}, replace: true });
  }

  if (query.isError && !payload) {
    return (
      <OperatorPageBoundary
        eyebrowNum="02"
        eyebrow="РЕКЛАМА · ОБЪЯВЛЕНИЯ"
        title="Объявления"
        subtitle="Серверный каталог, фильтры и команды"
      >
        <OperatorUnavailableState
          title="Объявления недоступны"
          resource="каталог объявлений"
          details={operatorProblemMessage(query.error)}
          onRetry={() => void query.refetch()}
        />
      </OperatorPageBoundary>
    );
  }

  return (
    <div className="mx-auto max-w-[1440px]">
      <header className="mb-5 flex flex-col gap-4 lg:flex-row lg:items-end lg:justify-between">
        <div>
          <Eyebrow num="04">УПРАВЛЕНИЕ · ОБЪЯВЛЕНИЯ</Eyebrow>
          <h1 className="m-0 mt-2 font-display text-[clamp(30px,4vw,44px)] font-medium text-bg-11">
            Объявления
          </h1>
          <p className="mt-2 max-w-2xl text-[16px] text-bg-9">
            Серверные поиск, фильтрация и сортировка. Для каждой команды отдельно видны постановка,
            выполнение и подтверждение.
          </p>
        </div>
        <div className="flex items-center gap-3">
          {displayState ? <DataStateBadge state={displayState} /> : null}
          <span className="font-numeric text-[14px] text-bg-9">
            {payload && hasConfirmedCount
              ? formatRussianCount(payload.total, "строка", "строки", "строк")
              : payload
                ? "— строк"
                : "Загрузка…"}
          </span>
        </div>
      </header>

      <div className="mb-4 md:hidden">
        <Button
          ref={filterTriggerRef}
          type="button"
          variant="secondary"
          className="min-h-11 w-full"
          leftIcon={<Filter aria-hidden="true" />}
          aria-label="Открыть фильтры объявлений"
          aria-haspopup="dialog"
          aria-expanded={filtersOpen}
          onClick={() => setFiltersOpen(true)}
        >
          Фильтры{activeFilterCount ? ` · ${activeFilterCount}` : ""}
        </Button>
        <Drawer
          open={filtersOpen}
          onOpenChange={setFiltersOpen}
          title="Фильтры объявлений"
          description="Поиск, кабинет, риск и сортировка"
          width={480}
          returnFocusRef={filterTriggerRef}
        >
          <AdsFilterFields
            search={search}
            draftSearch={draftSearch}
            cabinets={cabinets}
            onDraftSearch={setDraftSearch}
            onChange={patchSearch}
            onSubmit={submitSearch}
            stacked
          />
        </Drawer>
      </div>

      <section
        aria-label="Фильтры объявлений"
        className="mb-4 hidden rounded-[var(--radius-3)] border border-[var(--color-hairline)] bg-bg-1 p-4 md:block"
      >
        <AdsFilterFields
          search={search}
          draftSearch={draftSearch}
          cabinets={cabinets}
          onDraftSearch={setDraftSearch}
          onChange={patchSearch}
          onSubmit={submitSearch}
        />
      </section>

      {displayPayload && displayState && displayState !== "ready" ? (
        <div className="mb-4">
          <DataStateNotice state={displayState} issues={displayPayload.issues} />
        </div>
      ) : null}

      <section className="rounded-[var(--radius-3)] border border-[var(--color-hairline)] bg-bg-1 p-3 sm:p-4">
        {query.isPending && !payload ? (
          <div role="status" aria-label="Загрузка объявлений" className="grid gap-3">
            {Array.from({ length: 6 }, (_, index) => (
              <Skeleton key={index} className="h-20 w-full" />
            ))}
          </div>
        ) : displayRows?.length ? (
          <>
            <OperatorAdsTable rows={displayRows} currency={currency} />
            <OperatorAdCards rows={displayRows} currency={currency} />
          </>
        ) : displayState === "empty" ? (
          <EmptyState
            title="Объявлений не найдено"
            description={
              activeFilterCount
                ? "Сервер подтвердил пустой результат для выбранных условий."
                : "Сервер подтвердил, что в каталоге пока нет объявлений."
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
            title="Список не подтверждён"
            description="Дождитесь сверки live-снимка или обновите данные. Неподтверждённый результат не считается нулём."
          />
        )}
      </section>

      {displayPayload && displayPayload.pages > 1 ? (
        <nav
          aria-label="Страницы объявлений"
          className="mt-4 flex items-center justify-between gap-3"
        >
          <Button
            variant="secondary"
            className="min-h-11"
            leftIcon={<ChevronLeft aria-hidden="true" />}
            disabled={page <= 1 || query.isFetching}
            onClick={() => patchSearch({ page: page - 1 })}
          >
            Назад
          </Button>
          <span className="text-[14px] text-bg-9" aria-live="polite">
            Страница {page} из {displayPayload.pages}
          </span>
          <Button
            variant="secondary"
            className="min-h-11"
            rightIcon={<ChevronRight aria-hidden="true" />}
            disabled={page >= displayPayload.pages || query.isFetching}
            onClick={() => patchSearch({ page: page + 1 })}
          >
            Далее
          </Button>
        </nav>
      ) : null}
    </div>
  );
}

function Select({
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
    <label>
      <span className="sr-only">{label}</span>
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

function AdsFilterFields({
  search,
  draftSearch,
  cabinets,
  onDraftSearch,
  onChange,
  onSubmit,
  stacked = false,
}: {
  search: OperatorAdsRouteSearch;
  draftSearch: string;
  cabinets: OperatorCabinetOption[];
  onDraftSearch: (value: string) => void;
  onChange: (next: Partial<OperatorAdsRouteSearch>) => void;
  onSubmit: (event: FormEvent) => void;
  stacked?: boolean;
}) {
  return (
    <form
      onSubmit={onSubmit}
      className={
        stacked
          ? "grid gap-4"
          : "grid gap-3 lg:grid-cols-[minmax(240px,1fr)_180px_160px_180px_150px_auto]"
      }
    >
      <label className="relative block">
        <span className="sr-only">Поиск по объявлениям</span>
        <Search
          aria-hidden="true"
          className="absolute left-3 top-1/2 -translate-y-1/2 text-bg-8"
          size={16}
        />
        <input
          value={draftSearch}
          onChange={(event) => onDraftSearch(event.target.value)}
          placeholder="Название, кампания или ID"
          className="min-h-11 w-full rounded-[var(--radius-2)] border border-[var(--color-hairline-strong)] bg-bg-0 pl-10 pr-3 text-[16px] text-bg-11 outline-none placeholder:text-bg-8 focus:border-accent focus:ring-1 focus:ring-accent"
        />
      </label>
      <Select
        label="Кабинет"
        value={search.account_id ?? ""}
        onChange={(value) => onChange({ account_id: value || undefined, page: undefined })}
      >
        <option value="">Все кабинеты</option>
        {cabinets.map((cabinet) => (
          <option key={cabinet.value} value={cabinet.value}>
            {cabinet.label}
          </option>
        ))}
      </Select>
      <Select
        label="Риск"
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
      </Select>
      <Select
        label="Сортировка"
        value={search.sort ?? OPERATOR_ADS_STOP_PROXIMITY_SORT}
        onChange={(value) => onChange({ sort: value as OperatorAdsRouteSort, page: undefined })}
      >
        {SORTS.map((item) => (
          <option key={item.value} value={item.value}>
            {item.label}
          </option>
        ))}
      </Select>
      <Select
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
      </Select>
      <Button type="submit" variant="primary" className="min-h-11">
        Найти
      </Button>
    </form>
  );
}
