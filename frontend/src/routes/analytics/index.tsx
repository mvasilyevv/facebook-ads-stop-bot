import { useMemo } from "react";
import { createFileRoute, useNavigate } from "@tanstack/react-router";
import { Activity, Database, RefreshCw, Search } from "lucide-react";
import type { AnalyticsPerformance } from "@fb/shared";

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
import { useHistoryTimeline } from "@/lib/api/history";
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
    from_iso: typeof search.from_iso === "string" ? search.from_iso : undefined,
    to_iso: typeof search.to_iso === "string" ? search.to_iso : undefined,
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
  const timeZone = resolveDisplayTimeZone(configuredTimeZone);
  const window = useMemo(
    () => periodWindow(search.period, search.from_iso, search.to_iso),
    [search.period, search.from_iso, search.to_iso],
  );

  const performanceParams: AnalyticsPerformanceParams = {
    period: search.period === "today" ? "today" : "custom",
    from_iso: window.from_iso,
    to_iso: window.to_iso,
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
  const budgetQ = useAnalyticsLiveBudget(
    {
      account_id: search.account_id,
      offer_id: search.offer_id,
      campaign_id: search.campaign_id,
    },
    search.period === "today" && search.tab === "uploads",
  );
  const daypartQ = useAnalyticsDaypart(
    {
      from_iso: window.from_iso,
      to_iso: window.to_iso,
      timezone: timeZone,
      account_id: search.account_id,
      offer_id: search.offer_id,
      campaign_id: search.campaign_id,
    },
    search.tab === "uploads" && search.period !== "today" && window.days >= 7,
  );

  const setSearch = (patch: Partial<typeof search>) =>
    void navigate({ search: (previous) => ({ ...previous, ...patch }), replace: true });

  const selectPeriod = (period: PeriodKey) => {
    const next = periodWindow(period);
    setSearch({
      period,
      from_iso: next.from_iso,
      to_iso: next.to_iso,
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

  return (
    <div className="min-w-0">
      <PageHeader
        eyebrowNum="05"
        eyebrow="PERFORMANCE · META × TRACKER"
        title="Аналитика"
        subtitle={
          search.period === "today"
            ? `Сутки рекламного кабинета · отображение ${timeZone}`
            : `${formatDisplayDate(window.from_iso, timeZone)} — ${formatDisplayDate(window.to_iso, timeZone)} · ${timeZone}`
        }
      />

      <div className="mb-5 flex items-center gap-1 border-b border-[var(--hairline)]">
        <TabButton active={search.tab === "uploads"} onClick={() => setSearch({ tab: "uploads" })}>
          Заливы
        </TabButton>
        <TabButton active={search.tab === "events"} onClick={() => setSearch({ tab: "events" })}>
          События
        </TabButton>
      </div>

      <AnalyticsToolbar
        search={search}
        options={performanceQ.data?.filter_options}
        onPeriod={selectPeriod}
        onChange={setSearch}
      />

      {performanceQ.data ? <SourceQuality data={performanceQ.data.sources} /> : null}

      {search.tab === "uploads" ? (
        performanceQ.isError && !performanceQ.data ? (
          <ErrorState
            title="Не удалось загрузить аналитику."
            error={performanceQ.error}
            onRetry={() => void performanceQ.refetch()}
          />
        ) : (
          <UploadsView
            performanceQ={performanceQ}
            budgetQ={budgetQ}
            daypartQ={daypartQ}
            params={performanceParams}
            period={search.period}
            onSort={handleSort}
            onPage={(page) => setSearch({ page })}
          />
        )
      ) : (
        <EventsView
          window={window}
          search={search}
          onChange={setSearch}
          onOpenAd={(fbAdId) => setSearch({ tab: "uploads", search: fbAdId, page: 1 })}
        />
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
  onSort,
  onPage,
}: {
  performanceQ: ReturnType<typeof useAnalyticsPerformance>;
  budgetQ: ReturnType<typeof useAnalyticsLiveBudget>;
  daypartQ: ReturnType<typeof useAnalyticsDaypart>;
  params: AnalyticsPerformanceParams;
  period: PeriodKey;
  onSort: (sort: NonNullable<AnalyticsPerformanceParams["sort"]>) => void;
  onPage: (page: number) => void;
}) {
  const data = performanceQ.data;
  const totals = data?.totals;
  const totalBudget = data?.total_live_budget;
  return (
    <div className="flex flex-col gap-5">
      <div className="grid grid-cols-2 border border-[var(--hairline)] bg-bg-1 lg:grid-cols-6">
        <Metric label="Spend" value={currency(totals?.spend)} />
        <Metric label="Clicks" value={integer(totals?.clicks)} />
        <Metric label="Регистрации" value={integer(totals?.registrations)} accent />
        <Metric label="FTD" value={integer(totals?.ftds)} accent />
        <Metric label="Подтв. депозиты" value={integer(totals?.confirmed_deposits)} accent />
        <Metric
          label={period === "today" ? "Δ от базы" : "Revenue"}
          value={
            period === "today" ? signedCurrency(totalBudget?.base_delta) : currency(totals?.revenue)
          }
          tone={
            period === "today" && Number(totalBudget?.base_delta ?? 0) > 0 ? "danger" : "default"
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
            <BudgetLineChart data={budgetQ.data} loading={budgetQ.isLoading} />
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
              clicks={totals?.clicks ?? 0}
              registrations={totals?.registrations ?? 0}
              ftds={totals?.ftds ?? 0}
              confirmedDeposits={totals?.confirmed_deposits ?? 0}
            />
          </div>
        </Card>
      </div>

      {period !== "today" && daypartQ.data ? (
        <Card padded className="p-5">
          <SectionHeader title="Когда трафик конвертит" meta="HEATMAP · локальное отображение" />
          <DaypartHeatmap data={daypartQ.data} />
        </Card>
      ) : null}

      <Card padded={false} className="min-w-0 overflow-hidden">
        <div className="flex items-center justify-between gap-4 border-b border-[var(--hairline)] px-5 py-4">
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
          params={params}
          onSort={onSort}
        />
        {data && data.pagination.pages > 1 ? (
          <div className="flex items-center justify-between border-t border-[var(--hairline)] px-5 py-3 text-[11px] text-bg-8">
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
  window,
  search,
  onChange,
  onOpenAd,
}: {
  window: ReturnType<typeof periodWindow>;
  search: AnalyticsSearch;
  onChange: (patch: Partial<AnalyticsSearch>) => void;
  onOpenAd: (fbAdId: string) => void;
}) {
  const query = useHistoryTimeline({
    from_iso: window.from_iso,
    to_iso: window.to_iso,
    campaign_id: search.campaign_id,
    stage: search.event_level,
    task_status: search.task_result,
    search: search.search,
    limit: 500,
  });
  const items = query.data ?? [];
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
          <p className="m-0 text-[10px] leading-4 text-bg-7">
            Нажмите событие объявления, чтобы открыть его статистику в «Заливах» с готовым фильтром.
          </p>
        </div>
      </Card>
      <HistoryTimeline
        items={items}
        isLoading={query.isLoading}
        error={query.error}
        onRetry={() => void query.refetch()}
        onAlertClick={(item) => item.fb_ad_id && onOpenAd(item.fb_ad_id)}
      />
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
        <div className="flex rounded-[var(--radius-2)] border border-[var(--hairline)] p-0.5">
          {(["today", "7d", "30d", "custom"] as PeriodKey[]).map((period) => (
            <button
              key={period}
              type="button"
              onClick={() => onPeriod(period)}
              className={`rounded-[var(--radius-1)] px-3 py-1.5 text-[11px] ${search.period === period ? "bg-bg-3 text-bg-11" : "text-bg-8 hover:text-bg-11"}`}
            >
              {period === "today" ? "Сегодня" : period === "custom" ? "Свой период" : period}
            </button>
          ))}
        </div>
        {search.period === "custom" ? (
          <>
            <input
              type="date"
              value={isoDate(search.from_iso)}
              onChange={(event) =>
                onChange({ from_iso: dateStartIso(event.target.value), page: 1 })
              }
              className="h-8 rounded-[var(--radius-2)] border border-[var(--hairline)] bg-bg-0 px-2 text-[11px] text-bg-10"
            />
            <input
              type="date"
              value={isoDate(search.to_iso)}
              onChange={(event) => onChange({ to_iso: dateEndIso(event.target.value), page: 1 })}
              className="h-8 rounded-[var(--radius-2)] border border-[var(--hairline)] bg-bg-0 px-2 text-[11px] text-bg-10"
            />
          </>
        ) : null}
        <div className="relative min-w-[190px] flex-1 lg:max-w-[320px]">
          <Search
            size={13}
            className="pointer-events-none absolute left-2.5 top-1/2 -translate-y-1/2 text-bg-7"
          />
          <input
            value={search.search ?? ""}
            onChange={(event) => onChange({ search: event.target.value || undefined, page: 1 })}
            placeholder="Кампания, адсет или ad ID"
            className="h-8 w-full rounded-[var(--radius-2)] border border-[var(--hairline)] bg-bg-0 pl-8 pr-3 text-[11px] text-bg-11 outline-none focus:border-accent"
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

function SourceQuality({ data }: { data: AnalyticsPerformance["sources"] }) {
  return (
    <div className="mb-5 flex flex-wrap items-center gap-x-5 gap-y-2 border-y border-[var(--hairline)] px-1 py-2.5 text-[10px] text-bg-8">
      <SourceItem icon={<Database size={12} />} label="META" data={data.meta} />
      <SourceItem icon={<Activity size={12} />} label="ADSET.PRO" data={data.tracker} />
    </div>
  );
}

function SourceItem({
  icon,
  label,
  data,
}: {
  icon: React.ReactNode;
  label: string;
  data: { status: string; last_event_at?: string | null; note?: string | null };
}) {
  const color =
    data.status === "good" ? "bg-success" : data.status === "degraded" ? "bg-warning" : "bg-bg-6";
  return (
    <span className="inline-flex items-center gap-1.5" title={data.note ?? undefined}>
      {icon}
      <strong className="font-display font-medium text-bg-9">{label}</strong>
      <span className={`size-1.5 rounded-full ${color}`} />
      <span>
        {data.last_event_at
          ? formatDisplayDateTime(data.last_event_at)
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
      className="min-w-0 border-b border-r border-[var(--hairline)] px-4 py-3 lg:border-b-0"
      title={hint ?? undefined}
    >
      <div className="font-display text-[9px] uppercase tracking-[0.08em] text-bg-7">{label}</div>
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
      <p className="m-0 mt-1 text-[10px] text-bg-7">{meta}</p>
    </div>
  );
}

function TabButton({
  active,
  onClick,
  children,
}: {
  active: boolean;
  onClick: () => void;
  children: React.ReactNode;
}) {
  return (
    <button
      type="button"
      onClick={onClick}
      className={`border-b-2 px-4 py-2 text-[12px] ${active ? "border-accent text-bg-11" : "border-transparent text-bg-8 hover:text-bg-11"}`}
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
      className="h-8 max-w-[210px] rounded-[var(--radius-2)] border border-[var(--hairline)] bg-bg-0 px-2 text-[11px] text-bg-10"
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

function periodWindow(period: PeriodKey, fromIso?: string, toIso?: string) {
  const now = new Date();
  if (period === "today") return { from_iso: undefined, to_iso: undefined, days: 1 };
  if (period === "custom" && fromIso && toIso) {
    return {
      from_iso: fromIso,
      to_iso: toIso,
      days: Math.max(1, (new Date(toIso).getTime() - new Date(fromIso).getTime()) / 86_400_000),
    };
  }
  const days = period === "7d" ? 7 : period === "30d" ? 30 : 7;
  return {
    from_iso: new Date(now.getTime() - days * 86_400_000).toISOString(),
    to_iso: now.toISOString(),
    days,
  };
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

function currency(value?: string | null) {
  return value == null
    ? "—"
    : new Intl.NumberFormat("en-US", { style: "currency", currency: "USD" }).format(Number(value));
}
function signedCurrency(value?: string | null) {
  const number = Number(value);
  return value == null || Number.isNaN(number) ? "—" : `${number > 0 ? "+" : ""}${currency(value)}`;
}
function integer(value?: number | null) {
  return value == null ? "—" : new Intl.NumberFormat("ru-RU").format(value);
}
function isoDate(value?: string) {
  return value ? value.slice(0, 10) : "";
}
function dateStartIso(value: string) {
  return value ? new Date(`${value}T00:00:00`).toISOString() : undefined;
}
function dateEndIso(value: string) {
  return value ? new Date(`${value}T23:59:59.999`).toISOString() : undefined;
}
