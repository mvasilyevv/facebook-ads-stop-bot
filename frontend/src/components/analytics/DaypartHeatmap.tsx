import { useState } from "react";
import type { AnalyticsDaypart } from "@fb/shared";

type Metric = "clicks" | "registrations" | "ftds";

const DAYS = ["Пн", "Вт", "Ср", "Чт", "Пт", "Сб", "Вс"];
const LABELS: Record<Metric, string> = {
  clicks: "Clicks",
  registrations: "Регистрации",
  ftds: "FTD",
};

export function DaypartHeatmap({ data }: { data?: AnalyticsDaypart }) {
  const [metric, setMetric] = useState<Metric>("registrations");
  const values = data?.cells.map((cell) => Number(cell[metric])) ?? [];
  const max = Math.max(...values, 1);
  const map = new Map(data?.cells.map((cell) => [`${cell.weekday}:${cell.hour}`, cell]));

  return (
    <div>
      <div className="mb-4 flex flex-wrap items-center justify-between gap-3">
        <span className="text-[11px] text-bg-8">{data?.timezone ?? "—"} · день × час</span>
        <div className="flex rounded-[var(--radius-2)] border border-[var(--hairline)] p-0.5">
          {(Object.keys(LABELS) as Metric[]).map((key) => (
            <button
              key={key}
              type="button"
              onClick={() => setMetric(key)}
              className={`rounded-[var(--radius-1)] px-2.5 py-1 text-[10px] ${
                metric === key ? "bg-bg-3 text-bg-11" : "text-bg-8 hover:text-bg-11"
              }`}
            >
              {LABELS[key]}
            </button>
          ))}
        </div>
      </div>

      <div className="overflow-x-auto pb-2">
        <div className="grid min-w-[760px] grid-cols-[34px_repeat(24,minmax(24px,1fr))] gap-1">
          <span />
          {Array.from({ length: 24 }, (_, hour) => (
            <span key={hour} className="text-center font-display text-[9px] text-bg-7">
              {hour.toString().padStart(2, "0")}
            </span>
          ))}
          {DAYS.map((day, dayIndex) => (
            <DayRow
              key={day}
              day={day}
              weekday={dayIndex + 1}
              metric={metric}
              max={max}
              cells={map}
            />
          ))}
        </div>
      </div>
    </div>
  );
}

function DayRow({
  day,
  weekday,
  metric,
  max,
  cells,
}: {
  day: string;
  weekday: number;
  metric: Metric;
  max: number;
  cells: Map<string, NonNullable<AnalyticsDaypart["cells"]>[number]>;
}) {
  return (
    <>
      <span className="self-center text-[10px] text-bg-8">{day}</span>
      {Array.from({ length: 24 }, (_, hour) => {
        const value = Number(cells.get(`${weekday}:${hour}`)?.[metric] ?? 0);
        const alpha = value === 0 ? 0.04 : 0.15 + (value / max) * 0.75;
        return (
          <div
            key={hour}
            title={`${day} ${hour.toString().padStart(2, "0")}:00 · ${LABELS[metric]}: ${value}`}
            className="aspect-square min-h-6 rounded-[3px] border border-white/[0.03]"
            style={{ backgroundColor: `rgba(110, 207, 151, ${alpha})` }}
          />
        );
      })}
    </>
  );
}
