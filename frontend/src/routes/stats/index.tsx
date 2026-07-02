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
import { useStatsToday, useStatsPeriod } from "@/lib/api/stats";

export const Route = createFileRoute("/stats/")({
  component: StatsPage,
});

function StatsPage() {
  const [mode, setMode] = useState<StatsMode>({ kind: "today" });
  const [breakdownKind] = useState<"offer" | "campaign">("offer");

  const todayQ = useStatsToday(mode.kind === "today" ? breakdownKind : undefined);
  const periodQ = useStatsPeriod(
    mode.kind === "period" ? mode.period : { from_iso: "", to_iso: "" },
  );

  const isToday = mode.kind === "today";
  // В period-режиме useStatsToday не активируем повторным вызовом — enabled нет
  // у useStatsToday намеренно (staleTime достаточно), поэтому просто не используем
  // его данные вне today-режима.
  const activeQuery = isToday ? todayQ : periodQ;

  const subtitle = isToday
    ? "Сутки кабинета"
    : `${mode.period.from_iso.slice(0, 10)} — ${mode.period.to_iso.slice(0, 10)}`;

  return (
    <>
      <PageHeader
        eyebrowNum="05"
        eyebrow="STATS · ВОРОНКА ЗАЛИВА"
        title="Статистика"
        subtitle={subtitle}
      />

      <div className="flex items-center gap-2 mb-6 flex-wrap">
        <StatsPeriodTabs value={mode} onChange={setMode} />
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
  return (
    <div className="flex flex-col gap-6">
      <FunnelKpiRow data={data?.meta} loading={query.isLoading} />
      <DerivedMetricsGrid data={data?.meta.derived} loading={query.isLoading} />
      <StatsChartCard mode="hourly" points={data?.meta.series_hourly} loading={query.isLoading} />
      <FunnelBar data={data?.meta.totals} loading={query.isLoading} />
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
  return (
    <div className="flex flex-col gap-6">
      <FunnelKpiRow data={data?.meta} loading={query.isLoading} />
      <DerivedMetricsGrid data={data?.meta.derived} loading={query.isLoading} />
      <StatsChartCard mode="daily" points={data?.meta.series_daily} loading={query.isLoading} />
      <FunnelBar data={data?.meta.totals} loading={query.isLoading} />
      <TrackerBlock data={data?.tracker} loading={query.isLoading} />
    </div>
  );
}
