import { createFileRoute, Link, useNavigate } from "@tanstack/react-router";
import { Suspense, lazy } from "react";
import {
  safeApiProblemMessage,
  useOperatorRealtimeStatus,
} from "@fb/operator-api";
import type { AnalyticsPerformance } from "@fb/shared";
import {
  ANALYTICS_PRESETS,
  ANALYTICS_SECTIONS,
} from "@fb/shared/analytics/presentation";
import {
  parseAnalyticsRouteSearch,
  type AnalyticsPreset,
  type AnalyticsRouteSearch,
  type AnalyticsSection,
  type AnalyticsSort,
} from "@fb/shared/analytics/routeState";
import type { components } from "@fb/shared/api/generated";
import { formatInt, formatSpend } from "@fb/shared/format/number";
import {
  formatZonedDateTime,
  timezoneEvidenceLabel,
} from "@fb/shared/format/time";
import type { DataState } from "@fb/shared/operator/contracts";
import { analyticsWindowSafety } from "@fb/shared/analytics/windowSafety";
import {
  analyticsPerformanceState,
  effectiveAnalyticsState,
  inheritAnalyticsState,
} from "@fb/shared/analytics/state";
import { DataStateBadge } from "@fb/operator-ui";

import { MiniHeader } from "@/components/layout/MiniHeader";
import {
  Button,
  Card,
  ErrorState,
  Input,
  Select,
  Skeleton,
} from "@/components/ui";
import { operatorProblemMessage } from "@/lib/operatorApi";
import { haptic } from "@/lib/tg";
import { cn } from "@/lib/cn";
import {
  useTmaAnalyticsDaypart,
  useTmaAnalyticsEvents,
  useTmaAnalyticsLiveBudget,
  useTmaAnalyticsPerformance,
} from "@/features/analytics/api";
import { useOperatorDisplayPreference } from "@/lib/settingsApi";
import { AnalyticsStateNotice } from "@/features/analytics/AnalyticsStateNotice";
import {
  ANALYTICS_PERIODS,
  formatFreshness,
  performanceWindow,
  sourceStatusLabel,
  type AnalyticsPeriod,
} from "@/features/analytics/viewModel";

// Каждый блок тяжёлый (SVG-график + собственная интерактивная логика) и
// нужен только тогда, когда оператор реально смотрит соответствующий раздел
// — до этого момента чанк не грузится вовсе. Это также снижает основной
// JS-бандл мини-приложения, у которого почти не осталось запаса бюджета.
const LiveBudgetChart = lazy(() =>
  import("@/features/analytics/AnalyticsMiniCharts").then((mod) => ({
    default: mod.LiveBudgetChart,
  })),
);
const FunnelSummary = lazy(() =>
  import("@/features/analytics/AnalyticsMiniCharts").then((mod) => ({
    default: mod.FunnelSummary,
  })),
);
const DaypartDayChart = lazy(() =>
  import("@/features/analytics/DaypartDayChart").then((mod) => ({
    default: mod.DaypartDayChart,
  })),
);
const PerformanceCards = lazy(() =>
  import("@/features/analytics/PerformanceCards").then((mod) => ({
    default: mod.PerformanceCards,
  })),
);

export const Route = createFileRoute("/analytics/")({
  component: AnalyticsPage,
  validateSearch: parseAnalyticsRouteSearch,
});

function ChartFallback({ className = "h-56" }: { className?: string }) {
  return <Skeleton className={cn("w-full", className)} />;
}

const ANALYTICS_SORT_OPTIONS: Array<{
  value: AnalyticsSort;
  label: string;
}> = [
  { value: "spend", label: "Расход" },
  { value: "clicks", label: "Клики" },
  { value: "registrations", label: "Регистрации" },
  { value: "ftds", label: "FTD" },
  { value: "confirmed_deposits", label: "Депозиты" },
  { value: "revenue", label: "Выручка" },
  { value: "base_delta", label: "Δ базы" },
  { value: "name", label: "Название" },
];

function AnalyticsPage() {
  const search = Route.useSearch();
  const navigate = useNavigate({ from: "/analytics/" });
  const realtimeStatus = useOperatorRealtimeStatus();
  const displayPreferenceQ = useOperatorDisplayPreference();
  const window = performanceWindow(
    search.period,
    search.from_date,
    search.to_date,
  );

  const performanceParams = {
    ...window,
    level: "campaign" as const,
    account_id: search.account_id,
    offer_id: search.offer_id,
    campaign_id: search.campaign_id,
    search: search.search,
    sort: search.sort,
    direction: search.direction,
    page: search.page,
    page_size: 20,
  };
  const performanceQ = useTmaAnalyticsPerformance(performanceParams);
  // A failed selector/request must not leave placeholder values looking usable.
  const performanceData = performanceQ.isError ? undefined : performanceQ.data;
  const windowSafety = analyticsWindowSafety(performanceData?.window);
  const realtimeConnected = realtimeStatus === "connected";
  const overallState = performanceData
    ? analyticsPerformanceState(performanceData, {
        realtimeConnected,
        placeholder: performanceQ.isPlaceholderData,
        refreshing: performanceQ.isFetching,
      })
    : "unavailable";
  const liveBudgetQ = useTmaAnalyticsLiveBudget(
    {
      account_id: search.account_id,
      offer_id: search.offer_id,
      campaign_id: search.campaign_id,
    },
    search.period === "today" &&
      Boolean(performanceData) &&
      !performanceQ.isError,
  );
  const liveBudgetData = liveBudgetQ.isError ? undefined : liveBudgetQ.data;
  const liveBudgetState = liveBudgetData
    ? inheritAnalyticsState(
        effectiveAnalyticsState(liveBudgetData.state, {
          realtimeConnected,
          refreshing: liveBudgetQ.isFetching,
          windowKnown: windowSafety.timezoneKnown,
        }),
        overallState,
      )
    : "unavailable";
  const eventsQ = useTmaAnalyticsEvents({
    period: search.period,
    from_date: search.from_date,
    to_date: search.to_date,
    campaign_id: search.campaign_id,
    stage: search.event_level,
    task_status: search.task_result,
    search: search.search,
    limit: 50,
  });
  const eventsState: DataState = eventsQ.isError
    ? "unavailable"
    : !realtimeConnected || eventsQ.isFetching
      ? "stale"
      : (eventsQ.data?.length ?? 0) > 0
        ? "ready"
        : "empty";

  const daypartEnabled =
    Boolean(performanceData) &&
    !performanceQ.isError &&
    performanceData?.state !== "unavailable";
  const daypartQ = useTmaAnalyticsDaypart(
    {
      from_iso: performanceData?.window.from_iso,
      to_iso: performanceData?.window.to_iso,
      account_id: search.account_id,
      offer_id: search.offer_id,
      campaign_id: search.campaign_id,
    },
    daypartEnabled,
  );
  const daypartData = daypartQ.isError ? undefined : daypartQ.data;
  const daypartState = daypartData
    ? inheritAnalyticsState(
        effectiveAnalyticsState(daypartData.state, {
          realtimeConnected,
          placeholder: daypartQ.isPlaceholderData,
          refreshing: daypartQ.isFetching,
          windowKnown: windowSafety.timezoneKnown,
        }),
        overallState,
      )
    : "unavailable";

  const patchSearch = (patch: Partial<AnalyticsRouteSearch>) => {
    void navigate({
      search: (previous) => ({ ...previous, ...patch }),
      replace: true,
    });
  };

  // Раздел живёт в URL как ?section= (тот же ключ, что и на вебе), так что
  // ссылка на конкретный график открывает тот же раздел, а переключение не
  // трогает период и фильтры.
  const selectSection = (section: AnalyticsSection) => {
    haptic.selection();
    patchSearch({ section });
  };

  const selectPeriod = (next: AnalyticsPeriod) => {
    haptic.selection();
    const dates =
      next === "custom"
        ? defaultCalendarDates(search.from_date, search.to_date)
        : { from_date: undefined, to_date: undefined };
    patchSearch({
      period: next,
      from_date: dates.from_date,
      to_date: dates.to_date,
      page: 1,
    });
  };

  const focusCampaign = (campaignId: string) => {
    haptic.selection();
    patchSearch({ campaign_id: campaignId, page: 1 });
    globalThis.scrollTo?.({ top: 0, behavior: "smooth" });
  };

  if (displayPreferenceQ.isPending) {
    return (
      <div className="flex min-h-full flex-col pb-20">
        <MiniHeader
          eyebrowNum="03"
          eyebrow="META × TRACKER"
          title="Аналитика"
        />
        <section className="grid gap-4 p-4" aria-label="Аналитика">
          <Card>
            <p role="status" className="m-0 text-[14px] text-bg-8">
              Подготавливаем подписи времени…
            </p>
          </Card>
        </section>
      </div>
    );
  }

  if (displayPreferenceQ.isError || !displayPreferenceQ.data) {
    return (
      <div className="flex min-h-full flex-col pb-20">
        <MiniHeader
          eyebrowNum="03"
          eyebrow="META × TRACKER"
          title="Аналитика"
        />
        <section className="grid gap-4 p-4" aria-label="Аналитика">
          <Card data-state="unavailable">
            <ErrorState
              message={safeApiProblemMessage(
                displayPreferenceQ.error,
                "Timezone отображения не подтверждён сервером. Откройте настройки или повторите запрос.",
              )}
              onRetry={() => void displayPreferenceQ.refetch()}
            />
          </Card>
        </section>
      </div>
    );
  }

  const displayTimeZone = displayPreferenceQ.data.timezone_name;

  return (
    <div className="flex min-h-full flex-col pb-20">
      <MiniHeader
        eyebrowNum="03"
        eyebrow="META × TRACKER"
        title="Аналитика"
        right={
          performanceData ? (
            <DataStateBadge state={overallState} compact />
          ) : null
        }
      />

      <section className="grid gap-4 p-4" aria-label="Аналитика">
        <AnalyticsFiltersPanel
          search={search}
          options={performanceData?.filter_options}
          onPeriod={selectPeriod}
          onChange={(patch) => patchSearch({ ...patch, page: 1 })}
          onReset={() => {
            haptic.selection();
            patchSearch({
              account_id: undefined,
              offer_id: undefined,
              campaign_id: undefined,
              search: undefined,
              sort: "spend",
              direction: "desc",
              page: 1,
            });
          }}
        />

        {performanceQ.isPending && !performanceData ? (
          <AnalyticsSkeleton />
        ) : performanceQ.isError || !performanceData ? (
          <Card data-state="unavailable">
            <div className="mb-2 flex items-center justify-between gap-3">
              <h2 className="m-0 font-display text-[14px] text-bg-11">
                Состояние данных
              </h2>
              <DataStateBadge state="unavailable" compact />
            </div>
            <ErrorState
              message={`${operatorProblemMessage(performanceQ.error)}. Неподтверждённые значения скрыты.`}
              onRetry={() => void performanceQ.refetch()}
            />
          </Card>
        ) : (
          <>
            <SourceEvidence
              data={performanceData}
              state={overallState}
              timezone={displayTimeZone}
              timezoneKnown={windowSafety.timezoneKnown}
              refreshing={performanceQ.isFetching}
              onRefresh={() => {
                haptic.impact("light");
                void Promise.all([
                  performanceQ.refetch(),
                  eventsQ.refetch(),
                  ...(search.period === "today" ? [liveBudgetQ.refetch()] : []),
                  ...(daypartEnabled ? [daypartQ.refetch()] : []),
                ]);
              }}
            />

            {overallState !== "ready" ? (
              <AnalyticsStateNotice
                state={overallState}
                issue={
                  performanceData.issues[0] ||
                  (windowSafety.state === "partial"
                    ? windowSafety.issues[0]
                    : undefined)
                }
                testId="performance-state"
              />
            ) : null}

            <TotalsSummary
              data={performanceData}
              state={overallState}
              period={search.period}
            />
            <StickyTotalsBar data={performanceData} state={overallState} />

            <AnalyticsSectionNav
              value={search.section}
              onChange={selectSection}
            />

            {search.section === "summary" ? (
              <div
                id="analytics-section-panel-summary"
                role="region"
                aria-labelledby="analytics-section-summary"
                className="rounded-[var(--radius-2)] border border-dashed border-[var(--color-hairline)] px-4 py-6 text-center text-[13px] leading-5 text-bg-8"
              >
                Общая сводка периода — выше. Переключитесь на «Динамику»,
                «Воронку» или «Результаты», чтобы увидеть график.
              </div>
            ) : null}

            {search.section === "dynamics" ? (
              <div
                id="analytics-section-panel-dynamics"
                role="region"
                aria-labelledby="analytics-section-dynamics"
                className="grid gap-4"
              >
                {search.period === "today" ? (
                  <section aria-labelledby="live-budget-title">
                    <h2
                      id="live-budget-title"
                      className="m-0 mb-3 font-display text-[18px] font-semibold text-bg-11"
                    >
                      Факт / база / stop
                    </h2>
                    {liveBudgetQ.isPending && !liveBudgetData ? (
                      <Skeleton className="h-80 w-full" />
                    ) : liveBudgetData ? (
                      <Suspense fallback={<ChartFallback className="h-80" />}>
                        <LiveBudgetChart
                          performance={performanceData}
                          series={liveBudgetData}
                          completeness={liveBudgetState}
                          timezone={displayTimeZone}
                          currency={commonCurrency(
                            performanceData.scope,
                            liveBudgetData.scope,
                          )}
                        />
                      </Suspense>
                    ) : (
                      <Card padding="sm" data-state="unavailable">
                        <AnalyticsStateNotice
                          state="unavailable"
                          issue={`${operatorProblemMessage(liveBudgetQ.error)}. Линии расхода и порогов скрыты.`}
                          testId="live-budget-state"
                        />
                      </Card>
                    )}
                  </section>
                ) : null}

                <section aria-labelledby="daypart-title">
                  <div className="mb-3">
                    <p className="m-0 font-display text-[12px] uppercase tracking-[0.08em] text-bg-8">
                      Выбранный день × 24 часа
                    </p>
                    <h2
                      id="daypart-title"
                      className="m-0 mt-1 font-display text-[18px] font-semibold text-bg-11"
                    >
                      Когда трафик конвертит
                    </h2>
                  </div>
                  <Card padding="sm">
                    {daypartQ.isPending && !daypartData ? (
                      <div
                        role="status"
                        aria-label="Загрузка почасовых данных"
                        className="grid gap-3 p-1"
                      >
                        <Skeleton className="h-11" />
                        <Skeleton className="h-52" />
                      </div>
                    ) : daypartQ.isError ? (
                      <div data-state="unavailable">
                        <AnalyticsStateNotice
                          state="unavailable"
                          issue={`${operatorProblemMessage(daypartQ.error)}. Почасовые значения скрыты.`}
                          testId="daypart-state"
                        />
                        <Button
                          type="button"
                          variant="secondary"
                          fullWidth
                          className="mt-3"
                          onClick={() => void daypartQ.refetch()}
                        >
                          Повторить
                        </Button>
                      </div>
                    ) : daypartData ? (
                      <Suspense fallback={<ChartFallback className="h-64" />}>
                        <DaypartDayChart data={daypartData} state={daypartState} />
                      </Suspense>
                    ) : (
                      <AnalyticsStateNotice
                        state="unavailable"
                        issue="Почасовой источник не подтвердил данные для выбранного окна."
                        testId="daypart-state"
                      />
                    )}
                  </Card>
                </section>
              </div>
            ) : null}

            {search.section === "funnel" ? (
              <section
                id="analytics-section-panel-funnel"
                aria-labelledby="funnel-title"
              >
                <h2
                  id="funnel-title"
                  className="m-0 mb-3 font-display text-[18px] font-semibold text-bg-11"
                >
                  Воронка
                </h2>
                <Suspense fallback={<ChartFallback />}>
                  <FunnelSummary
                    performance={performanceData}
                    completeness={overallState}
                    timezone={displayTimeZone}
                    currency={confirmedCurrency(performanceData.scope)}
                  />
                </Suspense>
              </section>
            ) : null}

            {search.section === "results" ? (
              <section
                id="analytics-section-panel-results"
                aria-labelledby="campaign-performance-title"
              >
                <div className="mb-3 flex items-end justify-between gap-3">
                  <h2
                    id="campaign-performance-title"
                    className="m-0 font-display text-[18px] font-semibold text-bg-11"
                  >
                    Кампании
                  </h2>
                  <span className="text-[12px] text-bg-8">
                    {overallState === "ready" || overallState === "empty"
                      ? `${performanceData.pagination.total} строк`
                      : "Количество не подтверждено"}
                  </span>
                </div>
                <AnalyticsPresetControl
                  value={search.preset}
                  onChange={(preset) => patchSearch({ preset, page: 1 })}
                />
                <Suspense fallback={<ChartFallback className="h-96" />}>
                  <PerformanceCards
                    rows={performanceData.rows}
                    parentState={overallState}
                    period={search.period}
                    preset={search.preset}
                    currency={confirmedCurrency(performanceData.scope)}
                    onFocusCampaign={focusCampaign}
                  />
                </Suspense>
                <Pagination
                  page={performanceData.pagination.page}
                  pages={performanceData.pagination.pages}
                  onPage={(page) => patchSearch({ page })}
                />
              </section>
            ) : null}

            <AnalyticsEvents
              items={eventsQ.data}
              state={eventsState}
              timezone={displayTimeZone}
              error={eventsQ.error}
              onRetry={() => void eventsQ.refetch()}
            />
          </>
        )}
      </section>
    </div>
  );
}

function AnalyticsFiltersPanel({
  search,
  options,
  onPeriod,
  onChange,
  onReset,
}: {
  search: AnalyticsRouteSearch;
  options?: AnalyticsPerformance["filter_options"];
  onPeriod: (period: AnalyticsPeriod) => void;
  onChange: (patch: Partial<AnalyticsRouteSearch>) => void;
  onReset: () => void;
}) {
  const structuredFilterCount = [
    search.account_id,
    search.offer_id,
    search.campaign_id,
  ].filter(Boolean).length;
  const activeFilterCount =
    structuredFilterCount +
    Number(Boolean(search.search)) +
    Number(search.sort !== "spend") +
    Number(search.direction !== "desc");
  return (
    <Card padding="sm" aria-label="Фильтры аналитики" className="grid gap-3">
      <div
        className="grid grid-cols-2 gap-2"
        role="group"
        aria-label="Период аналитики"
      >
        {ANALYTICS_PERIODS.map((item) => (
          <button
            key={item.id}
            type="button"
            aria-pressed={item.id === search.period}
            onClick={() => onPeriod(item.id)}
            className={cn(
              "min-h-11 rounded-[var(--radius-2)] border px-2 text-[13px] font-semibold",
              item.id === search.period
                ? "border-accent bg-accent text-bg-0"
                : "border-[var(--color-hairline-strong)] bg-bg-2 text-bg-9",
            )}
          >
            {item.label}
          </button>
        ))}
      </div>

      {search.period === "custom" ? (
        <div className="grid grid-cols-2 gap-2">
          <Input
            type="date"
            label="С даты"
            aria-label="Начало периода"
            value={search.from_date ?? ""}
            onChange={(event) =>
              onChange({ from_date: event.target.value || undefined })
            }
          />
          <Input
            type="date"
            label="По дату"
            aria-label="Конец периода"
            value={search.to_date ?? ""}
            onChange={(event) =>
              onChange({ to_date: event.target.value || undefined })
            }
          />
        </div>
      ) : null}

      <Input
        type="search"
        label="Быстрый поиск"
        aria-label="Поиск в аналитике"
        placeholder="Кампания, adset или ad ID"
        value={search.search ?? ""}
        onChange={(event) =>
          onChange({ search: event.target.value || undefined })
        }
      />

      <details className="rounded-[var(--radius-2)] border border-[var(--color-hairline)] bg-bg-1">
        <summary className="flex min-h-11 cursor-pointer items-center justify-between gap-3 px-3 text-[13px] font-semibold text-bg-11">
          <span>Фильтры и сортировка</span>
          <span className="font-display text-[12px] text-bg-8">
            {activeFilterCount
              ? `изменено ${activeFilterCount}`
              : "по умолчанию"}
          </span>
        </summary>
        <div className="grid gap-3 border-t border-[var(--color-hairline)] p-3">
          <Select
            label="Кабинет"
            aria-label="Кабинет"
            value={search.account_id ?? ""}
            options={[
              { value: "", label: "Все кабинеты" },
              ...(options?.accounts ?? []),
            ]}
            onChange={(event) =>
              onChange({
                account_id: event.target.value || undefined,
                offer_id: undefined,
                campaign_id: undefined,
              })
            }
          />
          <Select
            label="Оффер"
            aria-label="Оффер"
            value={search.offer_id ?? ""}
            options={[
              { value: "", label: "Все офферы" },
              ...(options?.offers ?? []),
            ]}
            onChange={(event) =>
              onChange({
                offer_id: event.target.value || undefined,
                campaign_id: undefined,
              })
            }
          />
          <Select
            label="Кампания"
            aria-label="Кампания"
            value={search.campaign_id ?? ""}
            options={[
              { value: "", label: "Все кампании" },
              ...(options?.campaigns ?? []),
            ]}
            onChange={(event) =>
              onChange({ campaign_id: event.target.value || undefined })
            }
          />
          <Select
            label="Сортировка"
            aria-label="Сортировка"
            value={search.sort}
            options={ANALYTICS_SORT_OPTIONS}
            onChange={(event) =>
              onChange({ sort: event.target.value as AnalyticsSort })
            }
          />
          <Select
            label="Порядок"
            aria-label="Порядок сортировки"
            value={search.direction}
            options={[
              { value: "desc", label: "По убыванию" },
              { value: "asc", label: "По возрастанию" },
            ]}
            onChange={(event) =>
              onChange({
                direction: event.target.value === "asc" ? "asc" : "desc",
              })
            }
          />
          <Button
            type="button"
            variant="ghost"
            fullWidth
            disabled={activeFilterCount === 0}
            onClick={onReset}
          >
            Сбросить фильтры
          </Button>
        </div>
      </details>
    </Card>
  );
}

function AnalyticsSectionNav({
  value,
  onChange,
}: {
  value: AnalyticsSection;
  onChange: (section: AnalyticsSection) => void;
}) {
  return (
    <div
      className="grid grid-cols-4 gap-2"
      role="group"
      aria-label="Раздел заливов"
    >
      {ANALYTICS_SECTIONS.map((item) => (
        <button
          key={item.value}
          type="button"
          id={`analytics-section-${item.value}`}
          aria-label={`Раздел: ${item.label}`}
          aria-pressed={value === item.value}
          aria-controls={`analytics-section-panel-${item.value}`}
          onClick={() => onChange(item.value)}
          className={cn(
            "min-h-11 rounded-[var(--radius-2)] border px-2 text-[13px] font-semibold",
            value === item.value
              ? "border-accent bg-accent text-bg-0"
              : "border-[var(--color-hairline-strong)] bg-bg-2 text-bg-9",
          )}
        >
          {item.label}
        </button>
      ))}
    </div>
  );
}

/**
 * Компактная закреплённая полоска «Итога окна». Полная карточка остаётся в
 * потоке страницы; эта — залипает под верхним safe-area отступом, пока
 * оператор листает «Динамику»/«Воронку»/«Результаты»/«События», и не
 * перекрывает нижний TabBar (он зафиксирован снизу, эта полоска — сверху).
 */
function StickyTotalsBar({
  data,
  state,
}: {
  data: AnalyticsPerformance;
  state: DataState;
}) {
  const visible = state !== "unavailable";
  const currency = confirmedCurrency(data.scope);
  return (
    <div
      role="status"
      aria-label="Итог окна, закреплено при прокрутке"
      data-testid="analytics-sticky-totals"
      data-state={state}
      className="sticky z-20 flex items-center justify-between gap-3 rounded-[var(--radius-2)] border border-[var(--color-hairline-strong)] bg-bg-1/95 px-3 py-2 backdrop-blur"
      style={{
        top: "var(--tg-content-safe-top, env(safe-area-inset-top, 0px))",
      }}
    >
      <StickyMetric
        label="Расход"
        value={visible ? formatSpend(data.totals.spend, currency) : "—"}
      />
      <StickyMetric
        label="Клики"
        value={visible ? formatInt(data.totals.clicks) : "—"}
      />
      <StickyMetric
        label="CPA"
        value={visible ? formatSpend(data.totals.cost_per_ftd, currency) : "—"}
      />
      <DataStateBadge state={state} compact />
    </div>
  );
}

function StickyMetric({ label, value }: { label: string; value: string }) {
  return (
    <div className="min-w-0">
      <div className="truncate text-[12px] uppercase tracking-[0.04em] text-bg-8">
        {label}
      </div>
      <div className="truncate font-display text-[13px] tabular-nums text-bg-11">
        {value}
      </div>
    </div>
  );
}

function AnalyticsPresetControl({
  value,
  onChange,
}: {
  value: AnalyticsPreset;
  onChange: (preset: AnalyticsPreset) => void;
}) {
  return (
    <div
      className="mb-3 grid grid-cols-3 gap-2"
      role="group"
      aria-label="Набор показателей аналитики"
    >
      {ANALYTICS_PRESETS.map((preset) => (
        <button
          key={preset.value}
          type="button"
          aria-pressed={value === preset.value}
          onClick={() => {
            haptic.selection();
            onChange(preset.value);
          }}
          className={cn(
            "min-h-11 rounded-[var(--radius-2)] border px-2 text-[13px] font-semibold",
            value === preset.value
              ? "border-accent bg-accent text-bg-0"
              : "border-[var(--color-hairline-strong)] bg-bg-2 text-bg-9",
          )}
        >
          {preset.label}
        </button>
      ))}
    </div>
  );
}

function SourceEvidence({
  data,
  state,
  timezone,
  timezoneKnown,
  refreshing,
  onRefresh,
}: {
  data: AnalyticsPerformance;
  state: DataState;
  timezone: string;
  timezoneKnown: boolean;
  refreshing: boolean;
  onRefresh: () => void;
}) {
  return (
    <Card
      title="Качество источников"
      eyebrow={`Снимок · ${formatFreshness(data.freshness_seconds)} назад`}
      titleRight={<DataStateBadge state={state} compact />}
      data-state={state}
    >
      <div className="grid gap-2">
        <SourceRow
          label="Meta"
          source={data.sources.meta}
          timezone={timezone}
          parentState={state}
        />
        <SourceRow
          label="AdSet.pro"
          source={data.sources.tracker}
          timezone={timezone}
          parentState={state}
        />
      </div>
      <div className="mt-3 grid gap-1 border-t border-[var(--color-hairline)] pt-3 text-[12px] leading-5 text-bg-9">
        <span>На: {formatZonedDateTime(data.as_of, timezone)}</span>
        <span>
          {state === "stale"
            ? `Сутки: снимок устарел · ${timezoneEvidenceLabel(
                data.scope.cabinet_timezone,
                data.scope.cabinet_timezone_state,
              )}`
            : state === "unavailable"
              ? "Сутки: не подтверждены"
              : state === "partial"
                ? timezoneKnown
                  ? `Сутки: неполный снимок · ${timezoneEvidenceLabel(
                      data.scope.cabinet_timezone,
                      data.scope.cabinet_timezone_state,
                    )}`
                  : "Сутки: оценка · часовой пояс кабинета неизвестен"
                : timezoneKnown
                  ? `Сутки: подтверждены · ${timezoneEvidenceLabel(
                      data.scope.cabinet_timezone,
                      data.scope.cabinet_timezone_state,
                    )}`
                  : "Сутки: оценка · часовой пояс кабинета неизвестен"}
        </span>
        <span>Валюта: {currencyEvidenceLabel(data.scope, state)}</span>
      </div>
      <Button
        type="button"
        variant="secondary"
        fullWidth
        loading={refreshing}
        className="mt-3"
        onClick={onRefresh}
      >
        Обновить данные
      </Button>
    </Card>
  );
}

function SourceRow({
  label,
  source,
  timezone,
  parentState,
}: {
  label: string;
  source: AnalyticsPerformance["sources"]["meta"];
  timezone: string;
  parentState: DataState;
}) {
  const effectiveStatus =
    parentState === "ready"
      ? source.status
      : parentState === "partial"
        ? "degraded"
        : "unknown";
  const tone =
    effectiveStatus === "good"
      ? "bg-success"
      : effectiveStatus === "degraded"
        ? "bg-warning"
        : effectiveStatus === "missing"
          ? "bg-danger"
          : "bg-bg-8";
  return (
    <div
      className="rounded-[var(--radius-2)] bg-bg-2 px-3 py-3"
      data-source-status={effectiveStatus}
    >
      <div className="flex items-center justify-between gap-3">
        <span className="inline-flex items-center gap-2 font-display text-[13px] text-bg-11">
          <span
            className={cn("size-2 rounded-full", tone)}
            aria-hidden="true"
          />
          {label}
        </span>
        <span className="text-[12px] text-bg-9">
          {sourceStatusLabel(effectiveStatus)}
        </span>
      </div>
      <div className="mt-1 text-[12px] leading-5 text-bg-8">
        {source.last_event_at
          ? `Последнее событие: ${formatZonedDateTime(source.last_event_at, timezone)}`
          : source.note || "Время последнего события не подтверждено"}
        {source.lag_seconds != null
          ? ` · лаг ${formatFreshness(source.lag_seconds)}`
          : ""}
      </div>
    </div>
  );
}

function TotalsSummary({
  data,
  state,
  period,
}: {
  data: AnalyticsPerformance;
  state: DataState;
  period: AnalyticsPeriod;
}) {
  const visible = state !== "unavailable";
  const confirmedTone = state === "ready";
  const budget = data.total_live_budget;
  const currency = confirmedCurrency(data.scope);
  return (
    <Card
      title="Итог окна"
      eyebrow={
        period === "today"
          ? `Снимок · ${formatFreshness(data.freshness_seconds)} назад`
          : confirmedTone
            ? "Подтверждённый период"
            : `Период · ${formatFreshness(data.freshness_seconds)} назад`
      }
      titleRight={<DataStateBadge state={state} compact />}
      data-state={state}
      padding="sm"
    >
      <dl className="grid grid-cols-3 gap-1">
        <SummaryMetric
          label="Расход"
          value={visible ? formatSpend(data.totals.spend, currency) : "—"}
        />
        <SummaryMetric
          label="Клики"
          value={visible ? formatInt(data.totals.clicks) : "—"}
        />
        <SummaryMetric
          label="Рег."
          value={visible ? formatInt(data.totals.registrations) : "—"}
          accent={confirmedTone}
        />
        <SummaryMetric
          label="FTD"
          value={visible ? formatInt(data.totals.ftds) : "—"}
          accent={confirmedTone}
        />
        <SummaryMetric
          label="Депозиты"
          value={visible ? formatInt(data.totals.confirmed_deposits) : "—"}
        />
        <SummaryMetric
          label={period === "today" ? "Δ stop" : "Выручка"}
          value={
            !visible
              ? "—"
              : period === "today"
                ? signedSpend(budget?.stop_delta, currency)
                : formatSpend(data.totals.revenue, currency)
          }
          danger={
            confirmedTone &&
            period === "today" &&
            Number(budget?.stop_delta ?? 0) > 0
          }
        />
      </dl>
      {period === "today" && !budget ? (
        <p className="m-0 mt-2 rounded-[var(--radius-2)] bg-bg-2 px-3 py-2 text-[12px] leading-5 text-bg-9">
          Base / stop:{" "}
          {data.total_budget_unavailable_reason || "нет подтверждённого порога"}
          .
        </p>
      ) : null}
    </Card>
  );
}

function SummaryMetric({
  label,
  value,
  accent = false,
  danger = false,
}: {
  label: string;
  value: string;
  accent?: boolean;
  danger?: boolean;
}) {
  return (
    <div className="min-w-0 rounded-[var(--radius-2)] bg-bg-2 px-2 py-3">
      <dt className="truncate text-[12px] uppercase tracking-[0.04em] text-bg-8">
        {label}
      </dt>
      <dd
        className={cn(
          "m-0 mt-1 truncate font-display text-[16px] tabular-nums",
          danger ? "text-danger" : accent ? "text-active" : "text-bg-11",
        )}
      >
        {value}
      </dd>
    </div>
  );
}

type AnalyticsEvent = components["schemas"]["OperatorEventItem"];

function AnalyticsEvents({
  items,
  state,
  timezone,
  error,
  onRetry,
}: {
  items?: AnalyticsEvent[];
  state: DataState;
  timezone: string;
  error: unknown;
  onRetry: () => void;
}) {
  return (
    <section aria-labelledby="analytics-events-title">
      <div className="mb-3 flex items-center justify-between gap-3">
        <div>
          <p className="m-0 font-display text-[12px] uppercase tracking-[0.08em] text-bg-8">
            Алерты и завершённые действия
          </p>
          <h2
            id="analytics-events-title"
            className="m-0 mt-1 font-display text-[18px] font-semibold text-bg-11"
          >
            События
          </h2>
        </div>
        <DataStateBadge state={state} compact />
      </div>
      <Card padding="sm" data-state={state}>
        {state === "unavailable" ? (
          <ErrorState
            message={`${operatorProblemMessage(error)}. Лента событий скрыта.`}
            onRetry={onRetry}
          />
        ) : state === "empty" ? (
          <p className="m-0 p-3 text-center text-[14px] text-bg-9">
            За выбранный период событий нет.
          </p>
        ) : (
          <>
            {state === "stale" ? (
              <AnalyticsStateNotice
                state="stale"
                issue="Лента сверяется с журналом; показанные события считаются устаревшими."
                testId="events-state"
              />
            ) : null}
            <ol className="divide-y divide-[var(--color-hairline)]">
              {(items ?? []).slice(0, 8).map((item, index) => (
                <li
                  key={`${item.event_type}-${item.ts}-${item.fb_ad_id ?? index}`}
                  className="py-3"
                >
                  <div className="flex items-start justify-between gap-3">
                    <div className="min-w-0">
                      <strong className="block text-[14px] leading-5 text-bg-11">
                        {eventTitle(item)}
                      </strong>
                      <span className="mt-1 block text-[12px] leading-5 text-bg-8">
                        {formatZonedDateTime(item.ts, timezone)}
                        {item.campaign_name ? ` · ${item.campaign_name}` : ""}
                      </span>
                    </div>
                    {item.fb_ad_id ? (
                      <Link
                        to="/ads/$fbAdId"
                        params={{ fbAdId: item.fb_ad_id }}
                        className="inline-flex min-h-11 shrink-0 items-center rounded-[var(--radius-2)] px-2 text-[13px] font-semibold text-active"
                      >
                        Открыть
                      </Link>
                    ) : null}
                  </div>
                </li>
              ))}
            </ol>
          </>
        )}
      </Card>
    </section>
  );
}

function eventTitle(item: AnalyticsEvent): string {
  const target = item.ad_name ?? item.fb_ad_id ?? "Система";
  if (item.event_type === "alert") {
    const label =
      item.stage === "stop"
        ? "критический stop"
        : item.stage === "warning"
          ? "предупреждение"
          : "сигнал";
    return `${target}: ${label}`;
  }
  const status = item.task_status?.toLowerCase();
  const result =
    status === "succeeded"
      ? "выполнено"
      : status === "failed"
        ? "ошибка"
        : status === "cancelled"
          ? "отменено"
          : "результат не подтверждён";
  return `${target}: ${item.task_type ?? "действие"} · ${result}`;
}

function Pagination({
  page,
  pages,
  onPage,
}: {
  page: number;
  pages: number;
  onPage: (page: number) => void;
}) {
  if (pages <= 1) return null;
  return (
    <nav
      aria-label="Страницы кампаний"
      className="mt-3 grid grid-cols-[1fr_auto_1fr] items-center gap-2"
    >
      <Button
        type="button"
        variant="secondary"
        disabled={page <= 1}
        onClick={() => onPage(page - 1)}
      >
        Назад
      </Button>
      <span className="px-2 font-display text-[12px] tabular-nums text-bg-9">
        {page} / {pages}
      </span>
      <Button
        type="button"
        variant="secondary"
        disabled={page >= pages}
        onClick={() => onPage(page + 1)}
      >
        Далее
      </Button>
    </nav>
  );
}

function AnalyticsSkeleton() {
  return (
    <div role="status" aria-label="Загрузка аналитики" className="grid gap-4">
      <Skeleton className="h-56 w-full" />
      <Skeleton className="h-36 w-full" />
      <Skeleton className="h-72 w-full" />
    </div>
  );
}

function signedSpend(
  value: string | null | undefined,
  currency: string | null,
): string {
  if (value == null) return "—";
  const numeric = Number(value);
  if (!Number.isFinite(numeric)) return "—";
  const formatted = formatSpend(value, currency);
  return formatted === "—"
    ? formatted
    : `${numeric > 0 ? "+" : ""}${formatted}`;
}

type CurrencyScope = AnalyticsPerformance["scope"];

function confirmedCurrency(scope: CurrencyScope): string | null {
  return scope.currency_state === "single" && scope.currency === "USD"
    ? "USD"
    : null;
}

function currencyEvidenceLabel(scope: CurrencyScope, state: DataState): string {
  if (scope.currency_state === "single" && scope.currency === "USD") {
    // Голый «$» не сообщал ничего: подтверждённую валюту называем кодом.
    const label = "USD";
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
      ? "несколько валют · снимок устарел · денежные итоги скрыты"
      : "несколько валют · денежные итоги скрыты";
  }
  return "не подтверждена · денежные итоги скрыты";
}

function commonCurrency(...scopes: CurrencyScope[]): string | null {
  const currencies = scopes.map(confirmedCurrency);
  const first = currencies[0] ?? null;
  return first !== null && currencies.every((currency) => currency === first)
    ? first
    : null;
}

function defaultCalendarDates(
  fromDate?: string,
  toDate?: string,
): { from_date: string; to_date: string } {
  if (validCalendarDate(fromDate) && validCalendarDate(toDate)) {
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

function validCalendarDate(value?: string): boolean {
  if (!value || !/^\d{4}-\d{2}-\d{2}$/.test(value)) return false;
  const [year, month, day] = value.split("-").map(Number);
  return (
    new Date(Date.UTC(year!, month! - 1, day!)).toISOString().slice(0, 10) ===
    value
  );
}

function localCalendarDate(value: Date): string {
  const year = value.getFullYear();
  const month = String(value.getMonth() + 1).padStart(2, "0");
  const day = String(value.getDate()).padStart(2, "0");
  return `${year}-${month}-${day}`;
}
