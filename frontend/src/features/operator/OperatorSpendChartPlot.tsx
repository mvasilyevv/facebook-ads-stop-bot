import {
  CartesianGrid,
  Line,
  LineChart,
  ReferenceLine,
  ResponsiveContainer,
  Tooltip,
  XAxis,
  YAxis,
} from "recharts";

import { formatZonedDateTime, formatZonedTime } from "@fb/shared/format/time";
import { formatSpend } from "@fb/shared/format/number";
import { currentMarkerLabelPosition, serverSeriesMarker } from "@fb/shared/operator/chartModel";
import type { OperatorSpendPoint } from "@fb/shared/operator/contracts";
import { decimalToNumber } from "@fb/shared/operator/viewModel";

interface OperatorSpendChartPlotProps {
  points: OperatorSpendPoint[];
  currency: string | null;
  timezone: string;
  generatedAt: string;
}

/** Recharts renderer kept behind a dynamic import on the operator start route. */
export function OperatorSpendChartPlot({
  points,
  currency,
  timezone,
  generatedAt,
}: OperatorSpendChartPlotProps) {
  const rows = points.map((point) => ({
    ...point,
    actualNumber: decimalToNumber(point.actual),
    baseNumber: decimalToNumber(point.base),
    stopNumber: decimalToNumber(point.stop),
  }));
  const currentMarker = serverSeriesMarker(
    rows.map((row) => row.at),
    generatedAt,
  );
  const currentMarkerLabelSide = currentMarker
    ? currentMarkerLabelPosition(
        rows.map((row) => row.at),
        currentMarker,
      )
    : null;

  return (
    <div className="h-[240px] w-full sm:h-[280px]">
      <ResponsiveContainer
        width="100%"
        height="100%"
        minWidth={0}
        minHeight={240}
        initialDimension={{ width: 320, height: 240 }}
      >
        <LineChart
          data={rows}
          margin={{ top: 12, right: 8, left: -12, bottom: 4 }}
          accessibilityLayer
        >
          <CartesianGrid stroke="rgba(255,255,255,.06)" vertical={false} />
          <XAxis
            dataKey="at"
            tickFormatter={(value) => formatZonedTime(String(value), timezone)}
            stroke="var(--color-bg-7)"
            tick={{ fill: "var(--color-bg-9)", fontSize: 12 }}
            minTickGap={32}
          />
          <YAxis
            tickFormatter={(value: number) => compactMoney(value, currency)}
            stroke="var(--color-bg-7)"
            tick={{ fill: "var(--color-bg-9)", fontSize: 12 }}
            width={58}
          />
          <Tooltip
            labelFormatter={(value) => formatDateTime(String(value), timezone)}
            formatter={(value, name) => [
              typeof value === "number" ? formatMoney(value, currency) : "—",
              name === "actualNumber" ? "Факт" : name === "baseNumber" ? "База" : "Stop",
            ]}
            contentStyle={{
              background: "var(--color-bg-2)",
              border: "1px solid var(--color-hairline-strong)",
              borderRadius: "10px",
              color: "var(--color-bg-11)",
              fontSize: 12,
            }}
          />
          <Line
            type="monotone"
            dataKey="stopNumber"
            stroke="var(--color-danger)"
            strokeDasharray="4 5"
            dot={false}
            strokeWidth={1.5}
            connectNulls={false}
            isAnimationActive={false}
          />
          <Line
            type="monotone"
            dataKey="baseNumber"
            stroke="var(--color-bg-9)"
            strokeDasharray="6 5"
            dot={false}
            strokeWidth={1.5}
            connectNulls={false}
            isAnimationActive={false}
          />
          <Line
            type="monotone"
            dataKey="actualNumber"
            stroke="var(--color-accent)"
            dot={{ r: 2.5, fill: "var(--color-accent)", strokeWidth: 0 }}
            activeDot={{ r: 4, fill: "var(--color-active)" }}
            strokeWidth={2.5}
            connectNulls={false}
            isAnimationActive={false}
          />
          {currentMarker ? (
            <ReferenceLine
              x={currentMarker}
              stroke="var(--color-active)"
              strokeDasharray="2 4"
              label={{
                value: "Сейчас",
                position: currentMarkerLabelSide ?? "insideTopLeft",
                fill: "var(--color-bg-9)",
                fontSize: 12,
                className: "operator-current-marker-label",
                textAnchor: currentMarkerLabelSide === "insideTopLeft" ? "end" : "start",
                dx: currentMarkerLabelSide === "insideTopLeft" ? -6 : 6,
              }}
            />
          ) : null}
        </LineChart>
      </ResponsiveContainer>
    </div>
  );
}

function formatMoney(value: number, currency: string | null): string {
  return formatSpend(value, currency);
}

function compactMoney(value: number, currency: string | null): string {
  return formatSpend(value, currency);
}

function formatDateTime(value: string, timezone: string): string {
  const formatted = formatZonedDateTime(value, timezone);
  return formatted === "—" ? "не подтверждено" : formatted;
}
