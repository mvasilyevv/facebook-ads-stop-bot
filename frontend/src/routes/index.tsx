/**
 * Dashboard — главная страница обзора кабинета.
 *
 * Структура (по макету dashboard.html):
 *   PageHeader: eyebrow / title / subtitle (live observer status) / action "Scan now"
 *   KpiStrip: 4 карточки Active/Warning/Stop/Disabled
 *   Grid 2 колонки: SpendChart + ActiveIncidents
 *   RecentEvents: лента алертов
 *   2× TaskQueueCard: disable-queue + enable-queue
 *
 * Данные: useDashboardBatch (главный агрегат), useDisableTasks, useEnableTasks.
 * Live-обновления: useRealtimeInvalidation (WS invalidation).
 */

import { createFileRoute, useRouter } from "@tanstack/react-router";
import { RefreshCw } from "lucide-react";
import { useMemo } from "react";

import { PageHeader, HeaderSep, LiveDot } from "@/components/layout/PageHeader";
import { Button } from "@/components/ui/Button";
import { ErrorState } from "@/components/ui/ErrorState";

import { KpiStrip, KpiStripSkeleton } from "@/components/dashboard/KpiStrip";
import { SpendChart } from "@/components/dashboard/SpendChart";
import { ActiveIncidents } from "@/components/dashboard/ActiveIncidents";
import { RecentEvents } from "@/components/dashboard/RecentEvents";
import { TaskQueueCard } from "@/components/domain/feed/TaskQueueCard";

import { useDashboardBatch } from "@/lib/api/dashboard";
import { useDisableTasks, useEnableTasks } from "@/lib/api/ads";
import { useRealtimeInvalidation } from "@/lib/websocket/useRealtimeInvalidation";
import { apiSend } from "@/lib/api/client";

import type { Incident, AlertEvent, TaskQueueRow } from "@fb/shared";
import { formatRelativeTime } from "@fb/shared";

// ─── Route ────────────────────────────────────────────────────────────────────

export const Route = createFileRoute("/")({
  component: DashboardPage,
});

// ─── Компонент ────────────────────────────────────────────────────────────────

function DashboardPage() {
  const router = useRouter();

  // WS-invalidation — живые обновления после сканов
  const { status: wsStatus } = useRealtimeInvalidation();

  // Главный агрегат
  const {
    data: batch,
    isLoading,
    isError,
    error,
    refetch,
  } = useDashboardBatch();

  // Очереди задач (отдельные запросы для актуальности)
  const disableTasksQ = useDisableTasks({ status: "PENDING,RUNNING,RETRYING", limit: 20 });
  const enableTasksQ = useEnableTasks({ status: "PENDING,RUNNING,RETRYING", limit: 20 });

  // Нормализуем incidents/alerts из batch (API отдаёт unknown[])
  const incidents = useMemo<Incident[]>(
    () => (batch?.recent_incidents as Incident[] | undefined) ?? [],
    [batch],
  );
  const events = useMemo<AlertEvent[]>(
    () => (batch?.recent_alerts as AlertEvent[] | undefined) ?? [],
    [batch],
  );

  // Scan now — публикует redis-триггер через API
  async function handleScanNow() {
    try {
      await apiSend("POST", "/settings/observer/scan-now");
    } catch {
      // Игнорируем ошибку — observer сам среагирует
    }
  }

  // Subtitle — live observer status
  const stats = batch?.stats;
  const observerRunning = stats?.observer_status === "running";
  const subtitle = (
    <>
      {observerRunning ? <LiveDot /> : null}
      <span>{observerRunning ? "Observer online" : "Observer offline"}</span>
      {stats?.last_scan_at && (
        <>
          <HeaderSep />
          <span>Скан {formatRelativeTime(stats.last_scan_at)} назад</span>
        </>
      )}
      {stats?.scans_today != null && (
        <>
          <HeaderSep />
          <span>{stats.scans_today} сканов сегодня</span>
        </>
      )}
      {wsStatus === "connected" ? (
        <>
          <HeaderSep />
          <span className="text-success">live</span>
        </>
      ) : wsStatus === "polling" ? (
        <>
          <HeaderSep />
          <span className="text-warning">polling</span>
        </>
      ) : null}
    </>
  );

  // Фатальная ошибка загрузки batch
  if (isError && !batch) {
    return (
      <div className="px-8 py-8">
        <PageHeader
          eyebrowNum="01"
          eyebrow="OVERVIEW · OBSERVE · OPERATE"
          title="Dashboard"
          subtitle={subtitle}
        />
        <ErrorState
          title="Не удалось загрузить данные Dashboard."
          error={error}
          onRetry={() => void refetch()}
        />
      </div>
    );
  }

  return (
    <div className="px-8 py-8 pb-16" aria-label="Dashboard">
      {/* ── PageHeader ──────────────────────────────────────────────────────── */}
      <PageHeader
        eyebrowNum="01"
        eyebrow="OVERVIEW · OBSERVE · OPERATE"
        title="Dashboard"
        displayNumber="01"
        subtitle={subtitle}
        action={
          <Button
            variant="secondary"
            size="md"
            leftIcon={<RefreshCw size={14} aria-hidden="true" />}
            onClick={() => void handleScanNow()}
          >
            Scan now
          </Button>
        }
      />

      {/* ── KPI Strip ───────────────────────────────────────────────────────── */}
      {isLoading || !stats ? (
        <KpiStripSkeleton />
      ) : (
        <KpiStrip stats={stats} />
      )}

      {/* ── Основная сетка: Graph + Incidents ──────────────────────────────── */}
      <div className="grid grid-cols-2 gap-6 mt-8">
        <SpendChart />
        <ActiveIncidents
          incidents={incidents}
          isLoading={isLoading}
          isError={isError}
          error={error}
          onRetry={() => void refetch()}
          onIncidentClick={(fbAdId) =>
            void router.navigate({ to: "/ads/$fbAdId", params: { fbAdId } })
          }
        />
      </div>

      {/* ── Recent Events ───────────────────────────────────────────────────── */}
      <div className="mt-8">
        <RecentEvents
          events={events}
          isLoading={isLoading}
          isError={isError}
          error={error}
          onRetry={() => void refetch()}
        />
      </div>

      {/* ── Task Queues ─────────────────────────────────────────────────────── */}
      <div className="grid grid-cols-2 gap-6 mt-8">
        <TaskQueueCard
          title="Disable queue"
          tasks={(disableTasksQ.data as TaskQueueRow[] | undefined) ?? []}
          isLoading={disableTasksQ.isLoading}
          isError={disableTasksQ.isError}
          error={disableTasksQ.error}
          onRetry={() => void disableTasksQ.refetch()}
          emptyLabel="Нет активных задач отключения"
        />
        <TaskQueueCard
          title="Enable queue"
          tasks={(enableTasksQ.data as TaskQueueRow[] | undefined) ?? []}
          isLoading={enableTasksQ.isLoading}
          isError={enableTasksQ.isError}
          error={enableTasksQ.error}
          onRetry={() => void enableTasksQ.refetch()}
          emptyLabel="Нет активных задач включения"
        />
      </div>
    </div>
  );
}
