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
import { ScanCluster, type ScanProgress } from "@/components/dashboard/ScanCluster";
import { PausedBanner } from "@/components/dashboard/PausedBanner";
import { ScanBlockedBanner } from "@/components/dashboard/ScanBlockedBanner";
import {
  KPI_CELL_STATE,
  SparklineKpiRow,
  SparklineKpiRowSkeleton,
} from "@/components/dashboard/SparklineKpiRow";
import { LiveTail } from "@/components/dashboard/LiveTail";
import { TaskQueues } from "@/components/dashboard/TaskQueues";

import { useDashboardBatch, useChartData } from "@/lib/api/dashboard";
import { useDisableTasks, useEnableTasks } from "@/lib/api/ads";
import {
  useObserverSettings,
  useObserverStatus,
  useToggleScanning,
} from "@/lib/api/settings";
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
  // pollingFallback=true — WS недоступен, обновление по таймеру (показываем в live-tail).
  const { pollingFallback } = useRealtimeInvalidation();

  // Главный агрегат + spend-ряд + очереди + observer-настройки.
  const { data: batch, isLoading, isError, error, refetch } = useDashboardBatch();
  // cabinet_day=true — ось с 00:00 текущих суток кабинета (не скользящие 24ч).
  const chartQ = useChartData({ bucket: "hour", cabinet_day: true });
  const disableTasksQ = useDisableTasks({ status: "PENDING,RUNNING,RETRYING", limit: 20 });
  const enableTasksQ = useEnableTasks({ status: "PENDING,RUNNING,RETRYING", limit: 20 });
  const observerQ = useObserverSettings();
  const observerStatusQ = useObserverStatus();
  const toggleScanning = useToggleScanning();

  const stats = batch?.stats;

  // Мульти-кабинет: прогресс цикла из observer:runtime (поля проброшены через extra).
  const scanProgress = useMemo<ScanProgress | null>(() => {
    const extra = (observerStatusQ.data?.extra ?? {}) as Record<string, unknown>;
    const total = typeof extra.accounts_total === "number" ? extra.accounts_total : null;
    if (!total || total < 1) return null;
    return {
      total,
      done: typeof extra.accounts_done === "number" ? extra.accounts_done : null,
      current:
        typeof extra.current_account_id === "string" ? extra.current_account_id : null,
    };
  }, [observerStatusQ.data]);

  // observer вкл/выкл: настройка is_scanning_enabled — основной источник;
  // фолбэк на observer_status из stats. По умолчанию (загрузка) считаем ВКЛ,
  // чтобы не моргать paused-баннером.
  const scanOn =
    observerQ.data?.is_scanning_enabled ??
    (stats ? stats.observer_status === "running" : true);
  const intervalSeconds = observerQ.data?.default_interval_seconds ?? 30;
  // Реальное время следующего скана (адаптивный интервал + jitter) из observer:runtime.
  const nextScanAt = useMemo<string | null>(() => {
    const v = (observerStatusQ.data?.extra ?? {})["next_scan_at"];
    return typeof v === "string" ? v : null;
  }, [observerStatusQ.data]);

  // Hero-число = ВСЕ объявления под контролем бота, включая отключённые (он их и
  // выключил — они под контролем, просто не крутятся). Берём total_ads_monitored
  // (как мини-апп), а не normal+warning+stop+claimed — иначе 14 disabled молча
  // выпадали и под «контролем» висело 4 вместо 18. disabled показываем в HealthBar.
  const normal = stats?.ads_in_normal ?? 0;
  const warning = stats?.ads_in_warning ?? 0;
  const stop = stats?.ads_in_stop ?? 0;
  const claimed = stats?.ads_in_claimed ?? 0;
  const disabled = stats?.ads_in_disabled ?? 0;
  const totalControlled =
    stats?.total_ads_monitored ?? normal + warning + stop + claimed + disabled;

  // Spend-ряд по часам (реальные данные) для графика и ACTIVE-sparkline.
  const spendSeries = useMemo<number[]>(
    () => (chartQ.data ?? []).map((b) => Number(b.spend ?? 0)),
    [chartQ.data],
  );
  // Headline-спенд: авторитетный current_day_spend из stats (latest-per-ad с полом по
  // полуночи кабинета). Не суммируем серию — кумулятивные снимки задвоят деньги.
  // При null/undefined (бэк не вернул) — 0, graceful прочерк через formatSpend.
  const spendTotal = parseFloat(stats?.current_day_spend ?? "0") || 0;

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

  // выключить observer (on → paused).
  function handleDisable() {
    toggleScanning.mutate(false);
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
          onDisable={handleDisable}
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
          nextScanAt={nextScanAt}
          intervalSeconds={intervalSeconds}
          scanProgress={scanProgress}
          onScan={handleScanNow}
          onEnable={handleEnable}
          onDisable={handleDisable}
        />

        {/* ── paused banner ───────────────────────────────────────────────── */}
        {!scanOn && (
          <div className="mb-6">
            <PausedBanner since={null} onEnable={handleEnable} />
          </div>
        )}

        {/* ── scan-blocked banner (скан вкл, но allowlist пуст → ничего не мониторим) ── */}
        {scanOn && stats?.scan_blocked_reason && (
          <div className="mb-6">
            <ScanBlockedBanner
              reason={stats.scan_blocked_reason}
              onNavigate={() => void router.navigate({ to: "/campaigns" })}
            />
          </div>
        )}

        {/* ── hero + chart ────────────────────────────────────────────────── */}
        <div className="mb-6 grid grid-cols-[1fr_1.1fr] items-center gap-8 border-b border-bg-5 pb-6">
          <Hero
            total={totalControlled}
            normal={normal}
            warning={warning}
            stop={stop}
            disabled={disabled}
          />
          <Card padded className="p-5">
            <div className="mb-3 flex items-baseline justify-between">
              <Eyebrow>SPEND × ЧАС · СУТКИ КАБИНЕТА</Eyebrow>
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
            <SparklineKpiRow
              stats={stats}
              spendSpark={spendSeries}
              onCellClick={(key) => {
                // Клик по KPI → Ads с фильтром по соответствующему состоянию.
                const state = KPI_CELL_STATE[key];
                if (state) {
                  void router.navigate({ to: "/ads", search: { state } });
                }
              }}
            />
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
                    : pollingFallback
                      ? "var(--warning)"
                      : events.length > 0
                        ? "var(--success)"
                        : "var(--bg-7)"
                }
              />
              {!scanOn
                ? "на паузе"
                : pollingFallback
                  ? "polling-режим (WS недоступен)"
                  : events.length > 0
                    ? "поток активен"
                    : "тихо"}
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
  nextScanAt?: string | null;
  intervalSeconds: number;
  scanProgress?: ScanProgress | null;
  onScan: () => void;
  onEnable: () => void;
  onDisable: () => void;
}

function PageHeaderBlock({
  scanOn,
  lastScanAt,
  nextScanAt,
  intervalSeconds,
  scanProgress,
  onScan,
  onEnable,
  onDisable,
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
        nextScanAt={nextScanAt}
        intervalSeconds={intervalSeconds}
        scanProgress={scanProgress}
        onScan={onScan}
        onEnable={onEnable}
        onDisable={onDisable}
      />
    </div>
  );
}
