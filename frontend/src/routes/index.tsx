/**
 * Dashboard — главная страница обзора (канон design_handoff/web-dashboard.jsx).
 *
 * Структура (top → bottom):
 *   page-header: eyebrow «01 / ОБЗОР · ПО ОБЪЯВЛЕНИЯМ · LIVE|ПАУЗА» + h1 «Панель»
 *               (30px, no dot) + ScanCluster справа.
 *   [PausedBanner — full-width, если observer выключен]
 *   hero + chart (grid 1fr 1.1fr): Hero (88px count-up + HealthBar) | SpendChart card.
 *   SparklineKpiRow (4 ячейки ACTIVE/WARNING/STOP/DISABLED).
 *   live-tail: eyebrow «02 / … LIVE-TAIL» + поток-маркер + LiveTail card.
 *   task queues: «03 / ОЧЕРЕДЬ ЗАДАЧ» — DISABLE/ENABLE.
 *
 * Данные (реальные):
 *   useDashboardBatch — stats + recent_alerts. useChartData — spend по часам.
 *   useDisableTasks/useEnableTasks — очереди. useObserverSettings — interval +
 *   is_scanning_enabled. useRealtimeInvalidation — live-обновления (WS).
 *   apiSend scan-now / useToggleScanning — управление observer.
 */

import { createFileRoute, useRouter } from "@tanstack/react-router";
import { useMemo } from "react";

import { Eyebrow } from "@/components/data/Eyebrow";
import { PulseDot } from "@/components/data/PulseDot";
import { Card } from "@/components/ui/Card";
import { ErrorState } from "@/components/ui/ErrorState";

import { BlueprintBg } from "@/components/dashboard/BlueprintBg";
import { Hero } from "@/components/dashboard/Hero";
import { SpendChart } from "@/components/dashboard/SpendChart";
import { ScanCluster } from "@/components/dashboard/ScanCluster";
import { PausedBanner } from "@/components/dashboard/PausedBanner";
import {
  SparklineKpiRow,
  SparklineKpiRowSkeleton,
} from "@/components/dashboard/SparklineKpiRow";
import { LiveTail } from "@/components/dashboard/LiveTail";
import { TaskQueues } from "@/components/dashboard/TaskQueues";

import { useDashboardBatch, useChartData } from "@/lib/api/dashboard";
import { useDisableTasks, useEnableTasks } from "@/lib/api/ads";
import { useObserverSettings, useToggleScanning } from "@/lib/api/settings";
import { useRealtimeInvalidation } from "@/lib/websocket/useRealtimeInvalidation";
import { apiSend } from "@/lib/api/client";

import type { AlertEvent, TaskQueueRow } from "@fb/shared";
import { formatSpend } from "@fb/shared";

// ─── Route ────────────────────────────────────────────────────────────────────

export const Route = createFileRoute("/")({
  component: DashboardPage,
});

// ─── Компонент ────────────────────────────────────────────────────────────────

function DashboardPage() {
  const router = useRouter();

  // WS live-invalidation — данные обновляются сразу после сканов.
  useRealtimeInvalidation();

  // Главный агрегат + spend-ряд + очереди + observer-настройки.
  const { data: batch, isLoading, isError, error, refetch } = useDashboardBatch();
  const chartQ = useChartData({ hours: 24, bucket: "hour" });
  const disableTasksQ = useDisableTasks({ status: "PENDING,RUNNING,RETRYING", limit: 20 });
  const enableTasksQ = useEnableTasks({ status: "PENDING,RUNNING,RETRYING", limit: 20 });
  const observerQ = useObserverSettings();
  const toggleScanning = useToggleScanning();

  const stats = batch?.stats;

  // observer вкл/выкл: настройка is_scanning_enabled — основной источник;
  // фолбэк на observer_status из stats. По умолчанию (загрузка) считаем ВКЛ,
  // чтобы не моргать paused-баннером.
  const scanOn =
    observerQ.data?.is_scanning_enabled ??
    (stats ? stats.observer_status === "running" : true);
  const intervalSeconds = observerQ.data?.default_interval_seconds ?? 30;

  // Hero-число = под контролем (normal + warning + stop + claimed).
  const normal = stats?.ads_in_normal ?? 0;
  const warning = stats?.ads_in_warning ?? 0;
  const stop = stats?.ads_in_stop ?? 0;
  const claimed = stats?.ads_in_claimed ?? 0;
  const totalControlled = normal + warning + stop + claimed;

  // Spend-ряд по часам (реальные данные) для графика и ACTIVE-sparkline.
  const spendSeries = useMemo<number[]>(
    () => (chartQ.data ?? []).map((b) => Number(b.spend ?? 0)),
    [chartQ.data],
  );
  const spendTotal = useMemo(
    () => spendSeries.reduce((a, b) => a + b, 0),
    [spendSeries],
  );

  // live-tail: реальные алерты.
  const events = useMemo<AlertEvent[]>(
    () => (batch?.recent_alerts as AlertEvent[] | undefined) ?? [],
    [batch],
  );

  // Очереди задач.
  const disableTasks = (disableTasksQ.data as TaskQueueRow[] | undefined) ?? [];
  const enableTasks = (enableTasksQ.data as TaskQueueRow[] | undefined) ?? [];

  // scan-now → redis-триггер.
  function handleScanNow() {
    void apiSend("POST", "/settings/observer/scan-now").catch(() => {
      // Игнорируем — observer сам среагирует по интервалу.
    });
  }

  // включить observer (paused → on).
  function handleEnable() {
    toggleScanning.mutate(true);
  }

  // Фатальная ошибка batch — показываем header + ErrorState.
  if (isError && !batch) {
    return (
      <div className="relative" aria-label="Dashboard">
        <PageHeaderBlock
          scanOn={scanOn}
          lastScanAt={null}
          intervalSeconds={intervalSeconds}
          onScan={handleScanNow}
          onEnable={handleEnable}
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
    <div className="relative" aria-label="Dashboard">
      {/* Blueprint-фон во всю ширину контента (decorative). */}
      <BlueprintBg />

      <div className="relative">
        {/* ── page header ─────────────────────────────────────────────────── */}
        <PageHeaderBlock
          scanOn={scanOn}
          lastScanAt={stats?.last_scan_at ?? null}
          intervalSeconds={intervalSeconds}
          onScan={handleScanNow}
          onEnable={handleEnable}
        />

        {/* ── paused banner ───────────────────────────────────────────────── */}
        {!scanOn && (
          <div className="mb-6">
            <PausedBanner since={null} onEnable={handleEnable} />
          </div>
        )}

        {/* ── hero + chart ────────────────────────────────────────────────── */}
        <div className="mb-6 grid grid-cols-[1fr_1.1fr] items-center gap-8 border-b border-bg-5 pb-6">
          <Hero total={totalControlled} normal={normal} warning={warning} stop={stop} />
          <Card padded className="p-5">
            <div className="mb-3 flex items-baseline justify-between">
              <Eyebrow>SPEND × ЧАС · 24Ч</Eyebrow>
              <span className="font-display text-[18px] tabular-nums text-bg-11">
                {formatSpend(spendTotal)}
              </span>
            </div>
            <SpendChart data={spendSeries} height={170} live={scanOn} animate />
          </Card>
        </div>

        {/* ── sparkline KPI row ───────────────────────────────────────────── */}
        <div className="mb-8">
          {isLoading || !stats ? (
            <SparklineKpiRowSkeleton />
          ) : (
            <SparklineKpiRow stats={stats} spendSpark={spendSeries} />
          )}
        </div>

        {/* ── live-tail feed ──────────────────────────────────────────────── */}
        <div className="mb-8">
          <div className="mb-4 flex items-center justify-between">
            <Eyebrow num="02">СОБЫТИЯ ПО ОБЪЯВЛЕНИЯМ · LIVE-TAIL</Eyebrow>
            <span className="inline-flex items-center gap-[7px] text-[12px] text-bg-9">
              <PulseDot
                size={6}
                color={
                  !scanOn
                    ? "var(--warning)"
                    : events.length > 0
                      ? "var(--success)"
                      : "var(--bg-7)"
                }
              />
              {!scanOn ? "на паузе" : events.length > 0 ? "поток активен" : "тихо"}
            </span>
          </div>
          <Card padded={false}>
            <LiveTail
              events={events}
              max={8}
              frozen={!scanOn}
              onRow={(e) =>
                e.fb_ad_id
                  ? void router.navigate({
                      to: "/ads/$fbAdId",
                      params: { fbAdId: e.fb_ad_id },
                    })
                  : undefined
              }
            />
          </Card>
        </div>

        {/* ── task queues ─────────────────────────────────────────────────── */}
        <TaskQueues
          disableTasks={disableTasks}
          enableTasks={enableTasks}
          disableLoading={disableTasksQ.isLoading}
          enableLoading={enableTasksQ.isLoading}
          disableError={disableTasksQ.isError}
          enableError={enableTasksQ.isError}
          onRetryDisable={() => void disableTasksQ.refetch()}
          onRetryEnable={() => void enableTasksQ.refetch()}
        />
      </div>
    </div>
  );
}

// ─── Page header (eyebrow + h1 «Панель» + ScanCluster) ─────────────────────────

interface PageHeaderBlockProps {
  scanOn: boolean;
  lastScanAt: string | null;
  intervalSeconds: number;
  onScan: () => void;
  onEnable: () => void;
}

function PageHeaderBlock({
  scanOn,
  lastScanAt,
  intervalSeconds,
  onScan,
  onEnable,
}: PageHeaderBlockProps) {
  return (
    <div className="mb-6 flex flex-wrap items-start justify-between gap-6">
      <div>
        <Eyebrow num="01">ОБЗОР · ПО ОБЪЯВЛЕНИЯМ · {scanOn ? "LIVE" : "ПАУЗА"}</Eyebrow>
        <h1
          className="m-0 mt-2 font-display font-medium text-bg-11"
          style={{ fontSize: 30, letterSpacing: "-0.02em" }}
        >
          Панель
        </h1>
      </div>
      <ScanCluster
        scanOn={scanOn}
        lastScanAt={lastScanAt}
        intervalSeconds={intervalSeconds}
        onScan={onScan}
        onEnable={onEnable}
      />
    </div>
  );
}
