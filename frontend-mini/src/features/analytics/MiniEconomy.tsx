import { useState } from "react";

import { AccessibleChartFrame } from "@fb/operator-ui";
import { formatSpend } from "@fb/shared/format/number";
import { formatZonedTime } from "@fb/shared/format/time";
import {
  currentMarkerLabelPosition,
  serverSeriesMarker,
} from "@fb/shared/operator/chartModel";
import type {
  DataState,
  OperatorEconomyData,
  OperatorSpendPoint,
} from "@fb/shared/operator/contracts";
import { decimalToNumber } from "@fb/shared/operator/viewModel";

export function MiniEconomy({
  economy,
  currency,
  timezone,
  completeness,
  asOf,
  currentAt,
  sources,
}: {
  economy: OperatorEconomyData;
  currency: string | null;
  timezone: string;
  completeness: DataState;
  asOf: string | null;
  currentAt: string | null;
  sources: string[];
}) {
  const usdConfirmed = currency === "USD";
  const valuesVisible = completeness !== "unavailable" && usdConfirmed;
  const displayCompleteness: DataState = usdConfirmed
    ? completeness
    : "unavailable";
  const totals = valuesVisible
    ? economy.totals
    : {
        spend: null,
        base: null,
        stop: null,
        base_delta: null,
      };
  const series = valuesVisible
    ? economy.series
    : economy.series.map((row) => ({
        ...row,
        actual: null,
        base: null,
        stop: null,
      }));
  return (
    <>
      <dl className="mt-4 grid grid-cols-2 gap-px overflow-hidden rounded-[var(--radius-2)] bg-[var(--color-hairline)]">
        <MiniMoney label="Факт" value={totals.spend} currency={currency} />
        <MiniMoney label="База" value={totals.base} currency={currency} />
        <MiniMoney label="Stop" value={totals.stop} currency={currency} />
        <MiniMoney
          label="Δ базы"
          value={totals.base_delta}
          currency={currency}
          signed
        />
      </dl>
      <AccessibleChartFrame
        title="Накопительный расход"
        summary={
          !usdConfirmed
            ? "Денежные значения скрыты: рабочая валюта не подтверждена как USD."
            : valuesVisible
              ? spendSummary(series, currency, completeness)
              : "Значения расхода и порогов не подтверждены и скрыты."
        }
        timezone={timezone}
        asOf={asOf}
        sources={sources}
        completeness={displayCompleteness}
        chart={
          <MiniSpendPlot
            rows={series}
            currency={currency}
            timezone={timezone}
            currentAt={currentAt}
            state={displayCompleteness}
          />
        }
        table={
          <MiniSpendTable
            rows={series}
            currency={currency}
            timezone={timezone}
          />
        }
      />
    </>
  );
}

function MiniSpendPlot({
  rows,
  currency,
  timezone,
  currentAt,
  state,
}: {
  rows: OperatorSpendPoint[];
  currency: string | null;
  timezone: string;
  currentAt: string | null;
  state: DataState;
}) {
  const [activeIndex, setActiveIndex] = useState<number | null>(null);
  const width = 360;
  const height = 140;
  const values = rows
    .flatMap((row) => [row.actual, row.base, row.stop].map(decimalToNumber))
    .filter((value): value is number => value !== null);
  if (!rows.length || !values.length)
    return <MiniEmpty text="Точки графика не подтверждены." />;
  const max = Math.max(...values, 1);
  const paths = (key: "actual" | "base" | "stop") =>
    makeSvgPaths(rows, key, width, height, max);
  const timestamps = rows.map((row) => row.at);
  const currentMarker = serverSeriesMarker(timestamps, currentAt);
  const currentMarkerIndex =
    currentMarker === null ? -1 : timestamps.indexOf(currentMarker);
  const currentMarkerLeft =
    currentMarkerIndex < 0
      ? null
      : currentMarkerIndex === rows.length - 1
        ? "calc(100% - 1px)"
        : currentMarkerIndex === 0
          ? "1px"
          : `${(currentMarkerIndex / (rows.length - 1)) * 100}%`;
  const currentMarkerLabelSide =
    currentMarker === null
      ? null
      : currentMarkerLabelPosition(timestamps, currentMarker);
  const stopLineClass =
    state === "ready"
      ? "border-danger"
      : state === "partial"
        ? "border-warning"
        : "border-bg-8";
  const stopStroke =
    state === "ready"
      ? "var(--color-danger)"
      : state === "partial"
        ? "var(--color-warning)"
        : "var(--color-bg-8)";
  const activeRow = activeIndex === null ? null : (rows[activeIndex] ?? null);
  return (
    <div>
      <ul
        className="mb-3 flex flex-wrap gap-x-4 gap-y-2 text-[12px] text-bg-9"
        aria-label="Обозначения графика"
      >
        <li className="inline-flex items-center gap-2">
          <span className="h-0.5 w-5 bg-accent" aria-hidden="true" />
          Факт
        </li>
        <li className="inline-flex items-center gap-2">
          <span
            className="w-5 border-t border-dashed border-bg-9"
            aria-hidden="true"
          />
          База
        </li>
        <li className="inline-flex items-center gap-2">
          <span
            className={`w-5 border-t border-dashed ${stopLineClass}`}
            aria-hidden="true"
          />
          Stop
        </li>
      </ul>
      <div className="relative">
        {currentMarkerLeft !== null ? (
          <div
            aria-hidden="true"
            data-current-time-marker
            className="pointer-events-none absolute inset-y-0 z-10 border-l border-dashed border-active"
            style={{ left: currentMarkerLeft }}
          >
            <span
              data-current-time-label
              className={`absolute top-1 whitespace-nowrap rounded-sm bg-bg-1/90 px-1 text-[12px] leading-5 text-bg-9 ${
                currentMarkerLabelSide === "insideTopLeft"
                  ? "right-1.5"
                  : "left-1.5"
              }`}
            >
              Сейчас
            </span>
          </div>
        ) : null}
        <svg
          viewBox={`0 0 ${width} ${height}`}
          className="h-auto w-full"
          preserveAspectRatio="none"
          aria-hidden="true"
          focusable="false"
        >
          {[35, 70, 105].map((y) => (
            <line
              key={y}
              x1="0"
              x2={width}
              y1={y}
              y2={y}
              stroke="rgba(255,255,255,.07)"
            />
          ))}
          {paths("stop").map((d) => (
            <path
              key={`stop-${d}`}
              d={d}
              fill="none"
              stroke={stopStroke}
              strokeWidth="1.5"
              strokeDasharray="4 5"
            />
          ))}
          {paths("base").map((d) => (
            <path
              key={`base-${d}`}
              d={d}
              fill="none"
              stroke="var(--color-bg-9)"
              strokeWidth="1.5"
              strokeDasharray="5 5"
            />
          ))}
          {paths("actual").map((d) => (
            <path
              key={`actual-${d}`}
              d={d}
              fill="none"
              stroke="var(--color-accent)"
              strokeWidth="2.5"
              vectorEffect="non-scaling-stroke"
            />
          ))}
        </svg>
        {rows.map((row, index) => {
          const rowValues = [row.actual, row.base, row.stop].map(
            decimalToNumber,
          );
          const actual = rowValues[0];
          const anchor = rowValues.find(
            (value): value is number => value !== null,
          );
          if (anchor === undefined) return null;
          const x =
            rows.length === 1 ? width / 2 : (index / (rows.length - 1)) * width;
          const y = height - 8 - (anchor / max) * (height - 16);
          const label = `${formatTime(row.at, timezone)}. Факт ${money(row.actual, currency)}, база ${money(row.base, currency)}, stop ${money(row.stop, currency)}.`;
          return (
            <button
              key={row.at}
              type="button"
              aria-label={label}
              className="absolute flex size-11 touch-manipulation items-center justify-center rounded-full border border-transparent bg-transparent focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-accent"
              style={{
                left: `clamp(22px, ${(x / width) * 100}%, calc(100% - 22px))`,
                top: `clamp(22px, ${(y / height) * 100}%, calc(100% - 22px))`,
                transform: "translate(-50%, -50%)",
              }}
              onFocus={() => setActiveIndex(index)}
              onPointerDown={() => setActiveIndex(index)}
              onPointerEnter={() => setActiveIndex(index)}
              onClick={() => setActiveIndex(index)}
            >
              {actual !== null ? (
                <span
                  aria-hidden="true"
                  data-actual-marker
                  className="size-2 rounded-full bg-accent"
                  style={{
                    boxShadow:
                      activeIndex === index
                        ? "0 0 0 3px var(--color-active)"
                        : undefined,
                  }}
                />
              ) : null}
            </button>
          );
        })}
      </div>
      <div
        role="status"
        aria-live="polite"
        className="mt-2 min-h-11 rounded-[var(--radius-2)] bg-bg-2 px-3 py-2 text-[12px] leading-5 text-bg-9"
      >
        {activeRow
          ? `${formatTime(activeRow.at, timezone)} · факт ${money(activeRow.actual, currency)} · база ${money(activeRow.base, currency)} · stop ${money(activeRow.stop, currency)}`
          : "Коснитесь точки или выберите её с клавиатуры, чтобы увидеть значения."}
      </div>
    </div>
  );
}

function spendSummary(
  rows: OperatorSpendPoint[],
  currency: string | null,
  state: DataState,
): string {
  const latest = [...rows]
    .reverse()
    .find((row) =>
      [row.actual, row.base, row.stop].some((value) => value !== null),
    );
  if (!latest) {
    return "Подтверждённых точек нет. Пропуски не заменяются нулём.";
  }
  const prefix =
    state === "ready"
      ? "Последние подтверждённые значения"
      : state === "partial"
        ? "Последние доступные значения из неполного снимка"
        : state === "stale"
          ? "Последние доступные значения из устаревшего снимка"
          : "Последние доступные значения";
  return `${prefix}: факт ${money(latest.actual, currency)}, база ${money(latest.base, currency)}, stop ${money(latest.stop, currency)}. Пропуски показаны разрывами.`;
}

function makeSvgPaths(
  rows: OperatorSpendPoint[],
  key: "actual" | "base" | "stop",
  width: number,
  height: number,
  max: number,
): string[] {
  const paths: string[] = [];
  let current: string[] = [];
  rows.forEach((row, index) => {
    const value = decimalToNumber(row[key]);
    if (value === null) {
      if (current.length > 1) paths.push(current.join(" "));
      current = [];
      return;
    }
    const x =
      rows.length === 1 ? width / 2 : (index / (rows.length - 1)) * width;
    const y = height - 8 - (value / max) * (height - 16);
    current.push(
      `${current.length ? "L" : "M"}${x.toFixed(1)},${y.toFixed(1)}`,
    );
  });
  if (current.length > 1) paths.push(current.join(" "));
  return paths;
}

function MiniSpendTable({
  rows,
  currency,
  timezone,
}: {
  rows: OperatorSpendPoint[];
  currency: string | null;
  timezone: string;
}) {
  return (
    <table>
      <caption className="sr-only">Расход по времени</caption>
      <thead>
        <tr>
          <th>Время</th>
          <th>Факт</th>
          <th>База</th>
          <th>Stop</th>
        </tr>
      </thead>
      <tbody>
        {rows.map((row) => (
          <tr key={row.at}>
            <th scope="row">{formatTime(row.at, timezone)}</th>
            <td>{money(row.actual, currency)}</td>
            <td>{money(row.base, currency)}</td>
            <td>{money(row.stop, currency)}</td>
          </tr>
        ))}
      </tbody>
    </table>
  );
}

function MiniMoney({
  label,
  value,
  currency,
  signed = false,
}: {
  label: string;
  value: string | null;
  currency: string | null;
  signed?: boolean;
}) {
  const parsed = decimalToNumber(value);
  const formatted = money(value, currency);
  const confirmed = parsed !== null && formatted !== "—";
  return (
    <div className="bg-bg-2 p-3">
      <dt className="text-[12px] font-semibold text-bg-9">{label}</dt>
      <dd className="m-0 mt-1 font-display text-[18px] tabular-nums text-bg-11">
        {!confirmed ? "—" : `${signed && parsed! > 0 ? "+" : ""}${formatted}`}
      </dd>
    </div>
  );
}

function money(value: string | null, currency: string | null): string {
  return formatSpend(value, currency);
}

function formatTime(value: string, timezone: string): string {
  return formatZonedTime(value, timezone);
}

function MiniEmpty({ text }: { text: string }) {
  return (
    <div className="mt-3 rounded-[var(--radius-2)] border border-dashed border-[var(--color-hairline-strong)] p-4 text-center text-[14px] text-bg-9">
      {text}
    </div>
  );
}
