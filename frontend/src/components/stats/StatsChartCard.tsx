/**
 * StatsChartCard — график воронки залива.
 *
 * Режим today (mode="hourly")  — почасовые ЧЕСТНЫЕ дельты (HourlyPointOut[]).
 * Режим period (mode="daily")  — подневные итоги (DailyPointOut[]).
 * Переключатель метрики spend|leads|deposits через RangeTabs (AreaChart умеет
 * рисовать только spend как основную серию + leads как скрытую — поэтому
 * переключение метрики подменяет, что кладём в поле `spend` AreaDataPoint;
 * подпись меняется вместе с метрикой).
 *
 * Пустое состояние — «Нет данных» при <2 точек (no-fake-data: AreaChart сам
 * не рисует линию на 0-1 точках, но здесь явно подменяем на текстовую заглушку,
 * чтобы не показывать пустой график с осями).
 */

import { useMemo, useState } from "react";
import { ChartCard, RangeTabs, type RangeTabItem } from "@/components/data/charts/ChartCard";
import { AreaChart, type AreaDataPoint } from "@/components/data/charts/AreaChart";
import { Skeleton } from "@/components/ui/Skeleton";
import { formatInt, formatSpend } from "@fb/shared";
import type { StatsPeriod, StatsToday } from "@fb/shared";

// StatsToday/StatsPeriod не экспортируют дочерние point-типы отдельным алиасом
// в @fb/shared — берём их indexed-access типом от родителя (без правок shared-пакета).
type HourlyPoint = NonNullable<StatsToday["meta"]["series_hourly"]>[number];
type DailyPoint = NonNullable<StatsPeriod["meta"]["series_daily"]>[number];

type Metric = "spend" | "leads" | "deposits";

const METRIC_TABS: RangeTabItem[] = [
  { value: "spend", label: "SPEND" },
  { value: "leads", label: "ЛИДЫ" },
  { value: "deposits", label: "ДЕПЫ" },
];

const METRIC_LABEL: Record<Metric, string> = {
  spend: "Траты",
  leads: "Лиды",
  deposits: "Депозиты",
};

interface StatsChartCardProps {
  mode: "hourly" | "daily";
  points?: HourlyPoint[] | DailyPoint[];
  loading?: boolean;
  className?: string;
}

function pointTs(p: HourlyPoint | DailyPoint): string {
  return "ts" in p ? p.ts : `${p.day}T00:00:00Z`;
}

function pointLabel(p: HourlyPoint | DailyPoint, mode: "hourly" | "daily"): string {
  if (mode === "hourly" && "ts" in p) {
    return new Date(p.ts).toLocaleTimeString("ru-RU", { hour: "2-digit", minute: "2-digit" });
  }
  if ("day" in p) {
    return p.day.slice(5); // MM-DD
  }
  return "";
}

function metricValue(p: HourlyPoint | DailyPoint, metric: Metric): number {
  if (metric === "spend") return Number.parseFloat(p.spend ?? "0") || 0;
  if (metric === "leads") return p.leads ?? 0;
  return p.deposits ?? 0;
}

export function StatsChartCard({ mode, points, loading, className }: StatsChartCardProps) {
  const [metric, setMetric] = useState<Metric>("spend");

  const chartData = useMemo<AreaDataPoint[]>(() => {
    if (!points) return [];
    return points.map((p) => ({
      ts: pointTs(p),
      label: pointLabel(p, mode),
      // AreaChart рисует основную серию из поля `spend` — подставляем выбранную метрику.
      spend: metricValue(p, metric),
      leads: p.leads ?? 0,
    }));
  }, [points, mode, metric]);

  const total = useMemo(() => chartData.reduce((sum, p) => sum + p.spend, 0), [chartData]);
  const peak = useMemo(
    () => chartData.reduce((max, p) => Math.max(max, p.spend), 0),
    [chartData],
  );

  const yFmt = metric === "spend" ? (v: number) => `$${formatInt(Math.round(v))}` : (v: number) => formatInt(Math.round(v));
  const valueFmt = metric === "spend" ? formatSpend : (v: number) => formatInt(Math.round(v));

  if (loading) {
    return (
      <ChartCard
        eyebrow={mode === "hourly" ? "ВОРОНКА × ЧАС · СУТКИ КАБИНЕТА" : "ВОРОНКА × ДЕНЬ · ПЕРИОД"}
        title={METRIC_LABEL[metric]}
        rangeControl={<RangeTabs items={METRIC_TABS} value={metric} onChange={(v) => setMetric(v as Metric)} />}
        className={className}
      >
        <Skeleton height={280} />
      </ChartCard>
    );
  }

  const hasEnoughData = chartData.length >= 2;

  return (
    <ChartCard
      eyebrow={mode === "hourly" ? "ВОРОНКА × ЧАС · СУТКИ КАБИНЕТА" : "ВОРОНКА × ДЕНЬ · ПЕРИОД"}
      title={METRIC_LABEL[metric]}
      rangeControl={<RangeTabs items={METRIC_TABS} value={metric} onChange={(v) => setMetric(v as Metric)} />}
      metaItems={
        hasEnoughData
          ? [
              { label: "Итого", value: valueFmt(total) },
              { label: "Пик", value: valueFmt(peak) },
            ]
          : undefined
      }
      className={className}
    >
      {hasEnoughData ? (
        <AreaChart data={chartData} yTickFormatter={yFmt} />
      ) : (
        <div className="h-[280px] flex items-center justify-center text-[13px] text-bg-9">
          Нет данных
        </div>
      )}
    </ChartCard>
  );
}
