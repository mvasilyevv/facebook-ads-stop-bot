/**
 * Dashboard (`/`) — overview-страница оператора.
 *
 * Блоки (по docs/frontend_v2_mockups/dashboard.html):
 *   1. PageHeader — observer_status + last_scan + WS-статус + "Scan now".
 *   2. KPI strip — 4 карточки (ads / warning / stop / incidents).
 *   3. Spend chart (Recharts area) + Active incidents — grid 1.6fr / 1fr.
 *   4. Recent events — лента alert_events.
 *   5. Task queues — Disable + Enable (grid 1/1).
 *
 * Источники данных:
 *   - useDashboardStats() — KPI + header (refetch 30s).
 *   - useDashboardBatch() — incidents + alerts + disable tasks (refetch 60s,
 *     partial-failure: упавшая секция приходит пустым массивом, страница не падает).
 *   - useChartData({ hours, bucket }) — бакеты графика (range-driven).
 *   - useEnableTasks() — enable-очередь (batch отдаёт только disable).
 *   - useTriggerScanNow() — кнопка "Scan now" + Toast.
 *   - useDashboardSocket() — WS-статус для header'а.
 */

import { useState, type ReactNode } from "react";
import { createFileRoute, useNavigate } from "@tanstack/react-router";
import { RefreshCcw, ChevronRight } from "lucide-react";

import { PageHeader, HeaderSep } from "@/components/layout/PageHeader";
import { Button } from "@/components/ui/Button";
import { toast } from "@/components/ui/Toast";
import { useDashboardSocket } from "@/lib/websocket/useDashboardSocket";
import {
  useDashboardStats,
  useDashboardBatch,
  useChartData,
  useEnableTasks,
  useTriggerScanNow,
} from "@/lib/api/dashboard";
import { formatRelativeTime } from "@/lib/utils/format";

import { KpiSection } from "@/components/dashboard/KpiSection";
import { SpendChartCard, type RangeKey } from "@/components/dashboard/SpendChartCard";
import { IncidentsCard } from "@/components/dashboard/IncidentsCard";
import { RecentEventsCard } from "@/components/dashboard/RecentEventsCard";
import { TaskQueueCard } from "@/components/dashboard/TaskQueueCard";

export const Route = createFileRoute("/")({
  component: DashboardPage,
});

const OBSERVER_LABEL: Record<string, string> = {
  running: "Observer онлайн",
  paused: "Observer на паузе",
  unknown: "Observer недоступен",
};

function DashboardPage() {
  const navigate = useNavigate();
  const [range, setRange] = useState<RangeKey>("today");

  const socket = useDashboardSocket();
  const statsQuery = useDashboardStats();
  const batchQuery = useDashboardBatch();
  const enableQuery = useEnableTasks({ limit: 10 });
  const scanNow = useTriggerScanNow();

  const chartCfg = range === "today" ? { hours: 24, bucket: "hour" as const } : { hours: 168, bucket: "day" as const };
  const chartQuery = useChartData(chartCfg);

  const stats = statsQuery.data;
  const batch = batchQuery.data;

  // Навигация к ad'у по клику в incidents / events.
  const goToAd = (fbAdId: string) => {
    navigate({ to: "/ads/$fbAdId", params: { fbAdId } });
  };

  const handleScanNow = () => {
    scanNow.mutate(undefined, {
      onSuccess: () => toast.success("Сканирование запущено", "Observer запустит цикл сканирования."),
      onError: (err) =>
        toast.error("Не удалось запустить scan", err instanceof Error ? err.message : String(err)),
    });
  };

  return (
    <>
      <PageHeader
        eyebrowNum="01"
        eyebrow="ОБЗОР · НАБЛЮДЕНИЕ · УПРАВЛЕНИЕ"
        title="Панель."
        displayNumber="01"
        subtitle={<HeaderSubtitle stats={stats} socketStatus={socket.status} pollingFallback={socket.pollingFallback} />}
        action={
          <Button
            variant="primary"
            leftIcon={<RefreshCcw size={14} aria-hidden="true" />}
            loading={scanNow.isPending}
            onClick={handleScanNow}
          >
            Сканировать
          </Button>
        }
      />

      {/* 2. KPI strip */}
      <KpiSection
        stats={stats}
        isLoading={statsQuery.isLoading}
        isError={statsQuery.isError}
        error={statsQuery.error}
        onRetry={() => statsQuery.refetch()}
      />

      {/* 3. Chart (1.6fr) + Incidents (1fr) */}
      <div className="grid grid-cols-[1.6fr_1fr] gap-6 mb-10">
        <SpendChartCard
          range={range}
          onRangeChange={setRange}
          buckets={chartQuery.data}
          isLoading={chartQuery.isLoading}
          isError={chartQuery.isError}
          error={chartQuery.error}
          onRetry={() => chartQuery.refetch()}
        />
        <IncidentsCard
          incidents={batch?.recent_incidents ?? []}
          isLoading={batchQuery.isLoading}
          isError={batchQuery.isError}
          error={batchQuery.error}
          onRetry={() => batchQuery.refetch()}
          onSelect={goToAd}
        />
      </div>

      {/* 4. Recent events */}
      <SectionTitle
        eyebrowNum="04"
        eyebrow="Поток"
        title="Последние события"
        action={
          <Button
            variant="ghost"
            size="sm"
            rightIcon={<ChevronRight size={14} aria-hidden="true" />}
            onClick={() => navigate({ to: "/history" })}
          >
            Все
          </Button>
        }
      />
      <RecentEventsCard
        events={batch?.recent_alerts ?? []}
        isLoading={batchQuery.isLoading}
        isError={batchQuery.isError}
        error={batchQuery.error}
        onRetry={() => batchQuery.refetch()}
        onSelect={goToAd}
      />

      <hr className="border-0 h-px bg-bg-5 my-10" />

      {/* 5. Task queues */}
      <SectionTitle eyebrowNum="05" eyebrow="Outbox" title="Очереди задач" />
      <div className="grid grid-cols-2 gap-6">
        <TaskQueueCard
          title="Очередь отключений"
          tasks={batch?.recent_disable_tasks ?? []}
          isLoading={batchQuery.isLoading}
          isError={batchQuery.isError}
          error={batchQuery.error}
          onRetry={() => batchQuery.refetch()}
        />
        <TaskQueueCard
          title="Очередь включений"
          tasks={enableQuery.data ?? []}
          isLoading={enableQuery.isLoading}
          isError={enableQuery.isError}
          error={enableQuery.error}
          onRetry={() => enableQuery.refetch()}
        />
      </div>
    </>
  );
}

/** Подзаголовок header'а: статус observer + last scan + WS-индикатор. */
function HeaderSubtitle({
  stats,
  socketStatus,
  pollingFallback,
}: {
  stats: ReturnType<typeof useDashboardStats>["data"];
  socketStatus: string;
  pollingFallback: boolean;
}) {
  const observerStatus = stats?.observer_status ?? "unknown";
  const isOnline = observerStatus === "running";

  return (
    <>
      <span>
        <span
          aria-hidden="true"
          className={cnDot(isOnline)}
        />
        {OBSERVER_LABEL[observerStatus] ?? OBSERVER_LABEL.unknown}
      </span>
      <HeaderSep />
      <span>Посл. скан {stats ? formatRelativeTime(stats.last_scan_at) : "—"}</span>
      <HeaderSep />
      <span>WS: {socketStatus}</span>
      {pollingFallback ? (
        <>
          <HeaderSep />
          <span className="text-warning">polling fallback</span>
        </>
      ) : null}
    </>
  );
}

/** Точка-индикатор статуса observer (живой пульс только когда online). */
function cnDot(isOnline: boolean): string {
  return [
    "inline-block size-1.5 rounded-full mr-1.5 align-middle",
    isOnline ? "bg-success pulse-dot" : "bg-bg-7",
  ].join(" ");
}

/**
 * SectionTitle — заголовок секции (eyebrow inline + title + optional action).
 * Повторяет .section-title-row из мока.
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
