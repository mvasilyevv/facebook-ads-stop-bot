/**
 * History-страница.
 * Структура: PeriodSelector → summary (слева) + timeline (справа) → drill-down drawer.
 * Default period: 30 дней. Max: 90 дней (иначе бэк 422).
 */

import { useState, useCallback } from "react";
import { createFileRoute } from "@tanstack/react-router";
import { PageHeader } from "@/components/layout/PageHeader";
import { PeriodSelector, type Period } from "@/components/history/PeriodSelector";
import { HistorySummarySection } from "@/components/history/HistorySummarySection";
import { HistoryTimeline } from "@/components/history/HistoryTimeline";
import { HistoryEventsDrawer } from "@/components/history/HistoryEventsDrawer";
import { MetaDelayedNote, TrackerLiveStrip } from "@/components/data/SourceStatus";
import { useHistorySummary, useHistoryTimeline } from "@/lib/api/history";
import { useStatsPeriod } from "@/lib/api/stats";
import { useRealtimeInvalidation } from "@/lib/websocket/useRealtimeInvalidation";
import type { HistoryTimelineItem } from "@fb/shared";

export const Route = createFileRoute("/history/")({
  component: HistoryPage,
});

/** Начальный период — последние 30 дней. */
function buildDefault30d(): Period {
  const to = new Date();
  const from = new Date(to.getTime() - 30 * 86400 * 1000);
  return {
    from_iso: from.toISOString(),
    to_iso: to.toISOString(),
  };
}

function HistoryPage() {
  useRealtimeInvalidation();
  const [period, setPeriod] = useState<Period>(buildDefault30d);
  const [drawerOpen, setDrawerOpen] = useState(false);
  const [drawerItem, setDrawerItem] = useState<HistoryTimelineItem | null>(null);

  // Запросы
  const summaryQ = useHistorySummary(period);
  const timelineQ = useHistoryTimeline(period);
  const trackerQ = useStatsPeriod(period);

  const handleAlertClick = useCallback((item: HistoryTimelineItem) => {
    setDrawerItem(item);
    setDrawerOpen(true);
  }, []);

  const handleDrawerClose = useCallback((open: boolean) => {
    setDrawerOpen(open);
    if (!open) setDrawerItem(null);
  }, []);

  return (
    <>
      {/* Export CSV удалён (аудит 2026-06-09): кнопка была нефункциональной заглушкой.
          Вернуть вместе с реальным API-эндпоинтом экспорта, если понадобится. */}
      <PageHeader
        eyebrowNum="03"
        eyebrow="HISTORY · АРХИВ"
        title="История"
        subtitle={`${formatHistoryDate(period.from_iso)} — ${formatHistoryDate(period.to_iso)}`}
      />

      {/* Toolbar: period selector + фильтры */}
      <div className="flex items-center gap-2 mb-6 flex-wrap">
        <PeriodSelector value={period} onChange={setPeriod} />
        <MetaDelayedNote className="ml-auto" />
      </div>

      <TrackerLiveStrip data={trackerQ.data} className="mb-6" />

      {/* Основная сетка 40% / 60% */}
      <div className="grid grid-cols-1 gap-6 xl:grid-cols-[minmax(280px,0.7fr)_minmax(0,1.3fr)]">
        {/* Левая колонка: сводка */}
        <div>
          <HistorySummarySection
            data={summaryQ.data}
            isLoading={summaryQ.isLoading}
            error={summaryQ.error}
            onRetry={() => void summaryQ.refetch()}
          />
        </div>

        {/* Правая колонка: таймлайн */}
        <div>
          <HistoryTimeline
            items={timelineQ.data}
            isLoading={timelineQ.isLoading}
            error={timelineQ.error}
            onRetry={() => void timelineQ.refetch()}
            onAlertClick={handleAlertClick}
          />
        </div>
      </div>

      {/* Drill-down drawer */}
      <HistoryEventsDrawer
        open={drawerOpen}
        onOpenChange={handleDrawerClose}
        initialItem={drawerItem}
        period={period}
      />
    </>
  );
}

function formatHistoryDate(value: string): string {
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) return value.slice(0, 10);
  return date.toLocaleDateString("ru-RU", {
    day: "2-digit",
    month: "short",
    year: "numeric",
  });
}
