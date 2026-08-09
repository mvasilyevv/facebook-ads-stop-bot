import type { AnalyticsDaypart, AnalyticsMetrics } from "../api/types";
import { formatSpendPerUnit } from "../format/number";
import { serverSeriesMarker } from "../operator/chartModel";

export interface SpendChartInputPoint {
  at: string;
  actual: string | null;
  base: string | null;
  stop: string | null;
}

export interface SpendChartPoint {
  at: string;
  timestamp: number;
  actual: number | null;
  base: number | null;
  stop: number | null;
}

export interface SpendChartModel {
  points: SpendChartPoint[];
  currentMarker: string | null;
  hasKnownValue: boolean;
  maximum: number;
}

/** Normalize decimal-string evidence without filling missing samples with zero. */
export function buildSpendChartModel(
  points: readonly SpendChartInputPoint[],
  currentAt: string | null,
): SpendChartModel {
  const normalized = points.map((point) => ({
    at: point.at,
    timestamp: Date.parse(point.at),
    actual: decimal(point.actual),
    base: decimal(point.base),
    stop: decimal(point.stop),
  }));
  const values = normalized
    .flatMap((point) => [point.actual, point.base, point.stop])
    .filter((value): value is number => value !== null);
  return {
    points: normalized,
    currentMarker: serverSeriesMarker(
      normalized.map((point) => point.at),
      currentAt,
    ),
    hasKnownValue: values.length > 0,
    maximum: Math.max(...values, 1),
  };
}

export type AnalyticsFunnelStageKey =
  | "clicks"
  | "registrations"
  | "ftd"
  | "confirmed_deposits";

export interface AnalyticsFunnelStageModel {
  key: AnalyticsFunnelStageKey;
  label: string;
  count: number | null;
  conversion: number | null;
  cost: string;
}

/** Shared funnel semantics; only confirmed USD may produce monetary costs. */
export function buildAnalyticsFunnelModel(
  totals: Pick<
    AnalyticsMetrics,
    "spend" | "clicks" | "registrations" | "ftds" | "confirmed_deposits"
  >,
  currency: string | null,
): AnalyticsFunnelStageModel[] {
  const trustedCurrency = currency === "USD" ? "USD" : null;
  const rows: Array<{
    key: AnalyticsFunnelStageKey;
    label: string;
    count: number | null;
  }> = [
    { key: "clicks", label: "Клики", count: totals.clicks },
    {
      key: "registrations",
      label: "Регистрации",
      count: totals.registrations,
    },
    { key: "ftd", label: "FTD", count: totals.ftds },
    {
      key: "confirmed_deposits",
      label: "Подтверждённые депозиты",
      count: totals.confirmed_deposits,
    },
  ];
  return rows.map((row, index) => {
    const previous = rows[index - 1]?.count ?? null;
    return {
      ...row,
      conversion:
        previous !== null && previous > 0 && row.count !== null
          ? (row.count / previous) * 100
          : null,
      cost: formatSpendPerUnit(totals.spend, row.count, trustedCurrency),
    };
  });
}

export type DaypartMetric = "clicks" | "registrations" | "ftds";

export const ANALYTICS_WEEKDAYS: ReadonlyArray<{
  id: number;
  short: string;
  label: string;
}> = [
  { id: 1, short: "Пн", label: "Понедельник" },
  { id: 2, short: "Вт", label: "Вторник" },
  { id: 3, short: "Ср", label: "Среда" },
  { id: 4, short: "Чт", label: "Четверг" },
  { id: 5, short: "Пт", label: "Пятница" },
  { id: 6, short: "Сб", label: "Суббота" },
  { id: 7, short: "Вс", label: "Воскресенье" },
];

export interface DaypartHourModel {
  hour: number;
  clicks: number | null;
  registrations: number | null;
  ftds: number | null;
  present: boolean;
}

/** Materialize sparse source data into the same explicit x24 model on all clients. */
export function selectedDayHours(
  cells: AnalyticsDaypart["cells"],
  weekday: number,
): DaypartHourModel[] {
  const selected = new Map(
    cells
      .filter((cell) => cell.weekday === weekday)
      .map((cell) => [cell.hour, cell]),
  );
  return Array.from({ length: 24 }, (_, hour) => {
    const cell = selected.get(hour);
    return {
      hour,
      clicks: cell?.clicks ?? null,
      registrations: cell?.registrations ?? null,
      ftds: cell?.ftds ?? null,
      present: cell !== undefined,
    };
  });
}

export function preferredWeekday(
  cells: AnalyticsDaypart["cells"],
  current = 1,
): number {
  if (cells.some((cell) => cell.weekday === current)) return current;
  return (
    ANALYTICS_WEEKDAYS.find((day) =>
      cells.some((cell) => cell.weekday === day.id),
    )?.id ?? 1
  );
}

export function daypartCellMap(cells: AnalyticsDaypart["cells"]) {
  return new Map(cells.map((cell) => [`${cell.weekday}:${cell.hour}`, cell]));
}

export function daypartMetricValue(
  cells: ReturnType<typeof daypartCellMap>,
  weekday: number,
  hour: number,
  metric: DaypartMetric,
): number | null {
  const value = cells.get(`${weekday}:${hour}`)?.[metric] ?? null;
  return value !== null && Number.isFinite(value) ? value : null;
}

function decimal(value: string | null): number | null {
  if (value === null) return null;
  const parsed = Number(value);
  return Number.isFinite(parsed) ? parsed : null;
}
