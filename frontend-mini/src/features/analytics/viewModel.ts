import type { AnalyticsPeriod } from "@fb/shared/analytics/routeState";

export type { AnalyticsPeriod } from "@fb/shared/analytics/routeState";
export type { DaypartMetric } from "@fb/shared/analytics/chartModel";
export {
  ANALYTICS_WEEKDAYS as WEEKDAYS,
  preferredWeekday,
  selectedDayHours,
} from "@fb/shared/analytics/chartModel";

export const ANALYTICS_PERIODS: ReadonlyArray<{
  id: AnalyticsPeriod;
  label: string;
}> = [
  { id: "today", label: "Сегодня" },
  { id: "7d", label: "7 дней" },
  { id: "30d", label: "30 дней" },
  { id: "custom", label: "Период" },
];

export function performanceWindow(
  period: AnalyticsPeriod,
  fromDate?: string,
  toDate?: string,
): {
  period: AnalyticsPeriod;
  from_date?: string;
  to_date?: string;
} {
  return period === "custom"
    ? { period, from_date: fromDate, to_date: toDate }
    : { period };
}

export function sourceStatusLabel(status: string): string {
  if (status === "good") return "актуально";
  if (status === "degraded") return "частично";
  if (status === "missing") return "нет данных";
  return "не подтверждено";
}

export function formatFreshness(seconds: number | null | undefined): string {
  if (seconds == null || seconds < 0) return "не подтверждена";
  if (seconds < 60) return `${Math.round(seconds)} сек`;
  if (seconds < 3600) return `${Math.round(seconds / 60)} мин`;
  if (seconds < 86_400) return `${Math.round(seconds / 3600)} ч`;
  return `${Math.round(seconds / 86_400)} дн`;
}
