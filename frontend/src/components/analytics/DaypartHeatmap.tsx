import { useMemo, useState, type KeyboardEvent } from "react";

import type { AnalyticsDaypart } from "@fb/shared";
import type { DataState } from "@fb/shared/operator/contracts";
import { inheritAnalyticsState } from "@fb/shared/analytics/state";
import { AccessibleChartFrame } from "@fb/operator-ui";

type Metric = "clicks" | "registrations" | "ftds";

const DAYS = ["Пн", "Вт", "Ср", "Чт", "Пт", "Сб", "Вс"];
const LABELS: Record<Metric, string> = {
  clicks: "Клики",
  registrations: "Регистрации",
  ftds: "FTD",
};

export function DaypartHeatmap({
  data,
  parentState,
}: {
  data: AnalyticsDaypart;
  parentState: DataState;
}) {
  const [metric, setMetric] = useState<Metric>("registrations");
  const [selectedDay, setSelectedDay] = useState(1);
  const [activeCell, setActiveCell] = useState<string | null>(null);
  const [focusedCell, setFocusedCell] = useState("1:0");
  const completeness = inheritAnalyticsState(data.state, parentState);
  const visibleCells = useMemo(
    () => (completeness === "unavailable" ? [] : data.cells),
    [completeness, data.cells],
  );
  const map = useMemo(
    () => new Map(visibleCells.map((cell) => [`${cell.weekday}:${cell.hour}`, cell])),
    [visibleCells],
  );
  const values = visibleCells
    .map((cell) => cell[metric])
    .filter((value): value is number => value !== null && Number.isFinite(value));
  const max = Math.max(...values, 1);
  const peak = visibleCells
    .filter((cell) => cell[metric] !== null)
    .reduce<(typeof data.cells)[number] | null>((best, cell) => {
      if (best === null || Number(cell[metric]) > Number(best[metric])) return cell;
      return best;
    }, null);
  const evidenceSummary = peak
    ? `Пик: ${DAYS[peak.weekday - 1]} ${hourLabel(peak.hour)}, ${LABELS[metric].toLowerCase()} — ${Number(peak[metric]).toLocaleString("ru-RU")}.`
    : "Нет подтверждённых почасовых данных.";
  const summary =
    completeness === "ready" || !data.issues[0]
      ? evidenceSummary
      : `${evidenceSummary} Причина неполноты: ${data.issues[0]}.`;

  const chart = (
    <div>
      <div className="mb-4 flex flex-wrap items-center justify-between gap-3">
        <label className="flex min-h-11 items-center gap-2 text-[14px] text-bg-9 md:hidden">
          <span>День</span>
          <select
            value={selectedDay}
            onChange={(event) => {
              const day = Number(event.target.value);
              setSelectedDay(day);
              setFocusedCell(`${day}:0`);
              setActiveCell(null);
            }}
            className="min-h-11 rounded-[var(--radius-2)] border border-[var(--color-hairline-strong)] bg-bg-1 px-3 text-[14px] text-bg-11"
          >
            {DAYS.map((day, index) => (
              <option key={day} value={index + 1}>
                {day}
              </option>
            ))}
          </select>
        </label>
        <div className="flex gap-2 overflow-x-auto" role="group" aria-label="Метрика heatmap">
          {(Object.keys(LABELS) as Metric[]).map((key) => (
            <button
              key={key}
              type="button"
              aria-pressed={metric === key}
              onClick={() => {
                setMetric(key);
                setActiveCell(null);
              }}
              className={`min-h-11 shrink-0 rounded-[var(--radius-2)] border px-3 text-[14px] font-semibold ${
                metric === key
                  ? "border-accent bg-accent-bg text-accent"
                  : "border-[var(--color-hairline-strong)] text-bg-9"
              }`}
            >
              {LABELS[key]}
            </button>
          ))}
        </div>
      </div>

      <div
        className="grid grid-cols-3 gap-2 sm:grid-cols-4 md:hidden"
        data-daypart-grid
        role="region"
        aria-label={`${DAYS[selectedDay - 1]} по часам`}
      >
        {Array.from({ length: 24 }, (_, hour) => (
          <HeatCell
            key={hour}
            weekday={selectedDay}
            day={DAYS[selectedDay - 1] ?? "День"}
            hour={hour}
            value={cellValue(map, selectedDay, hour, metric)}
            metric={metric}
            max={max}
            onInspect={setActiveCell}
            focusedCell={focusedCell}
            onFocusCell={setFocusedCell}
          />
        ))}
      </div>

      <div
        className="hidden overflow-x-auto rounded-[var(--radius-2)] pb-2 md:block"
        data-daypart-grid
        role="region"
        aria-label="Визуальная карта по дням и часам"
      >
        <div className="grid min-w-[1110px] grid-cols-[42px_repeat(24,minmax(44px,1fr))] gap-1">
          <span />
          {Array.from({ length: 24 }, (_, hour) => (
            <span key={hour} className="text-center font-display text-[12px] text-bg-9">
              {hour.toString().padStart(2, "0")}
            </span>
          ))}
          {DAYS.map((day, index) => (
            <DayRow
              key={day}
              day={day}
              weekday={index + 1}
              metric={metric}
              max={max}
              cells={map}
              onInspect={setActiveCell}
              focusedCell={focusedCell}
              onFocusCell={setFocusedCell}
            />
          ))}
        </div>
      </div>
      <p
        id="daypart-cell-inspection"
        className="mt-3 min-h-5 text-[14px] text-bg-9"
        aria-live="polite"
      >
        {activeCell ?? summary}
      </p>
    </div>
  );

  const table = (
    <table className="w-full border-collapse text-left text-[14px]">
      <caption className="sr-only">Данные по дням недели и часам</caption>
      <thead>
        <tr>
          <th scope="col">День</th>
          <th scope="col">Час</th>
          <th scope="col">Клики</th>
          <th scope="col">Регистрации</th>
          <th scope="col">FTD</th>
        </tr>
      </thead>
      <tbody>
        {Array.from({ length: 7 * 24 }, (_, index) => {
          const weekday = Math.floor(index / 24) + 1;
          const hour = index % 24;
          const cell = map.get(`${weekday}:${hour}`);
          return (
            <tr key={`${weekday}:${hour}`}>
              <th scope="row">{DAYS[weekday - 1]}</th>
              <td>{hourLabel(hour)}</td>
              <td>{known(cell?.clicks ?? null)}</td>
              <td>{known(cell?.registrations ?? null)}</td>
              <td>{known(cell?.ftds ?? null)}</td>
            </tr>
          );
        })}
      </tbody>
    </table>
  );

  return (
    <AccessibleChartFrame
      title="Распределение по дням и часам"
      summary={summary}
      timezone={data.timezone}
      asOf={data.as_of}
      sources={daypartSourceLabels(data, completeness)}
      completeness={completeness}
      chart={chart}
      table={table}
    />
  );
}

function daypartSourceLabels(data: AnalyticsDaypart, state: DataState): string[] {
  if (state === "stale") return ["Meta (снимок устарел)", "AdSet.pro (снимок устарел)"];
  if (state === "unavailable") return ["Meta (не подтверждено)", "AdSet.pro (не подтверждено)"];
  if (state === "partial") return ["Meta (частично)", "AdSet.pro (частично)"];
  return [`Meta (${data.sources.meta.status})`, `AdSet.pro (${data.sources.tracker.status})`];
}

function DayRow({
  day,
  weekday,
  metric,
  max,
  cells,
  onInspect,
  focusedCell,
  onFocusCell,
}: {
  day: string;
  weekday: number;
  metric: Metric;
  max: number;
  cells: Map<string, AnalyticsDaypart["cells"][number]>;
  onInspect: (label: string) => void;
  focusedCell: string;
  onFocusCell: (key: string) => void;
}) {
  return (
    <>
      <span className="self-center text-[12px] text-bg-9">{day}</span>
      {Array.from({ length: 24 }, (_, hour) => (
        <HeatCell
          key={hour}
          weekday={weekday}
          day={day}
          hour={hour}
          value={cellValue(cells, weekday, hour, metric)}
          metric={metric}
          max={max}
          onInspect={onInspect}
          focusedCell={focusedCell}
          onFocusCell={onFocusCell}
          compact
        />
      ))}
    </>
  );
}

function HeatCell({
  day,
  weekday,
  hour,
  value,
  metric,
  max,
  onInspect,
  focusedCell,
  onFocusCell,
  compact = false,
}: {
  day: string;
  weekday: number;
  hour: number;
  value: number | null;
  metric: Metric;
  max: number;
  onInspect: (label: string) => void;
  focusedCell: string;
  onFocusCell: (key: string) => void;
  compact?: boolean;
}) {
  const label = `${day} ${hourLabel(hour)} · ${LABELS[metric]}: ${value === null ? "неизвестно" : value.toLocaleString("ru-RU")}`;
  const alpha = value === null ? 0.02 : value === 0 ? 0.06 : 0.15 + (value / max) * 0.75;
  const cellKey = `${weekday}:${hour}`;
  return (
    <button
      type="button"
      aria-label={label}
      aria-describedby="daypart-cell-inspection"
      title={label}
      data-weekday={weekday}
      data-hour={hour}
      tabIndex={focusedCell === cellKey ? 0 : -1}
      onPointerEnter={() => onInspect(label)}
      onFocus={() => {
        onFocusCell(cellKey);
        onInspect(label);
      }}
      onClick={() => onInspect(label)}
      onKeyDown={(event) => moveCellFocus(event, weekday, hour)}
      className={`flex min-h-11 min-w-11 flex-col items-center justify-center rounded-[4px] border border-white/[0.05] text-[12px] text-bg-10 ${compact ? "px-0" : "px-2"}`}
      style={{
        backgroundColor: value === null ? "var(--color-bg-2)" : `rgba(110, 207, 151, ${alpha})`,
      }}
    >
      {compact ? (
        <span aria-hidden="true">&nbsp;</span>
      ) : (
        <>
          <span className="block font-display">{hourLabel(hour)}</span>
          <span className="block">{value === null ? "—" : value}</span>
        </>
      )}
    </button>
  );
}

function moveCellFocus(
  event: KeyboardEvent<HTMLButtonElement>,
  weekday: number,
  hour: number,
): void {
  let nextWeekday = weekday;
  let nextHour = hour;
  if (event.key === "ArrowLeft") nextHour = Math.max(0, hour - 1);
  else if (event.key === "ArrowRight") nextHour = Math.min(23, hour + 1);
  else if (event.key === "ArrowUp") nextWeekday = Math.max(1, weekday - 1);
  else if (event.key === "ArrowDown") nextWeekday = Math.min(7, weekday + 1);
  else if (event.key === "Home") nextHour = 0;
  else if (event.key === "End") nextHour = 23;
  else return;

  const grid = event.currentTarget.closest<HTMLElement>("[data-daypart-grid]");
  const target = grid?.querySelector<HTMLButtonElement>(
    `button[data-weekday="${nextWeekday}"][data-hour="${nextHour}"]`,
  );
  if (!target || target === event.currentTarget) return;
  event.preventDefault();
  target.focus();
}

function cellValue(
  cells: Map<string, AnalyticsDaypart["cells"][number]>,
  weekday: number,
  hour: number,
  metric: Metric,
): number | null {
  const cell = cells.get(`${weekday}:${hour}`);
  if (!cell) return null;
  const value = cell[metric];
  return value !== null && Number.isFinite(value) ? value : null;
}

function hourLabel(hour: number): string {
  return `${hour.toString().padStart(2, "0")}:00`;
}

function known(value: number | null): string {
  return value === null ? "—" : value.toLocaleString("ru-RU");
}
