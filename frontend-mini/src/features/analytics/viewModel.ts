import type {
  AnalyticsDaypart,
} from "@fb/shared";

export type AnalyticsPeriod = "today" | "7d" | "30d";
export type DaypartMetric = "clicks" | "registrations" | "ftds";

export const ANALYTICS_PERIODS: ReadonlyArray<{
  id: AnalyticsPeriod;
  label: string;
}> = [
  { id: "today", label: "Сегодня" },
  { id: "7d", label: "7 дней" },
  { id: "30d", label: "30 дней" },
];

export const WEEKDAYS: ReadonlyArray<{
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

export function performanceWindow(
  period: AnalyticsPeriod,
): {
  period: AnalyticsPeriod;
} {
  return { period };
}

export interface DaypartHour {
  hour: number;
  clicks: number | null;
  registrations: number | null;
  ftds: number | null;
  present: boolean;
}

/** Materialize the sparse API result into an explicit 24-hour disclosure. */
export function selectedDayHours(
  cells: AnalyticsDaypart["cells"],
  weekday: number,
): DaypartHour[] {
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
    WEEKDAYS.find((day) =>
      cells.some((cell) => cell.weekday === day.id),
    )?.id ?? 1
  );
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
