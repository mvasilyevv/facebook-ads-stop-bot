/**
 * SpendChart — ChartCard с AreaChart трат за выбранный диапазон.
 * Range-табы: 6h / 24h / 48h / 7d.
 * Источник: useChartData (бакетированные данные).
 */

import { useState, useMemo } from "react";
import { ChartCard, RangeTabs } from "@/components/data/charts/ChartCard";
import { AreaChart, type AreaDataPoint } from "@/components/data/charts/AreaChart";
import { Skeleton } from "@/components/ui/Skeleton";
import { EmptyState } from "@/components/ui/EmptyState";
import { ErrorState } from "@/components/ui/ErrorState";
import { useChartData } from "@/lib/api/dashboard";
import { formatSpend, formatInt } from "@fb/shared";
import type { ChartBucket } from "@fb/shared";

// ─── Варианты диапазонов ──────────────────────────────────────────────────────

const RANGE_TABS = [
  { value: "6", label: "6ч" },
  { value: "24", label: "24ч" },
  { value: "48", label: "48ч" },
  { value: "168", label: "7д" },
];

// ─── Конвертация ChartBucket → AreaDataPoint ──────────────────────────────────

function bucketToPoint(b: ChartBucket): AreaDataPoint {
  const d = new Date(b.ts);
  const label = d.toLocaleTimeString("ru", {
    hour: "2-digit",
    minute: "2-digit",
    hour12: false,
  });
  return {
    ts: b.ts,
    label,
    spend: Number(b.spend ?? 0),
    leads: b.leads ?? 0,
  };
}

// ─── Компонент ────────────────────────────────────────────────────────────────

export function SpendChart() {
  const [range, setRange] = useState("24");
  const hours = Number(range);
  const bucket = hours <= 48 ? "hour" : "day";

  const { data, isLoading, isError, error, refetch } = useChartData({ hours, bucket });

  // Агрегируем мета-показатели
  const meta = useMemo(() => {
    if (!data || data.length === 0) return null;
    const totalSpend = data.reduce((s, b) => s + Number(b.spend ?? 0), 0);
    const avgAds = Math.round(
      data.reduce((s, b) => s + (b.active_ads ?? 0), 0) / data.length,
    );
    const peakSpend = Math.max(...data.map((b) => Number(b.spend ?? 0)));
    return { totalSpend, avgAds, peakSpend };
  }, [data]);

  const points = useMemo<AreaDataPoint[]>(() => {
    if (!data) return [];
    return data.map(bucketToPoint);
  }, [data]);

  return (
    <ChartCard
      eyebrow="02 SPEND × HOUR"
      title={`Spend rate · last ${hours}ч`}
      rangeControl={
        <RangeTabs
          items={RANGE_TABS}
          value={range}
          onChange={setRange}
          aria-label="Диапазон графика трат"
        />
      }
      metaItems={
        meta
          ? [
              { label: "Итого", value: formatSpend(meta.totalSpend) },
              { label: "Пик", value: formatSpend(meta.peakSpend) },
              { label: "Avg объявлений", value: formatInt(meta.avgAds) },
            ]
          : []
      }
    >
      {isError ? (
        <ErrorState
          title="Не удалось загрузить данные графика."
          error={error}
          onRetry={() => void refetch()}
        />
      ) : isLoading ? (
        // Skeleton-заглушка нужной высоты
        <div role="status" aria-label="Загрузка графика">
          <Skeleton height={280} className="w-full" />
        </div>
      ) : points.length === 0 ? (
        <EmptyState
          title="Данных нет"
          description="Нет данных о тратах за выбранный период."
        />
      ) : (
        <AreaChart data={points} height={280} />
      )}
    </ChartCard>
  );
}
