import { useMemo } from "react";
import {
  CartesianGrid,
  Legend,
  Line,
  LineChart,
  ResponsiveContainer,
  Tooltip,
  XAxis,
  YAxis,
} from "recharts";
import type { AnalyticsLiveBudgetSeries } from "@fb/shared";

import { Skeleton } from "@/components/ui/Skeleton";
import { formatDisplayTime, resolveDisplayTimeZone } from "@/lib/timezone";

interface BudgetLineChartProps {
  data?: AnalyticsLiveBudgetSeries;
  loading?: boolean;
  height?: number;
}

export function BudgetLineChart({ data, loading = false, height = 260 }: BudgetLineChartProps) {
  const timeZone = resolveDisplayTimeZone();
  const points = useMemo(
    () =>
      (data?.points ?? []).map((point) => ({
        ts: new Date(point.ts).getTime(),
        actual: Number(point.actual),
        base: Number(point.base),
        stop: Number(point.stop),
      })),
    [data],
  );

  if (loading) return <Skeleton height={height} className="w-full" />;
  if (!points.length) {
    return (
      <div className="flex items-center justify-center text-[12px] text-bg-8" style={{ height }}>
        Линии появятся после первого скана текущих суток
      </div>
    );
  }

  return (
    <div className="w-full min-w-0 overflow-hidden" style={{ height }}>
      <ResponsiveContainer width="100%" height="100%">
        <LineChart data={points} margin={{ top: 8, right: 18, bottom: 4, left: 2 }}>
          <CartesianGrid vertical={false} stroke="var(--hairline)" />
          <XAxis
            dataKey="ts"
            type="number"
            scale="time"
            domain={["dataMin", "dataMax"]}
            padding={{ left: 12, right: 12 }}
            interval="preserveStartEnd"
            minTickGap={34}
            tickFormatter={(value) => formatDisplayTime(new Date(value), {}, timeZone)}
            tick={{ fill: "var(--color-bg-8)", fontSize: 10 }}
            axisLine={false}
            tickLine={false}
          />
          <YAxis
            width={44}
            tickFormatter={(value) => `$${Number(value).toFixed(0)}`}
            tick={{ fill: "var(--color-bg-8)", fontSize: 10 }}
            axisLine={false}
            tickLine={false}
          />
          <Tooltip
            labelFormatter={(value) => formatDisplayTime(new Date(Number(value)), {}, timeZone)}
            formatter={(value, name) => [`$${Number(value).toFixed(2)}`, String(name)]}
            contentStyle={{
              background: "var(--color-bg-1)",
              border: "1px solid var(--hairline-strong)",
              borderRadius: "var(--radius-2)",
              fontSize: 12,
            }}
          />
          <Legend iconType="plainline" wrapperStyle={{ fontSize: 11 }} />
          <Line
            name="Факт"
            dataKey="actual"
            stroke="var(--accent)"
            strokeWidth={2.2}
            dot={false}
            isAnimationActive={false}
          />
          <Line
            name="База"
            dataKey="base"
            stroke="var(--info)"
            strokeWidth={1.6}
            strokeDasharray="5 4"
            dot={false}
            isAnimationActive={false}
          />
          <Line
            name="Stop"
            dataKey="stop"
            stroke="var(--danger)"
            strokeWidth={1.6}
            dot={false}
            isAnimationActive={false}
          />
        </LineChart>
      </ResponsiveContainer>
    </div>
  );
}
