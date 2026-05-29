/**
 * History (`/history`) — страница истории событий.
 *
 * Блоки (по docs/frontend_v2_design.md §4.4):
 *   1. PageHeader — eyebrow 04/HISTORY, title "History.".
 *   2. PeriodSelector — пресеты 7/30/90 дней + custom from/to (≤90д).
 *   3. HistorySummarySection — KPI-strip + breakdown по stage + by_rule.
 *   4. Tabs: Timeline | Events
 *      - Timeline — лента alert+task событий по дням DESC.
 *      - Events — drill-down таблица AlertEvent с фильтрами.
 *
 * Источники данных:
 *   - useHistorySummary({ from_iso, to_iso }) — агрегаты за период.
 *   - useHistoryTimeline({ from_iso, to_iso, limit: 200 }) — лента.
 *   - useHistoryEvents({ from_iso, to_iso, stage? }) — drill-down события.
 *
 * Обработка лимита 90 дней:
 *   - PeriodSelector валидирует ≤90 дней до onChange.
 *   - API 422 → ErrorState с сообщением "Максимальный период — 90 дней".
 */

import { useState, type ReactNode } from "react";
import { createFileRoute } from "@tanstack/react-router";

import { PageHeader, HeaderSep } from "@/components/layout/PageHeader";
import { Tabs, TabsList, TabsContent } from "@/components/ui/Tabs";

import { useHistorySummary, useHistoryTimeline, useHistoryEvents } from "@/lib/api/history";
import { formatInt } from "@/lib/utils/format";

import {
  PeriodSelector,
  type DateRange,
} from "@/components/history/PeriodSelector";
import { HistorySummarySection } from "@/components/history/HistorySummarySection";
import { HistoryTimeline } from "@/components/history/HistoryTimeline";
import { HistoryEventsTable } from "@/components/history/HistoryEventsTable";

export const Route = createFileRoute("/history/")({
  component: HistoryPage,
});

/** Вычисляет ISO-дату начала N дней назад. */
function daysAgoIso(days: number): string {
  const d = new Date();
  d.setUTCDate(d.getUTCDate() - days);
  d.setUTCHours(0, 0, 0, 0);
  return d.toISOString().slice(0, 10);
}

function HistoryPage() {
  // Состояние периода (default: последние 30 дней)
  const [range, setRange] = useState<DateRange>({
    from_iso: daysAgoIso(30),
    to_iso: new Date().toISOString().slice(0, 10),
  });

  // Активный таб (timeline | events)
  const [activeTab, setActiveTab] = useState<string>("timeline");

  // Фильтр stage для Events-таба
  const [stageFilter, setStageFilter] = useState<string | null>(null);

  // Запросы
  const summaryQuery = useHistorySummary({ from_iso: range.from_iso, to_iso: range.to_iso });
  const timelineQuery = useHistoryTimeline({
    from_iso: range.from_iso,
    to_iso: range.to_iso,
    limit: 200,
  });
  const eventsQuery = useHistoryEvents({
    from_iso: range.from_iso,
    to_iso: range.to_iso,
    ...(stageFilter ? { stage: stageFilter } : {}),
  });

  // Подсчёт событий для subtitle
  const totalEvents =
    (summaryQuery.data?.alerts.warning_count ?? 0) +
    (summaryQuery.data?.alerts.stop_count ?? 0);

  // Проверка на 422 (лимит 90 дней)
  const is422Error = (err: unknown): boolean => {
    if (err instanceof Error && err.message.includes("422")) return true;
    if (err && typeof err === "object" && "status" in err && (err as { status: number }).status === 422) return true;
    return false;
  };

  return (
    <>
      {/* 1. PageHeader */}
      <PageHeader
        eyebrowNum="04"
        eyebrow="ИСТОРИЯ"
        title="История."
        displayNumber="04"
        subtitle={
          <HeaderSubtitle
            from_iso={range.from_iso}
            to_iso={range.to_iso}
            totalEvents={totalEvents}
            isLoading={summaryQuery.isLoading}
          />
        }
      />

      {/* 2. Период-селектор */}
      <div className="mb-8">
        <PeriodSelector value={range} onChange={setRange} />
      </div>

      {/* 3. Summary секция */}
      <HistorySummarySection
        summary={summaryQuery.data}
        isLoading={summaryQuery.isLoading}
        isError={summaryQuery.isError}
        error={
          is422Error(summaryQuery.error)
            ? new Error("Максимальный период — 90 дней. Пожалуйста, сократите диапазон дат.")
            : summaryQuery.error
        }
        onRetry={() => summaryQuery.refetch()}
      />

      {/* 4. Timeline + Events Tabs */}
      <SectionTitle eyebrowNum="05" eyebrow="ЛЕНТА" title="События" />

      <Tabs value={activeTab} onValueChange={setActiveTab} variant="underline" className="mb-6">
        <TabsList
          variant="underline"
          items={[
            {
              value: "timeline",
              label: "Лента",
              count: timelineQuery.data?.length ?? undefined,
            },
            {
              value: "events",
              label: "Алерты",
              count: eventsQuery.data?.length ?? undefined,
            },
          ]}
        />

        <TabsContent value="timeline" className="mt-6">
          <HistoryTimeline
            events={timelineQuery.data}
            isLoading={timelineQuery.isLoading}
            isError={timelineQuery.isError}
            error={
              is422Error(timelineQuery.error)
                ? new Error("Максимальный период — 90 дней.")
                : timelineQuery.error
            }
            onRetry={() => timelineQuery.refetch()}
          />
        </TabsContent>

        <TabsContent value="events" className="mt-6">
          <HistoryEventsTable
            events={eventsQuery.data}
            isLoading={eventsQuery.isLoading}
            isError={eventsQuery.isError}
            error={
              is422Error(eventsQuery.error)
                ? new Error("Максимальный период — 90 дней.")
                : eventsQuery.error
            }
            onRetry={() => eventsQuery.refetch()}
            onStageFilter={setStageFilter}
          />
        </TabsContent>
      </Tabs>
    </>
  );
}

/** Subtitle header: диапазон + кол-во событий. */
function HeaderSubtitle({
  from_iso,
  to_iso,
  totalEvents,
  isLoading,
}: {
  from_iso: string;
  to_iso: string;
  totalEvents: number;
  isLoading: boolean;
}) {
  return (
    <>
      <span>
        {from_iso} — {to_iso}
      </span>
      <HeaderSep />
      <span>{isLoading ? "Загрузка..." : `${formatInt(totalEvents)} событий`}</span>
    </>
  );
}

/**
 * SectionTitle — заголовок секции (eyebrow + title).
 * Аналог компонента в index.tsx.
 */
function SectionTitle({
  eyebrowNum,
  eyebrow,
  title,
  action,
}: {
  eyebrowNum: string;
  eyebrow: string;
  title: string;
  action?: ReactNode;
}) {
  return (
    <div className="flex items-baseline justify-between mb-5">
      <h2 className="section-title">
        <span className="font-display text-[10px] uppercase tracking-[0.14em] text-bg-8 mr-3.5">
          <span className="text-bg-7 mr-1.5">{eyebrowNum}</span>
          {eyebrow}
        </span>
        {title}
      </h2>
      {action ? <div>{action}</div> : null}
    </div>
  );
}
