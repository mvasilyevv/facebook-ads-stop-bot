/**
 * Stats-страница — «Статистика залива».
 *
 * Режим «Сегодня» (kind=today): GET /stats/today[?breakdown] — почасовые
 * дельты + разрез по офферу/кампании (endpoint отдаёт breakdown только
 * в этом режиме).
 * Режим «Период» (kind=period, пресеты 7/30/90д или custom): GET /stats/period
 * — подневные серии. Breakdown в period-режиме не показываем — эндпоинт его
 * не отдаёт (см. StatsPeriodOut — поля breakdown нет).
 */

import { useState } from "react";
import { createFileRoute } from "@tanstack/react-router";
import { PageHeader } from "@/components/layout/PageHeader";
import { StatsPeriodTabs, type StatsMode } from "@/components/stats/StatsPeriodTabs";
import { FunnelKpiRow } from "@/components/stats/FunnelKpiRow";
import { DerivedMetricsGrid } from "@/components/stats/DerivedMetricsGrid";
import { StatsChartCard } from "@/components/stats/StatsChartCard";
import { FunnelBar } from "@/components/stats/FunnelBar";
import { TrackerBlock } from "@/components/stats/TrackerBlock";
import { BreakdownTable } from "@/components/stats/BreakdownTable";
import { ErrorState } from "@/components/ui/ErrorState";
import { EmptyState } from "@/components/ui/EmptyState";
import { MetaDelayedNote } from "@/components/data/SourceStatus";
import { useStatsToday, useStatsPeriod } from "@/lib/api/stats";
import { useRealtimeInvalidation } from "@/lib/websocket/useRealtimeInvalidation";

export const Route = createFileRoute("/stats/")({
  component: StatsPage,
});

function StatsPage() {
  useRealtimeInvalidation();
  const [mode, setMode] = useState<StatsMode>({ kind: "today" });
  const [breakdownKind, setBreakdownKind] = useState<"offer" | "campaign">("offer");

  const isToday = mode.kind === "today";
  const todayQ = useStatsToday(isToday ? breakdownKind : undefined, isToday);
  const periodQ = useStatsPeriod(
    mode.kind === "period" ? mode.period : { from_iso: "", to_iso: "" },
    mode.kind === "period",
  );

  // Неактивный endpoint отключаем: смена режима не создаёт лишний фоновый запрос.
  const activeQuery = isToday ? todayQ : periodQ;

  const subtitle = isToday
    ? "Сутки кабинета"
    : `${formatDate(mode.period.from_iso)} — ${formatDate(mode.period.to_iso)}`;

  return (
    <>
      <PageHeader
        eyebrowNum="05"
        eyebrow="STATS · ВОРОНКА ЗАЛИВА"
        title="Статистика"
        subtitle={subtitle}
      />

      <div className="mb-6 flex flex-wrap items-center justify-between gap-3">
        <StatsPeriodTabs value={mode} onChange={setMode} />
        <div className="flex flex-wrap items-center gap-3">
          <MetaDelayedNote />
          {isToday ? (
            <div className="flex items-center gap-1 rounded-[var(--radius-2)] border border-[var(--hairline)] bg-bg-1 p-1" role="group" aria-label="Разрез статистики">
            {(["offer", "campaign"] as const).map((kind) => (
              <button
                key={kind}
                type="button"
                aria-pressed={breakdownKind === kind}
                onClick={() => setBreakdownKind(kind)}
                className={`min-h-7 rounded-[var(--radius-1)] px-3 text-[12px] transition-colors focus-visible:outline-2 focus-visible:outline-offset-1 focus-visible:outline-accent ${
                  breakdownKind === kind
                    ? "bg-accent text-bg-0"
                    : "text-bg-10 hover:bg-bg-3 hover:text-bg-11"
                }`}
              >
                {kind === "offer" ? "По офферам" : "По кампаниям"}
              </button>
            ))}
            </div>
          ) : null}
        </div>
      </div>

      {activeQuery.isError && !activeQuery.data ? (
        <ErrorState
          title="Не удалось загрузить статистику."
          error={activeQuery.error}
          onRetry={() => void activeQuery.refetch()}
        />
      ) : isToday ? (
        <TodayView query={todayQ} breakdownKind={breakdownKind} />
      ) : (
        <PeriodView query={periodQ} />
      )}
    </>
  );
}

// ─── Режим «Сегодня» ────────────────────────────────────────────────────────────

function TodayView({
  query,
  breakdownKind,
}: {
  query: ReturnType<typeof useStatsToday>;
  breakdownKind: "offer" | "campaign";
}) {
  const data = query.data;
  if (!data && !query.isLoading) {
    return (
      <EmptyState
        title="Нет подтверждённых данных"
        description="После первого успешного скана здесь появится воронка текущих суток кабинета."
      />
    );
  }
  return (
    <div className="flex flex-col gap-6">
      <DataSnapshotLine generatedAt={data?.generated_at} />
      <FunnelKpiRow
        data={data?.meta}
        trackerData={data?.tracker}
        loading={query.isLoading}
      />
      <DerivedMetricsGrid
        data={data?.meta.derived}
        metaTotals={data?.meta.totals}
        trackerData={data?.tracker}
        loading={query.isLoading}
      />
      <StatsChartCard mode="hourly" points={data?.meta.series_hourly} loading={query.isLoading} />
      <FunnelBar
        data={data?.meta.totals}
        trackerData={data?.tracker}
        loading={query.isLoading}
      />
      <TrackerBlock data={data?.tracker} loading={query.isLoading} />
      <BreakdownTable
        rows={data?.breakdown}
        breakdownKind={breakdownKind}
        loading={query.isLoading}
      />
    </div>
  );
}

// ─── Режим «Период» ──────────────────────────────────────────────────────────────

function PeriodView({ query }: { query: ReturnType<typeof useStatsPeriod> }) {
  const data = query.data;
  if (!data && !query.isLoading) {
    return (
      <EmptyState
        title="За период нет данных"
        description="Выберите другой диапазон или дождитесь первого успешного скана."
      />
    );
  }
  return (
    <div className="flex flex-col gap-6">
      <FunnelKpiRow
        data={data?.meta}
        trackerData={data?.tracker}
        loading={query.isLoading}
      />
      <DerivedMetricsGrid
        data={data?.meta.derived}
        metaTotals={data?.meta.totals}
        trackerData={data?.tracker}
        loading={query.isLoading}
      />
      <StatsChartCard mode="daily" points={data?.meta.series_daily} loading={query.isLoading} />
      <FunnelBar
        data={data?.meta.totals}
        trackerData={data?.tracker}
        loading={query.isLoading}
      />
      <TrackerBlock data={data?.tracker} loading={query.isLoading} />
    </div>
  );
}

function formatDate(value: string): string {
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) return value.slice(0, 10);
  return date.toLocaleDateString("ru-RU", { day: "2-digit", month: "2-digit", year: "numeric" });
}

function DataSnapshotLine({ generatedAt }: { generatedAt?: string | null }) {
  if (!generatedAt) return null;
  const date = new Date(generatedAt);
  const label = Number.isNaN(date.getTime())
    ? generatedAt
    : date.toLocaleString("ru-RU", {
        day: "2-digit",
        month: "short",
        hour: "2-digit",
        minute: "2-digit",
      });
  return (
    <p className="-mb-2 font-display text-[11px] text-bg-9" role="status">
      Срез сформирован {label}
    </p>
  );
}
