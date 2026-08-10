import { useId, useState } from "react";
import type { AnalyticsDaypart } from "@fb/shared";
import { formatInt } from "@fb/shared/format/number";
import type { DataState } from "@fb/shared/operator/contracts";
import {
  DATA_STATE_DESCRIPTION,
  DATA_STATE_LABEL,
} from "@fb/shared/operator/viewModel";
import { AccessibleChartFrame, DataStateBadge } from "@fb/operator-ui";

import { cn } from "@/lib/cn";
import { haptic } from "@/lib/tg";

import {
  preferredWeekday,
  selectedDayHours,
  sourceStatusLabel,
  WEEKDAYS,
  type DaypartMetric,
} from "./viewModel";

const METRICS: ReadonlyArray<{
  id: DaypartMetric;
  label: string;
  short: string;
}> = [
  { id: "clicks", label: "Клики", short: "Клики" },
  { id: "registrations", label: "Регистрации", short: "Рег." },
  { id: "ftds", label: "FTD", short: "FTD" },
];

export function DaypartDayChart({
  data,
  state,
}: {
  data: AnalyticsDaypart;
  state: DataState;
}) {
  const [requestedWeekday, setRequestedWeekday] = useState(() =>
    preferredWeekday(data.cells),
  );
  const [metric, setMetric] = useState<DaypartMetric>("ftds");
  const weekday = requestedWeekday;
  const hours = selectedDayHours(
    state === "empty" || state === "unavailable" ? [] : data.cells,
    weekday,
  );
  const values = hours.map((hour) => hour[metric]);
  const knownValues = values.filter((value): value is number => value !== null);
  const unknownCount = 24 - knownValues.length;
  const maximum = Math.max(1, ...knownValues);
  const dayLabel =
    WEEKDAYS.find((day) => day.id === weekday)?.label ?? "Выбранный день";
  const metricLabel =
    METRICS.find((option) => option.id === metric)?.label ?? "FTD";

  return (
    <div className="grid gap-3">
      {state !== "ready" ? (
        <AnalyticsStateNotice
          state={state}
          issue={data.issues[0]}
          testId="daypart-state"
        />
      ) : null}

      <div
        className="flex gap-2 overflow-x-auto pb-1"
        role="group"
        aria-label="День недели"
      >
        {WEEKDAYS.map((day) => (
          <button
            key={day.id}
            type="button"
            aria-label={day.label}
            aria-pressed={day.id === weekday}
            onClick={() => {
              haptic.selection();
              setRequestedWeekday(day.id);
            }}
            className={cn(
              "min-h-11 min-w-11 shrink-0 rounded-[var(--radius-2)] border px-3 text-[13px] font-semibold",
              day.id === weekday
                ? "border-accent bg-accent text-bg-0"
                : "border-[var(--color-hairline-strong)] bg-bg-2 text-bg-9",
            )}
          >
            {day.short}
          </button>
        ))}
      </div>

      <div
        className="grid grid-cols-3 gap-2"
        role="group"
        aria-label="Метрика почасового графика"
      >
        {METRICS.map((option) => (
          <button
            key={option.id}
            type="button"
            aria-pressed={option.id === metric}
            onClick={() => {
              haptic.selection();
              setMetric(option.id);
            }}
            className={cn(
              "min-h-11 rounded-[var(--radius-2)] border px-2 text-[13px] font-semibold",
              option.id === metric
                ? "border-active/60 bg-active-bg text-active"
                : "border-[var(--color-hairline)] bg-bg-2 text-bg-9",
            )}
          >
            {option.short}
          </button>
        ))}
      </div>

      <AccessibleChartFrame
        title={`${dayLabel} · 24 часа`}
        summary={daypartSummary(state, metricLabel, 24 - unknownCount)}
        timezone={data.timezone}
        asOf={data.as_of}
        sources={[
          `Meta — ${effectiveSourceLabel(data.sources.meta.status, state)}`,
          `AdSet.pro — ${effectiveSourceLabel(data.sources.tracker.status, state)}`,
        ]}
        completeness={state}
        chart={
          state === "empty" || state === "unavailable" ? (
            <p className="m-0 py-8 text-center text-[14px] text-bg-9">
              Почасовые значения не подтверждены.
            </p>
          ) : (
            <HourlyBars
              values={values}
              maximum={maximum}
              metricLabel={metricLabel}
              state={state}
            />
          )
        }
        table={
          <table>
            <caption className="sr-only">
              Почасовые данные за {dayLabel.toLowerCase()}
            </caption>
            <thead>
              <tr>
                <th scope="col">Час</th>
                <th scope="col">Клики</th>
                <th scope="col">Рег.</th>
                <th scope="col">FTD</th>
              </tr>
            </thead>
            <tbody>
              {hours.map((hour) => (
                <tr key={hour.hour}>
                  <th scope="row">{hourLabel(hour.hour)}</th>
                  <td>{formatInt(hour.clicks)}</td>
                  <td>{formatInt(hour.registrations)}</td>
                  <td>{formatInt(hour.ftds)}</td>
                </tr>
              ))}
            </tbody>
          </table>
        }
      />
    </div>
  );
}

function effectiveSourceLabel(status: string, state: DataState): string {
  if (state === "ready") return sourceStatusLabel(status);
  if (state === "partial") return "снимок неполный";
  if (state === "stale") return "снимок устарел";
  if (state === "empty") return "пустой снимок";
  return "не подтверждено";
}

function HourlyBars({
  values,
  maximum,
  metricLabel,
  state,
}: {
  values: Array<number | null>;
  maximum: number;
  metricLabel: string;
  state: Extract<DataState, "ready" | "partial" | "stale">;
}) {
  const baseline = 116;
  const chartHeight = 92;
  const inspectorId = useId();
  const [selectedHour, setSelectedHour] = useState(0);
  const selectedValue = values[selectedHour] ?? null;
  const inspectionText = `${hourLabel(selectedHour)} · ${metricLabel}: ${
    selectedValue === null ? "неизвестно" : formatInt(selectedValue)
  }`;
  return (
    <div>
      <div
        role="status"
        aria-live="polite"
        data-testid="daypart-hour-inspection"
        className="mb-2 min-h-11 rounded-[var(--radius-2)] border border-[var(--color-hairline-strong)] bg-bg-2 px-3 py-2 text-[14px] font-semibold text-bg-11"
      >
        {inspectionText}
      </div>
      <svg
        viewBox="0 0 480 148"
        className="block h-auto w-full"
        role="img"
        aria-label={`${metricLabel} по часам. Пунктиром отмечены часы без подтверждённых данных. Для просмотра точки используйте выбор часа под графиком.`}
      >
        <line
          x1="8"
          x2="472"
          y1={baseline}
          y2={baseline}
          stroke="var(--color-hairline-strong)"
        />
        {values.map((value, hour) => {
          const x = 10 + hour * 19;
          if (value === null) {
            return (
              <rect
                key={hour}
                data-unknown-hour={hour}
                x={x}
                y={baseline - 14}
                width="11"
                height="14"
                rx="2"
                fill="transparent"
                stroke={
                  selectedHour === hour
                    ? "var(--color-accent)"
                    : "var(--color-bg-8)"
                }
                strokeWidth={selectedHour === hour ? 2 : 1}
                strokeDasharray="2 2"
                data-selected-hour={selectedHour === hour ? hour : undefined}
              />
            );
          }
          const height =
            value === 0 ? 2 : Math.max(4, (value / maximum) * chartHeight);
          return (
            <rect
              key={hour}
              data-known-hour={hour}
              x={x}
              y={baseline - height}
              width="11"
              height={height}
              rx="2"
              fill="var(--color-active)"
              stroke={
                selectedHour === hour ? "var(--color-accent)" : "transparent"
              }
              strokeWidth={selectedHour === hour ? 2 : 0}
              data-selected-hour={selectedHour === hour ? hour : undefined}
            />
          );
        })}
        {[0, 6, 12, 18, 23].map((hour) => (
          <text
            key={hour}
            x={15.5 + hour * 19}
            y="139"
            fill="var(--color-bg-8)"
            fontFamily="var(--font-display)"
            fontSize="12"
            textAnchor="middle"
          >
            {String(hour).padStart(2, "0")}
          </text>
        ))}
      </svg>
      <label
        htmlFor={inspectorId}
        className="mt-2 grid gap-1 text-[12px] text-bg-9"
      >
        <span className="flex items-center justify-between gap-3">
          <span>Час для просмотра</span>
          <span
            aria-hidden="true"
            className="font-display tabular-nums text-bg-11"
          >
            {hourLabel(selectedHour)}
          </span>
        </span>
        <input
          id={inspectorId}
          type="range"
          min="0"
          max="23"
          step="1"
          value={selectedHour}
          aria-valuetext={inspectionText}
          onChange={(event) =>
            setSelectedHour(Number(event.currentTarget.value))
          }
          className="min-h-11 w-full cursor-pointer accent-accent focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-accent"
        />
      </label>
      <div className="mt-1 flex flex-wrap gap-x-4 gap-y-1 text-[12px] text-bg-9">
        <span className="inline-flex items-center gap-2">
          <span className="h-2 w-3 rounded-sm bg-active" aria-hidden="true" />
          {state === "ready"
            ? "подтверждено"
            : state === "partial"
              ? "доступно · снимок неполный"
              : "доступно · снимок устарел"}
        </span>
        <span className="inline-flex items-center gap-2">
          <span
            className="h-2 w-3 rounded-sm border border-dashed border-bg-8"
            aria-hidden="true"
          />
          нет подтверждения
        </span>
      </div>
    </div>
  );
}

export function AnalyticsStateNotice({
  state,
  issue,
  testId,
}: {
  state: Exclude<DataState, "ready">;
  issue?: string;
  testId?: string;
}) {
  return (
    <div
      role={state === "unavailable" ? "alert" : "status"}
      data-state={state}
      data-testid={testId}
      className={cn(
        "rounded-[var(--radius-2)] border bg-bg-2 px-4 py-3 text-[14px]",
        state === "partial"
          ? "border-warning/30"
          : state === "unavailable"
            ? "border-danger/30"
            : "border-[var(--color-hairline-strong)]",
      )}
    >
      <div className="flex items-center justify-between gap-3">
        <strong className="text-bg-11">{DATA_STATE_LABEL[state]}</strong>
        <DataStateBadge state={state} compact />
      </div>
      <p className="m-0 mt-1 leading-5 text-bg-9">
        {issue || DATA_STATE_DESCRIPTION[state]}
      </p>
    </div>
  );
}

function hourLabel(hour: number): string {
  return `${String(hour).padStart(2, "0")}:00`;
}

function daypartSummary(
  state: DataState,
  metricLabel: string,
  availableHours: number,
): string {
  if (state === "ready") {
    return availableHours === 24
      ? `${metricLabel}: подтверждены все 24 часа.`
      : `${metricLabel}: подтверждено ${availableHours} из 24 часов; разрывы означают неизвестные значения.`;
  }
  if (state === "partial") {
    return `${metricLabel}: доступно ${availableHours} из 24 часов в неполном снимке; значения нельзя считать полными.`;
  }
  if (state === "empty") {
    return `${metricLabel}: сервер подтвердил пустое окно; значения не заменяются нулём.`;
  }
  if (state === "unavailable") {
    return `${metricLabel}: источник не подтвердил почасовые данные.`;
  }
  return `${metricLabel}: доступно ${availableHours} из 24 часов в устаревшем снимке; значения не считаются текущими.`;
}
