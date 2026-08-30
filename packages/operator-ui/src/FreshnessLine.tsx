/**
 * FreshnessLine — «Часовой пояс / На: / Источник» одной строкой.
 *
 * Раньше формат жил только внутри AccessibleChartFrame и был недоступен
 * строкам вне графика (например, будущим карточкам с тем же freshness-triplet).
 * Извлечён без изменения формата — используется здесь же, внутри фрейма.
 */

import { formatZonedDateTime } from "@fb/shared/format/time";

export interface FreshnessLineProps {
  timezone: string;
  asOf: string | null;
  sources: string[];
  className?: string;
}

export function FreshnessLine({ timezone, asOf, sources, className }: FreshnessLineProps) {
  const classes = ["operator-chart-meta", className].filter(Boolean).join(" ");
  return (
    <div className={classes}>
      <span>Часовой пояс: {timezone}</span>
      <span>На: {formatTimestamp(asOf, timezone)}</span>
      <span>Источник: {sources.length ? sources.join(", ") : "не подтверждён"}</span>
    </div>
  );
}

function formatTimestamp(value: string | null, timezone: string): string {
  const formatted = formatZonedDateTime(value, timezone);
  return formatted === "—" ? "не подтверждено" : formatted;
}
