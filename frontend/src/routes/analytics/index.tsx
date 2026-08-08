import { createFileRoute, useNavigate } from "@tanstack/react-router";
import { Activity, Database, RefreshCw, Search } from "lucide-react";
import type { KeyboardEvent, ReactNode } from "react";
import type { AnalyticsPerformance } from "@fb/shared";
import { formatSpend } from "@fb/shared/format/number";
import { timezoneEvidenceLabel } from "@fb/shared/format/time";
import {
  analyticsWindowSafety,
  type AnalyticsWindowSafety,
} from "@fb/shared/analytics/windowSafety";
import type { DataState } from "@fb/shared/operator/contracts";
import { DataStateBadge } from "@fb/operator-ui";
import { useOperatorRealtimeStatus } from "@fb/operator-api";
import {
  analyticsPerformanceState,
  effectiveAnalyticsState,
  inheritAnalyticsState,
} from "@fb/shared/analytics/state";

import { PageHeader } from "@/components/layout/PageHeader";
import { Card } from "@/components/ui/Card";
import { Button } from "@/components/ui/Button";
import { ErrorState } from "@/components/ui/ErrorState";
import { BudgetLineChart } from "@/components/analytics/BudgetLineChart";
import { FunnelChart } from "@/components/analytics/FunnelChart";
import { DaypartHeatmap } from "@/components/analytics/DaypartHeatmap";
import { PerformanceTable } from "@/components/analytics/PerformanceTable";
import { HistoryTimeline } from "@/components/history/HistoryTimeline";
import {
  useAnalyticsDaypart,
  useAnalyticsLiveBudget,
  useAnalyticsPerformance,
  type AnalyticsPerformanceParams,
} from "@/lib/api/analytics";
import { useOperatorEvents } from "@/lib/api/operator";
import { formatDisplayDate, formatDisplayDateTime, resolveDisplayTimeZone } from "@/lib/timezone";
import { useUiStore } from "@/stores/ui";

type PeriodKey = "today" | "7d" | "30d" | "custom";
export const Route = createFileRoute("/analytics/")({
  component: AnalyticsPage,
  validateSearch: (search: Record<string, unknown>) => ({
    tab: search.tab === "events" ? ("events" as const) : ("uploads" as const),
    period: (["today", "7d", "30d", "custom"] as const).includes(search.period as PeriodKey)
      ? (search.period as PeriodKey)
      : ("today" as const),
    from_date: typeof search.from_date === "string" ? search.from_date : undefined,
    to_date: typeof search.to_date === "string" ? search.to_date : undefined,
    account_id: typeof search.account_id === "string" ? search.account_id : undefined,
    offer_id: typeof search.offer_id === "string" ? search.offer_id : undefined,
    campaign_id: typeof search.campaign_id === "string" ? search.campaign_id : undefined,
    search: typeof search.search === "string" ? search.search : undefined,
    sort: typeof search.sort === "string" ? search.sort : "spend",
    direction: search.direction === "asc" ? ("asc" as const) : ("desc" as const),
    page: typeof search.page === "number" && search.page > 0 ? search.page : 1,
    event_level: typeof search.event_level === "string" ? search.event_level : undefined,
    task_result: typeof search.task_result === "string" ? search.task_result : undefined,
  }),
});

type AnalyticsSearch = ReturnType<typeof Route.useSearch>;

function AnalyticsPage() {
  const search = Route.useSearch();
  const navigate = useNavigate({ from: "/analytics/" });
  const configuredTimeZone = useUiStore((state) => state.displayTimeZone);
  const displayTimeZone = resolveDisplayTimeZone(configuredTimeZone);
  const realtimeStatus = useOperatorRealtimeStatus();
  const performanceParams: AnalyticsPerformanceParams = {
    period: search.period,
    from_date: search.from_date,
    to_date: search.to_date,
    level: "campaign",
    account_id: search.account_id,
    offer_id: search.offer_id,
    campaign_id: search.campaign_id,
    search: search.search,
    sort: isPerformanceSort(search.sort) ? search.sort : "spend",
    direction: search.direction,
    page: search.page,
    page_size: 50,
  };
  const performanceQ = useAnalyticsPerformance(performanceParams);
  // Query selectors validate JSON at runtime. On any error, also hide cached
  // placeholder data so a broken refresh cannot look current or healthy.
  const performanceData = performanceQ.isError ? undefined : performanceQ.data;
  const windowSafety = analyticsWindowSafety(performanceData?.window);
  const performanceState = performanceData
    ? analyticsPerformanceState(performanceData, {
        realtimeConnected: realtimeStatus === "connected",
        placeholder: performanceQ.isPlaceholderData,
        refreshing: performanceQ.isFetching,
      })
    : "unavailable";
  const timeZone = performanceData ? performanceData.scope.display_timezone : displayTimeZone;
  const budgetQ = useAnalyticsLiveBudget(
    {
      account_id: search.account_id,
      offer_id: search.offer_id,
      campaign_id: search.campaign_id,
    },
    search.period === "today" && search.tab === "uploads" && !performanceQ.isError,
  );
  const daypartQ = useAnalyticsDaypart(
    {
      from_iso: performanceData?.window.from_iso,
      to_iso: performanceData?.window.to_iso,
      account_id: search.account_id,
      offer_id: search.offer_id,
      campaign_id: search.campaign_id,
    },
    search.tab === "uploads" &&
      search.period !== "today" &&
      periodDays(search.period, search.from_date, search.to_date) >= 7 &&
      Boolean(performanceData) &&
      !performanceQ.isError,
  );

  const setSearch = (patch: Partial<typeof search>) =>
    void navigate({ search: (previous) => ({ ...previous, ...patch }), replace: true });

  const selectPeriod = (period: PeriodKey) => {
    const next =
      period === "custom"
        ? defaultCalendarDates(search.from_date, search.to_date)
        : { from_date: undefined, to_date: undefined };
    setSearch({
      period,
      from_date: next.from_date,
      to_date: next.to_date,
      page: 1,
    });
  };

  const handleSort = (sort: NonNullable<AnalyticsPerformanceParams["sort"]>) => {
    setSearch({
      sort,
      direction: search.sort === sort && search.direction === "desc" ? "asc" : "desc",
      page: 1,
    });
  };

  const selectTab = (tab: AnalyticsSearch["tab"], focus = false) => {
    setSearch({ tab });
    if (focus) {
      document.getElementById(`analytics-tab-${tab}`)?.focus();
    }
  };

  const handleTabKeyDown = (
    event: KeyboardEvent<HTMLButtonElement>,
    tab: AnalyticsSearch["tab"],
  ) => {
    let next: AnalyticsSearch["tab"] | null = null;
    if (event.key === "ArrowRight" || event.key === "ArrowDown") {
      next = tab === "uploads" ? "events" : "uploads";
    } else if (event.key === "ArrowLeft" || event.key === "ArrowUp") {
      next = tab === "events" ? "uploads" : "events";
    } else if (event.key === "Home") {
      next = "uploads";
    } else if (event.key === "End") {
      next = "events";
    }
    if (next === null) return;
    event.preventDefault();
    selectTab(next, true);
  };

  return (
    <div className="min-w-0">
      <PageHeader
        eyebrowNum="05"
        eyebrow="PERFORMANCE · META × TRACKER"
        title="Аналитика"
        subtitle={
          search.period === "today"
            ? performanceData
              ? `Сутки рекламного кабинета · ${timezoneEvidenceLabel(
                  performanceData.scope.cabinet_timezone,
                  performanceData.scope.cabinet_timezone_state,
                )}${!windowSafety.timezoneKnown ? " · оценочные границы" : ""}`
              : "Границы суток формирует сервер"
            : performanceData
              ? `${formatDisplayDate(performanceData.window.from_iso, timeZone)} — ${formatDisplayDate(performanceData.window.to_iso, timeZone)} · отображение ${timeZone}${!windowSafety.timezoneKnown ? " · границы оценочные" : ""}`
              : "Границы периода формирует сервер"
        }
      />

      <div
        className="mb-5 flex items-center gap-1 border-b border-[var(--color-hairline)]"
        role="tablist"
        aria-label="Раздел аналитики"
      >
        <TabButton
          id="analytics-tab-uploads"
          controls="analytics-panel-uploads"
          active={search.tab === "uploads"}
          onClick={() => selectTab("uploads")}
          onKeyDown={(event) => handleTabKeyDown(event, "uploads")}
        >
          Заливы
        </TabButton>
        <TabButton
          id="analytics-tab-events"
          controls="analytics-panel-events"
          active={search.tab === "events"}
          onClick={() => selectTab("events")}
          onKeyDown={(event) => handleTabKeyDown(event, "events")}
        >
          События
        </TabButton>
      </div>

      <AnalyticsToolbar
        search={search}
        options={performanceData?.filter_options}
        onPeriod={selectPeriod}
        onChange={setSearch}
      />

      {performanceData ? (
        <SourceQuality
          data={performanceData.sources}
          scope={performanceData.scope}
          asOf={performanceData.as_of}
          freshnessSeconds={performanceData.freshness_seconds}
          timeZone={timeZone}
          windowSafety={windowSafety}
          state={performanceState}
        />
      ) : null}

      {search.tab === "uploads" ? (
        <div
          id="analytics-panel-uploads"
          role="tabpanel"
          aria-labelledby="analytics-tab-uploads"
          tabIndex={0}
        >
          {performanceQ.isError ? (
            <div
              data-state="unavailable"
              className="flex flex-col gap-3 rounded-[var(--radius-3)] border border-danger/30 bg-danger-bg/20 p-4"
            >
              <div className="flex items-center justify-between gap-3">
                <span className="font-display text-[14px] font-semibold text-bg-11">
                  Состояние данных
                </span>
                <DataStateBadge state="unavailable" />
              </div>
              <ErrorState
                title="Аналитика недоступна. Неподтверждённые данные скрыты."
                error={performanceQ.error}
                onRetry={() => void performanceQ.refetch()}
              />
            </div>
          ) : (
            <UploadsView
              performanceQ={performanceQ}
              budgetQ={budgetQ}
              daypartQ={daypartQ}
              params={performanceParams}
              period={search.period}
              timeZone={timeZone}
              windowSafety={windowSafety}
              performanceState={performanceState}
              onSort={handleSort}
              onPage={(page) => setSearch({ page })}
            />
          )}
        </div>
      ) : (
        <div
          id="analytics-panel-events"
          role="tabpanel"
          aria-labelledby="analytics-tab-events"
          tabIndex={0}
        >
          <EventsView
            search={search}
            realtimeStatus={realtimeStatus}
            timeZone={timeZone}
            onChange={setSearch}
            onOpenAd={(fbAdId) => setSearch({ tab: "uploads", search: fbAdId, page: 1 })}
          />
        </div>
      )}
    </div>
  );
}

function UploadsView({
  performanceQ,
  budgetQ,
  daypartQ,
  params,
  period,
  timeZone,
  windowSafety,
  performanceState,
  onSort,
  onPage,
}: {
  performanceQ: ReturnType<typeof useAnalyticsPerformance>;
  budgetQ: ReturnType<typeof useAnalyticsLiveBudget>;
  daypartQ: ReturnType<typeof useAnalyticsDaypart>;
  params: AnalyticsPerformanceParams;
  period: PeriodKey;
  timeZone: string;
  windowSafety: AnalyticsWindowSafety;
  performanceState: DataState;
  onSort: (sort: NonNullable<AnalyticsPerformanceParams["sort"]>) => void;
  onPage: (page: number) => void;
}) {
  const data = performanceQ.data;
  const totals = data?.totals;
  const totalBudget = data?.total_live_budget;
  const completeness = performanceState;
  const sourceNames = data ? analyticsSourceNames(data.sources, completeness) : [];
  const totalsAvailable = completeness !== "unavailable";
  const confirmedTone = completeness === "ready";
  const currency = confirmedCurrency(data?.scope);
  const realtimeConnected = useOperatorRealtimeStatus() === "connected";
  const budgetParentState = inheritAnalyticsState(
    effectiveAnalyticsState(performanceState, {
      realtimeConnected,
      refreshing: budgetQ.isFetching,
    }),
    performanceState,
  );
  const daypartParentState = inheritAnalyticsState(
    effectiveAnalyticsState(performanceState, {
      realtimeConnected,
      refreshing: daypartQ.isFetching,
    }),
    performanceState,
  );
  return (
    <div className="flex flex-col gap-5">
      {data ? (
        <div className="flex flex-col gap-3 rounded-[var(--radius-3)] border border-[var(--color-hairline)] bg-bg-1 p-4 sm:flex-row sm:items-center sm:justify-between">
          <div>
            <div className="font-display text-[14px] font-semibold text-bg-11">
              Точность аналитического окна
            </div>
            <p className="m-0 mt-1 text-[14px] text-bg-9">
              {analyticsWindowEvidence(data, completeness, windowSafety)}
            </p>
          </div>
          <DataStateBadge state={completeness} />
        </div>
      ) : null}
      <div className="grid grid-cols-2 border border-[var(--color-hairline)] bg-bg-1 lg:grid-cols-6">
        <Metric
          label="Расход"
          value={totalsAvailable ? formatSpend(totals?.spend, currency) : "—"}
        />
        <Metric label="Клики" value={totalsAvailable ? integer(totals?.clicks) : "—"} />
        <Metric
          label="Регистрации"
          value={totalsAvailable ? integer(totals?.registrations) : "—"}
          accent={confirmedTone}
        />
        <Metric
          label="FTD"
          value={totalsAvailable ? integer(totals?.ftds) : "—"}
          accent={confirmedTone}
        />
        <Metric
          label="Подтв. депозиты"
          value={totalsAvailable ? integer(totals?.confirmed_deposits) : "—"}
          accent={confirmedTone}
        />
        <Metric
          label={period === "today" ? "Δ от базы" : "Выручка"}
          value={
            !totalsAvailable
              ? "—"
              : period === "today"
                ? signedCurrency(totalBudget?.base_delta, currency)
                : formatSpend(totals?.revenue, currency)
          }
          tone={
            period === "today" && confirmedTone && Number(totalBudget?.base_delta ?? 0) > 0
              ? "danger"
              : "default"
          }
          hint={data?.total_budget_unavailable_reason}
        />
      </div>

      <div className="grid gap-5 xl:grid-cols-[minmax(0,1.35fr)_minmax(330px,0.65fr)]">
        <Card padded className="min-w-0 p-5">
          <SectionHeader
            title={period === "today" ? "Факт / база / stop" : "Экономика периода"}
            meta={
              period === "today"
                ? "LIVE · почасовая шкала"
                : "Budget delta не моделируется задним числом"
            }
          />
          {period === "today" ? (
            budgetQ.isError ? (
              <ErrorState
                title="График бюджета недоступен. Неподтверждённые точки скрыты."
                error={budgetQ.error}
                onRetry={() => void budgetQ.refetch()}
              />
            ) : (
              <BudgetLineChart
                data={budgetQ.data}
                loading={budgetQ.isLoading}
                timezone={timeZone}
                parentState={budgetParentState}
              />
            )
          ) : (
            <div className="flex h-[260px] items-center justify-center px-6 text-center text-[12px] text-bg-8">
              Исторический budget delta скрыт: правила оффера не версионируются, поэтому пересчет
              задним числом был бы недостоверным.
            </div>
          )}
        </Card>
        <Card padded className="p-5">
          <SectionHeader title="Воронка" meta="Meta → AdSet.pro" />
          <div className="pt-5">
            <FunnelChart
              clicks={totalsAvailable && totals ? totals.clicks : null}
              registrations={totalsAvailable && totals ? totals.registrations : null}
              ftds={totalsAvailable && totals ? totals.ftds : null}
              confirmedDeposits={totalsAvailable && totals ? totals.confirmed_deposits : null}
              spend={totalsAvailable ? (totals?.spend ?? null) : null}
              currency={currency}
              timezone={timeZone}
              asOf={data?.window.to_iso ?? null}
              completeness={completeness}
              sources={sourceNames}
            />
          </div>
        </Card>
      </div>

      {period !== "today" && daypartQ.isError ? (
        <Card padded className="p-5">
          <ErrorState
            title="Почасовое распределение недоступно. Неподтверждённые ячейки скрыты."
            error={daypartQ.error}
            onRetry={() => void daypartQ.refetch()}
          />
        </Card>
      ) : period !== "today" && daypartQ.data ? (
        <Card padded className="p-5">
          <SectionHeader title="Когда трафик конвертит" meta="HEATMAP · локальное отображение" />
          <DaypartHeatmap data={daypartQ.data} parentState={daypartParentState} />
        </Card>
      ) : null}

      <Card padded={false} className="min-w-0 overflow-hidden">
        <div className="flex items-center justify-between gap-4 border-b border-[var(--color-hairline)] px-5 py-4">
          <SectionHeader
            title="Результат по заливам"
            meta="Кампания → адсет → объявление"
            compact
          />
          <Button
            variant="ghost"
            size="sm"
            leftIcon={<RefreshCw size={13} />}
            onClick={() => void performanceQ.refetch()}
            loading={performanceQ.isFetching}
          >
            Обновить
          </Button>
        </div>
        <PerformanceTable
          rows={data?.rows}
          loading={performanceQ.isLoading}
          parentState={completeness}
          currency={currency}
          params={params}
          onSort={onSort}
        />
        {data && data.pagination.pages > 1 ? (
          <div className="flex items-center justify-between border-t border-[var(--color-hairline)] px-5 py-3 text-[14px] text-bg-8">
            <span>{data.pagination.total} строк</span>
            <div className="flex items-center gap-2">
              <Button
                size="sm"
                variant="ghost"
                disabled={data.pagination.page <= 1}
                onClick={() => onPage(data.pagination.page - 1)}
              >
                Назад
              </Button>
              <span className="font-display tabular-nums">
                {data.pagination.page} / {data.pagination.pages}
              </span>
              <Button
                size="sm"
                variant="ghost"
                disabled={data.pagination.page >= data.pagination.pages}
                onClick={() => onPage(data.pagination.page + 1)}
              >
                Далее
              </Button>
            </div>
          </div>
        ) : null}
      </Card>
    </div>
  );
}

function EventsView({
  search,
  realtimeStatus,
  timeZone,
  onChange,
  onOpenAd,
}: {
  search: AnalyticsSearch;
  realtimeStatus: ReturnType<typeof useOperatorRealtimeStatus>;
  timeZone: string;
  onChange: (patch: Partial<AnalyticsSearch>) => void;
  onOpenAd: (fbAdId: string) => void;
}) {
  const query = useOperatorEvents({
    period: search.period,
    from_date: search.from_date,
    to_date: search.to_date,
    campaign_id: search.campaign_id,
    stage: search.event_level,
    task_status: search.task_result,
    search: search.search,
    limit: 500,
  });
  const items = query.data ?? [];
  const state: DataState = query.isError
    ? "unavailable"
    : realtimeStatus !== "connected" || query.isFetching
      ? "stale"
      : items.length > 0
        ? "ready"
        : "empty";
  return (
    <div className="grid gap-5 xl:grid-cols-[260px_minmax(0,1fr)]">
      <Card padded className="h-fit p-4">
        <SectionHeader title="Фильтры событий" meta="до 500 записей" compact />
        <div className="mt-4 flex flex-col gap-3">
          <SelectField
            value={search.event_level ?? ""}
            onChange={(value) => onChange({ event_level: value || undefined })}
            options={[
              { value: "warning", label: "WARNING" },
              { value: "stop", label: "STOP" },
            ]}
            placeholder="Любой уровень"
          />
          <SelectField
            value={search.task_result ?? ""}
            onChange={(value) => onChange({ task_result: value || undefined })}
            options={[
              { value: "SUCCEEDED", label: "Успешно" },
              { value: "FAILED", label: "Ошибка" },
              { value: "CANCELLED", label: "Отменено" },
            ]}
            placeholder="Любой результат"
          />
          <p className="m-0 text-[12px] leading-5 text-bg-8">
            Нажмите событие объявления, чтобы открыть его статистику в «Заливах» с готовым фильтром.
          </p>
        </div>
      </Card>
      <div className="grid min-w-0 content-start gap-3" data-state={state}>
        {state === "stale" ? (
          <div
            role="status"
            className="flex items-center justify-between gap-3 rounded-[var(--radius-2)] border border-warning/30 bg-warning-bg/20 px-4 py-3"
          >
            <span className="text-[14px] text-bg-10">
              Лента сверяется с журналом событий. Показанные записи считаются устаревшими.
            </span>
            <DataStateBadge state="stale" />
          </div>
        ) : null}
        <HistoryTimeline
          items={items}
          isLoading={query.isLoading && !query.data}
          error={query.error}
          timeZone={timeZone}
          onRetry={() => void query.refetch()}
          onAlertClick={(item) => item.fb_ad_id && onOpenAd(item.fb_ad_id)}
        />
      </div>
    </div>
  );
}

function AnalyticsToolbar({
  search,
  options,
  onPeriod,
  onChange,
}: {
  search: AnalyticsSearch;
  options?: AnalyticsPerformance["filter_options"];
  onPeriod: (period: PeriodKey) => void;
  onChange: (patch: Partial<AnalyticsSearch>) => void;
}) {
  return (
    <Card padded className="mb-4 p-3">
      <div className="flex flex-wrap items-center gap-2">
        <div
          className="flex rounded-[var(--radius-2)] border border-[var(--color-hairline)] p-0.5"
          role="group"
          aria-label="Период аналитики"
        >
          {(["today", "7d", "30d", "custom"] as PeriodKey[]).map((period) => (
            <button
              key={period}
              type="button"
              aria-pressed={search.period === period}
              onClick={() => onPeriod(period)}
              className={`min-h-11 min-w-11 rounded-[var(--radius-1)] px-3 py-2 text-[14px] ${search.period === period ? "bg-bg-3 text-bg-11" : "text-bg-8 hover:text-bg-11"}`}
            >
              {period === "today" ? "Сегодня" : period === "custom" ? "Свой период" : period}
            </button>
          ))}
        </div>
        {search.period === "custom" ? (
          <>
            <input
              type="date"
              value={search.from_date ?? ""}
              onChange={(event) => onChange({ from_date: event.target.value, page: 1 })}
              aria-label="Начало периода"
              className="min-h-11 rounded-[var(--radius-2)] border border-[var(--color-hairline)] bg-bg-0 px-3 text-[14px] text-bg-10"
            />
            <input
              type="date"
              value={search.to_date ?? ""}
              onChange={(event) => onChange({ to_date: event.target.value, page: 1 })}
              aria-label="Конец периода"
              className="min-h-11 rounded-[var(--radius-2)] border border-[var(--color-hairline)] bg-bg-0 px-3 text-[14px] text-bg-10"
            />
          </>
        ) : null}
        <div className="relative min-w-[190px] flex-1 lg:max-w-[320px]">
          <Search
            size={13}
            className="pointer-events-none absolute left-2.5 top-1/2 -translate-y-1/2 text-bg-8"
          />
          <input
            value={search.search ?? ""}
            onChange={(event) => onChange({ search: event.target.value || undefined, page: 1 })}
            placeholder="Кампания, адсет или ad ID"
            aria-label="Поиск в аналитике"
            className="min-h-11 w-full rounded-[var(--radius-2)] border border-[var(--color-hairline)] bg-bg-0 pl-9 pr-3 text-[16px] text-bg-11 outline-none focus:border-accent"
          />
        </div>
        <SelectField
          value={search.account_id ?? ""}
          onChange={(value) => onChange({ account_id: value || undefined, page: 1 })}
          options={options?.accounts ?? []}
          placeholder="Все кабинеты"
        />
        <SelectField
          value={search.offer_id ?? ""}
          onChange={(value) => onChange({ offer_id: value || undefined, page: 1 })}
          options={options?.offers ?? []}
          placeholder="Все офферы"
        />
        <SelectField
          value={search.campaign_id ?? ""}
          onChange={(value) => onChange({ campaign_id: value || undefined, page: 1 })}
          options={options?.campaigns ?? []}
          placeholder="Все кампании"
        />
      </div>
    </Card>
  );
}

function SourceQuality({
  data,
  scope,
  asOf,
  freshnessSeconds,
  timeZone,
  windowSafety,
  state,
}: {
  data: AnalyticsPerformance["sources"];
  scope: AnalyticsPerformance["scope"];
  asOf: string | null;
  freshnessSeconds: number | null;
  timeZone: string;
  windowSafety: AnalyticsWindowSafety;
  state: DataState;
}) {
  const timezoneStatus =
    state === "ready" && windowSafety.timezoneKnown
      ? "good"
      : state === "partial"
        ? "degraded"
        : "unknown";
  const timezoneLabel =
    state === "ready"
      ? windowSafety.timezoneKnown
        ? "подтверждён"
        : "оценка"
      : state === "partial"
        ? "частично"
        : state === "stale"
          ? "снимок устарел"
          : state === "empty"
            ? "пустой снимок"
            : "не подтверждён";
  return (
    <div className="mb-5 flex flex-wrap items-center gap-x-5 gap-y-2 border-y border-[var(--color-hairline)] px-1 py-3 text-[12px] text-bg-8">
      <span
        className="inline-flex min-h-7 items-center gap-2"
        data-testid="analytics-freshness"
        data-source-status={
          state === "ready" ? "good" : state === "partial" ? "degraded" : "unknown"
        }
      >
        <strong className="font-display font-medium text-bg-9">СНИМОК</strong>
        <span>
          {formatDisplayDateTime(asOf, timeZone)} · свежесть {formatFreshness(freshnessSeconds)}
        </span>
      </span>
      <SourceItem
        icon={<Database size={14} />}
        label="META"
        data={data.meta}
        timeZone={timeZone}
        parentState={state}
      />
      <SourceItem
        icon={<Activity size={14} />}
        label="ADSET.PRO"
        data={data.tracker}
        timeZone={timeZone}
        parentState={state}
      />
      <span className="inline-flex min-h-7 items-center gap-2" data-source-status={timezoneStatus}>
        <span
          aria-hidden="true"
          className={`size-2 rounded-full ${
            timezoneStatus === "good"
              ? "bg-success"
              : timezoneStatus === "degraded"
                ? "bg-warning"
                : "bg-bg-6"
          }`}
        />
        <strong className="font-display font-medium text-bg-9">TIMEZONE</strong>
        <span>{`${timezoneLabel} · ${timezoneEvidenceLabel(
          scope.cabinet_timezone,
          scope.cabinet_timezone_state,
        )}`}</span>
      </span>
      <span
        className="inline-flex min-h-7 items-center gap-2"
        data-source-status={currencyEvidenceStatus(scope, state)}
      >
        <span
          aria-hidden="true"
          className={`size-2 rounded-full ${
            currencyEvidenceStatus(scope, state) === "good"
              ? "bg-success"
              : currencyEvidenceStatus(scope, state) === "degraded"
                ? "bg-warning"
                : "bg-bg-6"
          }`}
        />
        <strong className="font-display font-medium text-bg-9">CURRENCY</strong>
        <span>{currencyEvidenceLabel(scope, state)}</span>
      </span>
    </div>
  );
}

function SourceItem({
  icon,
  label,
  data,
  timeZone,
  parentState,
}: {
  icon: React.ReactNode;
  label: string;
  data: { status: string; last_event_at?: string | null; note?: string | null };
  timeZone: string;
  parentState: DataState;
}) {
  const effectiveStatus =
    parentState === "ready" ? data.status : parentState === "partial" ? "degraded" : "unknown";
  const color =
    effectiveStatus === "good"
      ? "bg-success"
      : effectiveStatus === "degraded"
        ? "bg-warning"
        : "bg-bg-6";
  const statusLabel =
    parentState === "ready"
      ? sourceStatusLabel(data.status)
      : parentState === "partial"
        ? "частично"
        : parentState === "stale"
          ? "снимок устарел"
          : parentState === "empty"
            ? "пустой снимок"
            : "не подтверждено";
  return (
    <span
      className="inline-flex items-center gap-1.5"
      title={data.note ?? undefined}
      data-source-status={effectiveStatus}
    >
      {icon}
      <strong className="font-display font-medium text-bg-9">{label}</strong>
      <span className={`size-2 rounded-full ${color}`} aria-hidden="true" />
      <span>{statusLabel}</span>
      <span>
        {data.last_event_at
          ? formatDisplayDateTime(data.last_event_at, timeZone)
          : (data.note ?? "нет событий")}
      </span>
    </span>
  );
}

function Metric({
  label,
  value,
  accent = false,
  tone = "default",
  hint,
}: {
  label: string;
  value: string;
  accent?: boolean;
  tone?: "default" | "danger";
  hint?: string | null;
}) {
  return (
    <div
      className="min-w-0 border-b border-r border-[var(--color-hairline)] px-4 py-3 lg:border-b-0"
      title={hint ?? undefined}
    >
      <div className="font-display text-[12px] uppercase tracking-[0.08em] text-bg-8">{label}</div>
      <div
        className={`mt-1 truncate font-display text-[18px] tabular-nums ${tone === "danger" ? "text-danger" : accent ? "text-accent" : "text-bg-11"}`}
      >
        {value}
      </div>
    </div>
  );
}

function SectionHeader({
  title,
  meta,
  compact = false,
}: {
  title: string;
  meta: string;
  compact?: boolean;
}) {
  return (
    <div className={compact ? "" : "mb-3"}>
      <h2 className="m-0 font-display text-[12px] font-medium uppercase tracking-[0.06em] text-bg-11">
        {title}
      </h2>
      <p className="m-0 mt-1 text-[12px] text-bg-8">{meta}</p>
    </div>
  );
}

function TabButton({
  id,
  controls,
  active,
  onClick,
  onKeyDown,
  children,
}: {
  id: string;
  controls: string;
  active: boolean;
  onClick: () => void;
  onKeyDown: (event: KeyboardEvent<HTMLButtonElement>) => void;
  children: ReactNode;
}) {
  return (
    <button
      id={id}
      type="button"
      role="tab"
      aria-controls={controls}
      aria-selected={active}
      tabIndex={active ? 0 : -1}
      onClick={onClick}
      onKeyDown={onKeyDown}
      className={`min-h-11 border-b-2 px-4 py-2 text-[14px] ${active ? "border-accent text-bg-11" : "border-transparent text-bg-8 hover:text-bg-11"}`}
    >
      {children}
    </button>
  );
}

function SelectField({
  value,
  onChange,
  options,
  placeholder,
}: {
  value: string;
  onChange: (value: string) => void;
  options: Array<{ value: string; label: string }>;
  placeholder: string;
}) {
  return (
    <select
      value={value}
      onChange={(event) => onChange(event.target.value)}
      aria-label={placeholder}
      className="min-h-11 max-w-[210px] rounded-[var(--radius-2)] border border-[var(--color-hairline)] bg-bg-0 px-3 text-[14px] text-bg-10"
    >
      <option value="">{placeholder}</option>
      {options.map((option) => (
        <option key={option.value} value={option.value}>
          {option.label}
        </option>
      ))}
    </select>
  );
}

function periodDays(period: PeriodKey, fromDate?: string, toDate?: string): number {
  if (period === "today") return 1;
  if (period === "7d") return 7;
  if (period === "30d") return 30;
  const from = parseCalendarDate(fromDate);
  const to = parseCalendarDate(toDate);
  return from !== null && to !== null ? Math.max(1, Math.round((to - from) / 86_400_000) + 1) : 0;
}

function isPerformanceSort(
  value: string,
): value is NonNullable<AnalyticsPerformanceParams["sort"]> {
  return [
    "name",
    "spend",
    "clicks",
    "registrations",
    "ftds",
    "confirmed_deposits",
    "revenue",
    "base_delta",
  ].includes(value);
}

function analyticsSourceNames(
  sources: AnalyticsPerformance["sources"],
  state: DataState,
): string[] {
  const suffix =
    state === "ready"
      ? null
      : state === "partial"
        ? "частично"
        : state === "stale"
          ? "снимок устарел"
          : state === "empty"
            ? "пустой снимок"
            : "не подтверждено";
  return [
    `Meta (${suffix ?? sourceStatusLabel(sources.meta.status)})`,
    `AdSet.pro (${suffix ?? sourceStatusLabel(sources.tracker.status)})`,
  ];
}

function currencyEvidenceStatus(
  scope: AnalyticsPerformance["scope"],
  state: DataState,
): "good" | "degraded" | "unknown" {
  if (state === "ready") {
    if (scope.currency_state === "single" && scope.currency === "USD") return "good";
    if (scope.currency_state === "single" && scope.currency !== "USD") return "degraded";
    return scope.currency_state === "mixed" ? "degraded" : "unknown";
  }
  if (
    state === "partial" &&
    (scope.currency_state === "single" || scope.currency_state === "mixed")
  ) {
    return "degraded";
  }
  return "unknown";
}

function currencyEvidenceLabel(scope: AnalyticsPerformance["scope"], state: DataState): string {
  if (scope.currency_state === "single" && scope.currency === "USD") {
    const label = "$";
    if (state === "ready") return `${label} · подтверждена`;
    if (state === "partial") return `${label} · снимок неполный`;
    if (state === "stale") return `${label} · снимок устарел`;
    if (state === "empty") return `${label} · пустой снимок`;
    return `${label} · не подтверждена`;
  }
  if (scope.currency_state === "single" && scope.currency !== "USD") {
    return "валюта не USD · денежные итоги скрыты";
  }
  if (scope.currency_state === "mixed") {
    return state === "stale"
      ? "несколько · снимок устарел · суммы скрыты"
      : "несколько · суммы скрыты";
  }
  return "не подтверждена · суммы скрыты";
}

function formatFreshness(seconds: number | null): string {
  if (seconds === null || seconds < 0) return "не подтверждена";
  if (seconds < 60) return `${Math.round(seconds)} сек`;
  if (seconds < 3_600) return `${Math.round(seconds / 60)} мин`;
  if (seconds < 86_400) return `${Math.round(seconds / 3_600)} ч`;
  return `${Math.round(seconds / 86_400)} дн`;
}

function sourceStatusLabel(status: string): string {
  if (status === "good") return "актуально";
  if (status === "degraded") return "частично";
  if (status === "missing") return "нет данных";
  return "неизвестно";
}

function analyticsWindowEvidence(
  data: AnalyticsPerformance,
  state: DataState,
  windowSafety: AnalyticsWindowSafety,
): string {
  if (state === "stale") {
    return `Снимок устарел; свежесть ${formatFreshness(data.freshness_seconds)}. Значения не считаются текущими.`;
  }
  if (state === "partial") {
    return (
      data.issues[0] ??
      windowSafety.issues[0] ??
      `Окно собрано не полностью; свежесть ${formatFreshness(data.freshness_seconds)}.`
    );
  }
  if (state === "empty") {
    return "Источники ответили: в выбранном аналитическом окне записей нет.";
  }
  if (state === "unavailable") {
    return "Точность окна не подтверждена; значения скрыты.";
  }
  if (!windowSafety.timezoneKnown) {
    return windowSafety.issues[0] ?? "Часовой пояс кабинета не подтверждён.";
  }
  return windowSafety.timezoneState === "mixed"
    ? "Границы суток подтверждены отдельно для каждого кабинета."
    : `Границы суток подтверждены для ${windowSafety.timezone}.`;
}

function signedCurrency(value: string | null | undefined, currency: string | null) {
  const number = Number(value);
  const formatted = formatSpend(value, currency);
  return value == null || !Number.isFinite(number) || formatted === "—"
    ? "—"
    : `${number > 0 ? "+" : ""}${formatted}`;
}
function confirmedCurrency(scope: AnalyticsPerformance["scope"] | undefined): string | null {
  return scope?.currency_state === "single" && scope.currency === "USD" ? "USD" : null;
}
function integer(value?: number | null) {
  return value == null ? "—" : new Intl.NumberFormat("ru-RU").format(value);
}
function defaultCalendarDates(
  fromDate?: string,
  toDate?: string,
): { from_date: string; to_date: string } {
  if (parseCalendarDate(fromDate) !== null && parseCalendarDate(toDate) !== null) {
    return { from_date: fromDate!, to_date: toDate! };
  }
  const today = new Date();
  const from = new Date(today);
  from.setDate(from.getDate() - 6);
  return {
    from_date: localCalendarDate(from),
    to_date: localCalendarDate(today),
  };
}

function parseCalendarDate(value?: string): number | null {
  if (!value || !/^\d{4}-\d{2}-\d{2}$/.test(value)) return null;
  const [year, month, day] = value.split("-").map(Number);
  const parsed = Date.UTC(year!, month! - 1, day!);
  const roundTrip = new Date(parsed).toISOString().slice(0, 10);
  return roundTrip === value ? parsed : null;
}

function localCalendarDate(value: Date): string {
  const year = value.getFullYear();
  const month = String(value.getMonth() + 1).padStart(2, "0");
  const day = String(value.getDate()).padStart(2, "0");
  return `${year}-${month}-${day}`;
}
