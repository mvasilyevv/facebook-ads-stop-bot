/**
 * SpendChartCard — area-график spend по бакетам (Recharts).
 *
 * Данные: useChartData({ hours, bucket }).
 *   Today  → hours=24,  bucket="hour"
 *   7 days → hours=168, bucket="day"
 *
 * Монохромный accent-градиент, tabular-числа на осях, кастомный tooltip.
 * Высота фиксирована через ChartWrapper (родитель с px-высотой) — это решает
 * Recharts width/height=-1 при ResponsiveContainer 100%/100%.
 *
 * Состояния: Loading (Skeleton-плашка), Error (ErrorState+retry),
 * Empty (EmptyState внутри карточки — бакеты пустые).
 */

import { useMemo } from "react";
import {
  Area,
  AreaChart,
  CartesianGrid,
  ResponsiveContainer,
  Tooltip,
  XAxis,
  YAxis,
} from "recharts";
import { LineChart as LineChartIcon } from "lucide-react";
import { Card } from "@/components/ui/Card";
import { Eyebrow } from "@/components/layout/Eyebrow";
import { Skeleton } from "@/components/ui/Skeleton";
import { ErrorState } from "@/components/ui/ErrorState";
import { EmptyState } from "@/components/ui/EmptyState";
import { ChartWrapper, CHART_COLORS, CustomTooltipContent } from "@/components/data/ChartWrapper";
import { cn } from "@/lib/utils/cn";
import { formatSpend, formatInt } from "@/lib/utils/format";
import type { ChartBucket } from "@/lib/types/api";

type RangeKey = "today" | "7d";

const RANGES: Record<RangeKey, { label: string; hours: number; bucket: "hour" | "day" }> = {
  today: { label: "Today", hours: 24, bucket: "hour" },
  "7d": { label: "7 days", hours: 168, bucket: "day" },
};

/** Точка для Recharts: spend как number (бэкенд отдаёт Decimal-строкой). */
interface ChartPoint {
  ts: string;
  label: string;
  spend: number;
  leads: number;
}

interface SpendChartCardProps {
  /** Активный диапазон поднят в родитель — чтобы он управлял useChartData. */
  range: RangeKey;
  onRangeChange: (range: RangeKey) => void;
  buckets: ChartBucket[] | undefined;
  isLoading: boolean;
  isError: boolean;
  error?: unknown;
  onRetry?: () => void;
}

export function SpendChartCard({
  range,
  onRangeChange,
  buckets,
  isLoading,
  isError,
  error,
  onRetry,
}: SpendChartCardProps) {
  const cfg = RANGES[range];

  const points = useMemo<ChartPoint[]>(() => {
    if (!buckets) return [];
    return buckets.map((b) => ({
      ts: b.ts,
      label: formatBucketLabel(b.ts, cfg.bucket),
      spend: toNumber(b.spend),
      leads: b.leads ?? 0,
    }));
  }, [buckets, cfg.bucket]);

  const summary = useMemo(() => computeSummary(points), [points]);

  return (
    <Card
      action={
        <RangeTabs value={range} onChange={onRangeChange} />
      }
    >
      <div className="flex items-start justify-between mb-5">
        <div>
          <Eyebrow num="02">Spend × {cfg.bucket === "hour" ? "Hour" : "Day"}</Eyebrow>
          <h3 className="mt-1.5 font-display text-[13px] font-medium tracking-wider text-bg-11 m-0">
            Spend rate · {range === "today" ? "last 24h" : "last 7d"}
          </h3>
        </div>
      </div>

      <ChartBody
        points={points}
        isLoading={isLoading}
        isError={isError}
        error={error}
        onRetry={onRetry}
      />

      {!isLoading && !isError && points.length > 0 ? (
        <div className="flex gap-6 pt-3 mt-3 border-t border-bg-5 font-display text-[11px] tracking-wider text-bg-10">
          <ChartMetaItem label="total" value={formatSpend(summary.total)} />
          <ChartMetaItem label="avg" value={formatSpend(summary.avg)} />
          <ChartMetaItem label="peak" value={formatSpend(summary.peak)} />
          <ChartMetaItem label="leads" value={formatInt(summary.leads)} />
        </div>
      ) : null}
    </Card>
  );
}

/** Тело графика: разруливает loading/error/empty/data. */
function ChartBody({
  points,
  isLoading,
  isError,
  error,
  onRetry,
}: {
  points: ChartPoint[];
  isLoading: boolean;
  isError: boolean;
  error?: unknown;
  onRetry?: () => void;
}) {
  if (isError) {
    return (
      <div className="h-[280px] flex items-center">
        <ErrorState
          className="w-full"
          title="Не удалось загрузить график."
          error={error}
          onRetry={onRetry}
        />
      </div>
    );
  }

  if (isLoading) {
    return <Skeleton height={280} className="w-full" />;
  }

  if (points.length === 0) {
    return (
      <div className="h-[280px] flex items-center justify-center">
        <EmptyState
          icon={<LineChartIcon size={36} strokeWidth={1.25} aria-hidden="true" />}
          title="Нет данных за период"
          description="За выбранное окно метрик spend ещё нет."
        />
      </div>
    );
  }

  return (
    <ChartWrapper height={280}>
      <ResponsiveContainer width="100%" height="100%">
        <AreaChart data={points} margin={{ top: 8, right: 8, bottom: 0, left: 0 }}>
          <defs>
            <linearGradient id="spendArea" x1="0" y1="0" x2="0" y2="1">
              <stop offset="0%" stopColor={CHART_COLORS.primary} stopOpacity={0.18} />
              <stop offset="100%" stopColor={CHART_COLORS.primary} stopOpacity={0} />
            </linearGradient>
          </defs>
          <CartesianGrid stroke={CHART_COLORS.grid} vertical={false} />
          <XAxis
            dataKey="label"
            tick={{ fill: CHART_COLORS.axisText, fontSize: 10, fontFamily: "JetBrains Mono" }}
            axisLine={{ stroke: CHART_COLORS.grid }}
            tickLine={false}
            minTickGap={32}
          />
          <YAxis
            tick={{ fill: CHART_COLORS.axisText, fontSize: 10, fontFamily: "JetBrains Mono" }}
            axisLine={false}
            tickLine={false}
            width={48}
            tickFormatter={(v: number) => `$${formatInt(Math.round(v))}`}
          />
          <Tooltip
            content={<CustomTooltipContent />}
            cursor={{ stroke: CHART_COLORS.axis, strokeDasharray: "2 2" }}
          />
          <Area
            type="monotone"
            dataKey="spend"
            name="spend"
            stroke={CHART_COLORS.primary}
            strokeWidth={1.5}
            fill="url(#spendArea)"
            dot={false}
            activeDot={{ r: 3, fill: CHART_COLORS.primary }}
            isAnimationActive={false}
          />
        </AreaChart>
      </ResponsiveContainer>
    </ChartWrapper>
  );
}

/** Сегментированный таб-переключатель диапазона (мок: chart-tabs). */
function RangeTabs({ value, onChange }: { value: RangeKey; onChange: (r: RangeKey) => void }) {
  const keys = Object.keys(RANGES) as RangeKey[];
  return (
    <div className="flex border border-bg-5 bg-bg-2" role="tablist" aria-label="Диапазон графика">
      {keys.map((key) => (
        <button
          key={key}
          type="button"
          role="tab"
          aria-selected={value === key}
          onClick={() => onChange(key)}
          className={cn(
            "px-2.5 py-1 font-display text-[11px] tracking-wider",
            "border-r border-bg-5 last:border-r-0 transition-colors",
            "focus-visible:outline-2 focus-visible:outline-offset-[-2px] focus-visible:outline-accent",
            value === key ? "bg-bg-4 text-accent" : "text-bg-9 hover:text-bg-11",
          )}
        >
          {RANGES[key].label}
        </button>
      ))}
    </div>
  );
}

function ChartMetaItem({ label, value }: { label: string; value: string }) {
  return (
    <span>
      <span className="text-bg-8 mr-1.5">{label}</span>
      <span className="text-bg-11 font-medium tabular-nums">{value}</span>
    </span>
  );
}

// ─── helpers ──────────────────────────────────────────────────────────────

/** Decimal-строка / null → number (0 при невалидном). */
function toNumber(value: string | null): number {
  if (value == null) return 0;
  const n = Number.parseFloat(value);
  return Number.isNaN(n) ? 0 : n;
}

/** Метка бакета на оси X. Hour → "14:00", Day → "05-21". */
function formatBucketLabel(iso: string, bucket: "hour" | "day"): string {
  const d = new Date(iso);
  if (Number.isNaN(d.getTime())) return "—";
  if (bucket === "hour") {
    return d.toLocaleTimeString("en-GB", { hour: "2-digit", minute: "2-digit", hour12: false });
  }
  // День: MM-DD (UTC, без локального сдвига).
  return d.toISOString().slice(5, 10);
}

/** Агрегаты для chart-meta футера. */
function computeSummary(points: ChartPoint[]): {
  total: number;
  avg: number;
  peak: number;
  leads: number;
} {
  if (points.length === 0) return { total: 0, avg: 0, peak: 0, leads: 0 };
  let total = 0;
  let peak = 0;
  let leads = 0;
  for (const p of points) {
    total += p.spend;
    leads += p.leads;
    if (p.spend > peak) peak = p.spend;
  }
  return { total, avg: total / points.length, peak, leads };
}

export type { RangeKey };
