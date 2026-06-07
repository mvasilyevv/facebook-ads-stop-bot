/**
 * History-страница.
 * Структура: PeriodSelector → summary (слева) + timeline (справа) → drill-down drawer.
 * Default period: 30 дней. Max: 90 дней (иначе бэк 422).
 */

import { useState, useCallback } from "react";
import { createFileRoute } from "@tanstack/react-router";
import { ExternalLink } from "lucide-react";
import { PageHeader } from "@/components/layout/PageHeader";
import { PeriodSelector, type Period } from "@/components/history/PeriodSelector";
import { HistorySummarySection } from "@/components/history/HistorySummarySection";
import { HistoryTimeline } from "@/components/history/HistoryTimeline";
import { HistoryEventsDrawer } from "@/components/history/HistoryEventsDrawer";
import { useHistorySummary, useHistoryTimeline } from "@/lib/api/history";
import { Button } from "@/components/ui/Button";
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
  const [period, setPeriod] = useState<Period>(buildDefault30d);
  const [drawerOpen, setDrawerOpen] = useState(false);
  const [drawerItem, setDrawerItem] = useState<HistoryTimelineItem | null>(null);

  // Запросы
  const summaryQ = useHistorySummary(period);
  const timelineQ = useHistoryTimeline(period);

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
      <PageHeader
        eyebrowNum="03"
        eyebrow="HISTORY · АРХИВ"
        title="История"
        displayNumber="03"
        subtitle={`${period.from_iso.slice(0, 10)} — ${period.to_iso.slice(0, 10)}`}
        action={
          <Button
            variant="secondary"
            size="sm"
            leftIcon={<ExternalLink size={14} />}
          >
            Export CSV
          </Button>
        }
      />

      {/* Toolbar: period selector + фильтры */}
      <div className="flex items-center gap-2 mb-6 flex-wrap">
        <PeriodSelector value={period} onChange={setPeriod} />
      </div>

      {/* Основная сетка 40% / 60% */}
      <div className="grid gap-6" style={{ gridTemplateColumns: "40% 60%" }}>
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
