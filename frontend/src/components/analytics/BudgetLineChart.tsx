import { useMemo } from "react";
import {
  CartesianGrid,
  Legend,
  Line,
  LineChart,
  ReferenceLine,
  ResponsiveContainer,
  Tooltip,
  XAxis,
  YAxis,
} from "recharts";

import type { AnalyticsLiveBudgetSeries } from "@fb/shared";
import { formatSpend } from "@fb/shared/format/number";
import { currentMarkerLabelPosition } from "@fb/shared/operator/chartModel";
import type { DataState } from "@fb/shared/operator/contracts";
import { inheritAnalyticsState } from "@fb/shared/analytics/state";
import { AccessibleChartFrame } from "@fb/operator-ui";

import { Skeleton } from "@/components/ui/Skeleton";
import { formatDisplayTime } from "@/lib/timezone";

interface BudgetLineChartProps {
  data?: AnalyticsLiveBudgetSeries;
  loading?: boolean;
  height?: number;
  timezone: string;
  parentState: DataState;
}

export function BudgetLineChart({
  data,
  loading = false,
  height = 260,
  timezone,
  parentState,
}: BudgetLineChartProps) {
  const points = useMemo(
    () =>
      (data?.points ?? []).map((point) => {
        return {
          ts: new Date(point.ts).getTime(),
          iso: point.ts,
          // Actual is emitted only when Meta has a latest ad snapshot. Budget
          // availability controls base/stop independently and must not erase spend.
          actual: decimal(point.actual),
          base: decimal(point.base),
          stop: decimal(point.stop),
          available: point.available_ads,
          unavailable: point.unavailable_ads,
        };
      }),
    [data],
  );
  const usdConfirmed = data?.scope.currency_state === "single" && data.scope.currency === "USD";
  const currency = usdConfirmed ? "USD" : null;

  if (loading) return <Skeleton height={height + 150} className="w-full" />;

  const hasKnownValue = points.some(
    (point) => point.actual !== null || point.base !== null || point.stop !== null,
  );
  const partial = points.some((point) => point.available === 0 || point.unavailable > 0);
  const serverState: DataState = data?.state ?? (!points.length ? "empty" : "unavailable");
  const localState: DataState = serverState === "ready" && partial ? "partial" : serverState;
  const completeness = inheritAnalyticsState(localState, parentState);
  const valuesAvailable = completeness !== "unavailable" && usdConfirmed;
  const stopColor =
    completeness === "ready"
      ? "var(--color-danger)"
      : completeness === "partial"
        ? "var(--color-warning)"
        : "var(--color-bg-8)";
  const latest = [...points].reverse().find((point) => point.actual !== null);
  const currentMarker = serverTimeMarker(
    points.map((point) => point.ts),
    data?.as_of ?? null,
  );
  const currentMarkerLabel =
    currentMarker === null
      ? "insideTopLeft"
      : currentMarkerLabelPosition(
          points.map((point) => point.iso),
          new Date(currentMarker).toISOString(),
        );
  const evidenceSummary = !usdConfirmed
    ? "Денежные ряды скрыты: рабочая валюта не подтверждена как USD."
    : completeness === "unavailable"
      ? "Точки расхода, базы и stop-границы не подтверждены и скрыты."
      : !points.length
        ? "Линии появятся после первого подтверждённого скана текущих суток."
        : !hasKnownValue
          ? "Источники не подтвердили ни одной точки расхода, базы или stop-границы."
          : completeness === "ready"
            ? `Последний подтверждённый расход ${money(latest?.actual, currency)}. Все точки получены без пропусков источников.`
            : completeness === "partial"
              ? `Последний доступный расход из неполного снимка ${money(latest?.actual, currency)}. Отсутствующие источники показаны разрывами.`
              : `Последний доступный расход из устаревшего снимка ${money(latest?.actual, currency)}. Значения не считаются текущими.`;
  const summary =
    completeness === "ready" || !data?.issues[0]
      ? evidenceSummary
      : `${evidenceSummary} Причина: ${data.issues[0]}.`;

  const chart =
    valuesAvailable && points.length && hasKnownValue ? (
      <div className="w-full min-w-0 overflow-hidden" style={{ height }}>
        <ResponsiveContainer
          width="100%"
          height="100%"
          minWidth={0}
          minHeight={240}
          initialDimension={{ width: 640, height: 320 }}
        >
          <LineChart
            accessibilityLayer
            data={points}
            margin={{ top: 8, right: 18, bottom: 4, left: 2 }}
          >
            <CartesianGrid vertical={false} stroke="var(--color-hairline)" />
            <XAxis
              dataKey="ts"
              type="number"
              scale="time"
              domain={["dataMin", "dataMax"]}
              padding={{ left: 12, right: 12 }}
              interval="preserveStartEnd"
              minTickGap={34}
              tickFormatter={(value) => formatDisplayTime(new Date(value), {}, timezone)}
              tick={{ fill: "var(--color-bg-8)", fontSize: 12 }}
              axisLine={false}
              tickLine={false}
            />
            <YAxis
              width={48}
              tickFormatter={(value) => money(Number(value), currency)}
              tick={{ fill: "var(--color-bg-8)", fontSize: 12 }}
              axisLine={false}
              tickLine={false}
            />
            <Tooltip
              labelFormatter={(value) => formatDisplayTime(new Date(Number(value)), {}, timezone)}
              formatter={(value, name) => [
                money(typeof value === "number" ? value : null, currency),
                String(name),
              ]}
              contentStyle={{
                background: "var(--color-bg-1)",
                border: "1px solid var(--color-hairline-strong)",
                borderRadius: "var(--radius-2)",
                fontSize: 14,
              }}
            />
            <Legend iconType="plainline" wrapperStyle={{ fontSize: 12 }} />
            {currentMarker !== null ? (
              <ReferenceLine
                x={currentMarker}
                stroke="var(--color-bg-7)"
                strokeDasharray="2 4"
                label={{
                  value: "Сейчас",
                  position: currentMarkerLabel,
                  fill: "var(--color-bg-8)",
                  fontSize: 12,
                }}
              />
            ) : null}
            <Line
              name="Факт"
              dataKey="actual"
              connectNulls={false}
              stroke="var(--color-accent)"
              strokeWidth={2.2}
              dot={{ r: 2.5, fill: "var(--color-accent)", strokeWidth: 0 }}
              activeDot={{ r: 4, fill: "var(--color-accent)", strokeWidth: 0 }}
              isAnimationActive={false}
            />
            <Line
              name="База"
              dataKey="base"
              connectNulls={false}
              stroke="var(--color-info)"
              strokeWidth={1.6}
              strokeDasharray="5 4"
              dot={false}
              isAnimationActive={false}
            />
            <Line
              name="Stop"
              dataKey="stop"
              connectNulls={false}
              stroke={stopColor}
              strokeWidth={1.6}
              strokeDasharray="5 4"
              dot={false}
              isAnimationActive={false}
            />
          </LineChart>
        </ResponsiveContainer>
      </div>
    ) : (
      <div className="flex items-center justify-center text-[14px] text-bg-8" style={{ height }}>
        Нет подтверждённых точек
      </div>
    );

  const table = (
    <table className="w-full border-collapse text-left text-[14px]">
      <caption className="sr-only">Почасовой расход, база, stop и доступность источников</caption>
      <thead>
        <tr>
          <th scope="col">Время</th>
          <th scope="col">Факт</th>
          <th scope="col">База</th>
          <th scope="col">Stop</th>
          <th scope="col">Источники</th>
        </tr>
      </thead>
      <tbody>
        {points.map((point) => (
          <tr key={point.iso}>
            <th scope="row">{formatDisplayTime(new Date(point.ts), {}, timezone)}</th>
            <td>{valuesAvailable ? money(point.actual, currency) : "—"}</td>
            <td>{valuesAvailable ? money(point.base, currency) : "—"}</td>
            <td>{valuesAvailable ? money(point.stop, currency) : "—"}</td>
            <td>
              {valuesAvailable
                ? `${point.available} доступно · ${point.unavailable} недоступно`
                : "не подтверждено"}
            </td>
          </tr>
        ))}
      </tbody>
    </table>
  );

  return (
    <AccessibleChartFrame
      title="Расход, база и stop-граница"
      summary={summary}
      timezone={timezone}
      asOf={data?.as_of ?? null}
      sources={sourceLabels(data, completeness)}
      completeness={completeness}
      chart={chart}
      table={table}
    />
  );
}

export function serverTimeMarker(points: number[], asOf: string | null): number | null {
  const serverTimestamp = asOf ? Date.parse(asOf) : Number.NaN;
  const valid = points.filter(Number.isFinite).sort((left, right) => left - right);
  if (!valid.length || !Number.isFinite(serverTimestamp)) return null;
  const earliest = valid[0]!;
  const latest = valid[valid.length - 1]!;
  return Math.max(earliest, Math.min(latest, serverTimestamp));
}

function decimal(value: string | null): number | null {
  if (value === null) return null;
  const parsed = Number(value);
  return Number.isFinite(parsed) ? parsed : null;
}

function sourceLabels(data: AnalyticsLiveBudgetSeries | undefined, state: DataState): string[] {
  if (!data) return [];
  if (state === "stale") return ["Meta (снимок устарел)", "AdSet.pro (снимок устарел)"];
  if (state === "unavailable") return ["Meta (не подтверждено)", "AdSet.pro (не подтверждено)"];
  if (state === "partial") return ["Meta (частично)", "AdSet.pro (частично)"];
  return [`Meta (${data.sources.meta.status})`, `AdSet.pro (${data.sources.tracker.status})`];
}

function money(value: number | null | undefined, currency: string | null): string {
  return formatSpend(value, currency);
}
