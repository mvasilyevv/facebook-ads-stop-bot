import { useState } from "react";
import { Link, useNavigate } from "@tanstack/react-router";
import {
  AlertTriangle,
  CheckCircle2,
  CircleHelp,
  RefreshCw,
  ShieldAlert,
} from "lucide-react";

import {
  ACTION_STATE_LABEL,
  decimalToNumber,
  severityForDataState,
  snapshotForRealtimeState,
  snapshotHeadline,
  snapshotOverviewState,
  workerStatusLabel,
} from "@fb/shared/operator/viewModel";
import {
  currentMarkerLabelPosition,
  serverSeriesMarker,
} from "@fb/shared/operator/chartModel";
import { useOperatorRealtimeStatus } from "@fb/operator-api";
import { formatSpend } from "@fb/shared/format/number";
import { formatZonedTime } from "@fb/shared/format/time";
import type { components } from "@fb/shared/api/generated";
import type {
  DataState,
  OperatorActionItem,
  OperatorEconomyData,
  OperatorSeverity,
  OperatorSpendPoint,
} from "@fb/shared/operator/contracts";
import {
  AccessibleChartFrame,
  DataStateBadge,
  OperatorSectionFrame,
} from "@fb/operator-ui";

import { Button } from "@/components/ui/Button";
import { haptic, tgAlert } from "@/lib/tg";
import {
  parseTmaAttentionHref,
  storeResolvedNavigation,
} from "@/lib/transientNavigation";
import {
  operatorProblemMessage,
  useOperatorScanNow,
  useOperatorSnapshot,
} from "@/lib/operatorApi";

const SEVERITY_VIEW: Record<
  OperatorSeverity,
  { Icon: typeof ShieldAlert; color: string }
> = {
  ok: { Icon: CheckCircle2, color: "var(--color-success)" },
  warning: { Icon: AlertTriangle, color: "var(--color-warning)" },
  critical: { Icon: ShieldAlert, color: "var(--color-danger)" },
  unknown: { Icon: CircleHelp, color: "var(--color-bg-9)" },
};

const SEVERITY_LABEL: Record<OperatorSeverity, string> = {
  ok: "Исправно",
  warning: "Внимание",
  critical: "Критично",
  unknown: "Не подтверждено",
};

export function OperatorMiniDashboard() {
  const navigate = useNavigate();
  const realtimeStatus = useOperatorRealtimeStatus();
  const snapshotQuery = useOperatorSnapshot({ window: "today" });
  const scan = useOperatorScanNow();
  const [scanReceipt, setScanReceipt] = useState<
    components["schemas"]["ScanNowResponse"] | null
  >(null);

  if (snapshotQuery.isLoading && !snapshotQuery.data) {
    return <MiniLoading />;
  }
  if (snapshotQuery.isError || !snapshotQuery.data) {
    return (
      <div
        role="alert"
        className="m-4 rounded-[var(--radius-3)] border border-danger/40 bg-danger-bg p-4"
      >
        <strong className="text-[16px] text-bg-11">Снимок недоступен</strong>
        <p className="mt-2 text-[14px] leading-5 text-bg-10">
          {operatorProblemMessage(snapshotQuery.error)}
        </p>
        <Button
          className="mt-4 min-h-11"
          onClick={() => void snapshotQuery.refetch()}
        >
          Повторить
        </Button>
      </div>
    );
  }

  const snapshot = snapshotForRealtimeState(
    snapshotQuery.data,
    realtimeStatus === "connected",
  );
  const headline = snapshotHeadline(snapshot);
  const timezoneDegraded = snapshot.meta.cabinet_timezone_state !== "single";
  const currencyUnknown = snapshot.meta.currency_state !== "single";
  const systemDisplayState = snapshotOverviewState(snapshot);
  const view = SEVERITY_VIEW[headline.severity];

  const runScan = async () => {
    haptic.impact("medium");
    try {
      const receipt = await scan.mutateAsync({});
      setScanReceipt(receipt);
      haptic.notify("warning");
      void snapshotQuery.refetch();
    } catch {
      haptic.notify("error");
    }
  };

  const openAttentionAction = async (href: string) => {
    const destination = parseTmaAttentionHref(href);
    if (!destination) {
      tgAlert("Ссылка действия недоступна.");
      return;
    }
    haptic.selection();
    if (destination.kind === "target") {
      storeResolvedNavigation(destination.target);
      await navigate({ to: "/open" });
      return;
    }
    await navigate({ to: destination.to });
  };

  return (
    <div className="flex min-w-0 flex-col gap-4 px-4 pb-5 pt-3">
      <header className="flex items-start justify-between gap-3">
        <div className="min-w-0">
          <div className="font-display text-[12px] uppercase tracking-[.08em] text-bg-8">
            Сейчас · {snapshot.meta.account.name ?? "кабинет"}
          </div>
          <h1 className="m-0 mt-2 font-display text-[30px] font-medium leading-[1.05] tracking-[-.03em] text-bg-11">
            Контроль
          </h1>
        </div>
        <button
          type="button"
          className="inline-flex size-11 shrink-0 items-center justify-center rounded-[var(--radius-2)] bg-accent text-bg-0 disabled:opacity-50 focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-accent"
          aria-label="Сканировать сейчас"
          disabled={scan.isPending}
          onClick={() => void runScan()}
        >
          <RefreshCw
            size={19}
            className={scan.isPending ? "animate-spin" : ""}
            aria-hidden="true"
          />
        </button>
      </header>

      {scanReceipt ? (
        <div
          role="status"
          aria-live="polite"
          className="flex min-h-11 flex-col gap-3 rounded-[var(--radius-3)] border border-warning/40 bg-warning-bg p-4 text-bg-11"
        >
          <div>
            <strong className="text-[14px]">
              Сканирование поставлено в очередь
            </strong>
            <div className="mt-1 font-display text-[12px] text-bg-9">
              Задача #{scanReceipt.task_id}
            </div>
          </div>
          <Link
            to="/actions/$actionId"
            params={{ actionId: String(scanReceipt.task_id) }}
            className="inline-flex min-h-11 items-center justify-center rounded-[var(--radius-2)] border border-warning/50 px-4 text-[14px] font-semibold text-bg-11 focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-accent"
          >
            Открыть выполнение
          </Link>
        </div>
      ) : null}

      {timezoneDegraded ? (
        <div
          role="status"
          className="flex min-h-11 items-start gap-3 rounded-[var(--radius-3)] border border-warning/40 bg-warning-bg p-4 text-[14px] leading-5 text-bg-11"
        >
          <AlertTriangle
            className="mt-0.5 shrink-0 text-warning"
            size={18}
            aria-hidden="true"
          />
          <span>
            {snapshot.meta.cabinet_timezone_state === "mixed"
              ? "В выборке несколько часовых поясов; границы суток рассчитаны отдельно по кабинетам."
              : "Часовой пояс кабинета неизвестен; границы суток оценочные."}
          </span>
        </div>
      ) : null}
      {currencyUnknown ? (
        <div
          role="status"
          className="flex min-h-11 items-start gap-3 rounded-[var(--radius-3)] border border-warning/40 bg-warning-bg p-4 text-[14px] leading-5 text-bg-11"
        >
          <AlertTriangle
            className="mt-0.5 shrink-0 text-warning"
            size={18}
            aria-hidden="true"
          />
          <span>
            {snapshot.meta.currency_state === "mixed"
              ? "В выборке несколько валют; денежные значения скрыты."
              : "Валюта кабинета не подтверждена; денежные значения скрыты."}
          </span>
        </div>
      ) : null}

      <section
        className="rounded-[var(--radius-3)] border border-[var(--color-hairline-strong)] bg-bg-1 p-4"
        aria-label="Сводное состояние"
      >
        <div className="flex items-start gap-3">
          <span
            className="flex size-10 shrink-0 items-center justify-center rounded-[var(--radius-2)] bg-bg-2"
            style={{ color: view.color }}
          >
            <view.Icon size={20} aria-hidden="true" />
          </span>
          <div className="min-w-0 flex-1">
            <DataStateBadge state={systemDisplayState} compact />
            <h2 className="m-0 mt-3 text-[18px] leading-6 text-bg-11">
              {headline.title}
            </h2>
            <p className="mt-1 text-[14px] leading-5 text-bg-9">
              {headline.detail}
            </p>
          </div>
        </div>
        {snapshot.system.data ? (
          <ul className="mt-4 grid grid-cols-2 gap-2" aria-label="Источники">
            {snapshot.system.data.workers.slice(0, 4).map((worker) => (
              <li
                key={worker.id}
                className="min-w-0 rounded-[var(--radius-2)] bg-bg-2 p-3"
              >
                <div className="flex items-center gap-2">
                  <span
                    className="size-2 shrink-0 rounded-full"
                    style={{
                      background:
                        SEVERITY_VIEW[
                          severityForDataState(
                            worker.severity,
                            snapshot.system.state,
                          )
                        ].color,
                    }}
                    aria-hidden="true"
                    data-severity={severityForDataState(
                      worker.severity,
                      snapshot.system.state,
                    )}
                  />
                  <strong className="truncate text-[14px] text-bg-11">
                    {worker.label}
                  </strong>
                </div>
                <div className="mt-1 truncate text-[12px] text-bg-9">
                  {snapshot.system.state === "stale" ||
                  snapshot.system.state === "unavailable"
                    ? "Состояние не подтверждено"
                    : workerStatusLabel(worker.status)}
                </div>
              </li>
            ))}
          </ul>
        ) : null}
      </section>

      <OperatorSectionFrame
        section={snapshot.attention}
        title="Требует внимания"
        description="Критичные риски и решения."
        empty={<MiniEmpty text="Активных сигналов нет." />}
      >
        {(attention) => (
          <ol className="mt-3 divide-y divide-[var(--color-hairline)]">
            {attention.items.slice(0, 4).map((item) => (
              <li key={item.id} className="py-4 first:pt-2">
                <div className="flex items-start gap-3">
                  <span
                    className="mt-0.5 flex size-8 shrink-0 items-center justify-center rounded-[var(--radius-2)] bg-bg-2"
                    style={{ color: SEVERITY_VIEW[item.severity].color }}
                    role="img"
                    aria-label={SEVERITY_LABEL[item.severity]}
                  >
                    {(() => {
                      const SeverityIcon = SEVERITY_VIEW[item.severity].Icon;
                      return <SeverityIcon size={16} aria-hidden="true" />;
                    })()}
                  </span>
                  <div className="min-w-0 flex-1">
                    <span className="font-display text-[12px] uppercase tracking-[.06em] text-bg-8">
                      {SEVERITY_LABEL[item.severity]}
                    </span>
                    <strong className="text-[14px] text-bg-11">
                      {item.title}
                    </strong>
                    <p className="mt-1 text-[14px] leading-5 text-bg-9">
                      {item.summary}
                    </p>
                    {item.action ? (
                      <button
                        type="button"
                        onClick={() =>
                          void openAttentionAction(item.action!.href)
                        }
                        className="mt-3 inline-flex min-h-11 items-center rounded-[var(--radius-2)] border border-[var(--color-hairline-strong)] px-3 text-[14px] font-semibold text-bg-11"
                      >
                        {item.action.label}
                      </button>
                    ) : null}
                  </div>
                </div>
              </li>
            ))}
          </ol>
        )}
      </OperatorSectionFrame>

      <OperatorSectionFrame
        section={snapshot.economy}
        title="Расход"
        description="Факт и границы суток кабинета."
        empty={<MiniEmpty text="Расхода в выбранном периоде нет." />}
      >
        {(economy) => (
          <MiniEconomy
            economy={economy}
            currency={snapshot.meta.currency ?? null}
            timezone={snapshot.meta.timezone}
            completeness={snapshot.economy.state}
            asOf={snapshot.economy.as_of}
            currentAt={snapshot.meta.generated_at}
            sources={snapshot.economy.sources}
          />
        )}
      </OperatorSectionFrame>

      <OperatorSectionFrame
        section={snapshot.funnel}
        title="Воронка"
        description="Meta → Tracker"
        empty={<MiniEmpty text="Событий воронки пока нет." />}
      >
        {(funnel) => (
          <ol
            className="mt-3 grid grid-cols-2 gap-2"
            aria-label="Этапы воронки"
          >
            {funnel.stages.map((stage) => (
              <li
                key={stage.key}
                className="rounded-[var(--radius-2)] bg-bg-2 p-3"
              >
                <div className="text-[12px] font-semibold text-bg-9">
                  {stage.label}
                </div>
                <div className="mt-1 font-display text-[22px] tabular-nums text-bg-11">
                  {stage.count === null
                    ? "—"
                    : stage.count.toLocaleString("ru-RU")}
                </div>
                <div className="mt-1 text-[12px] text-bg-9">
                  CR {stage.conversion === null ? "—" : `${stage.conversion}%`}
                </div>
                <div className="mt-1 text-[12px] text-bg-9">
                  Стоимость{" "}
                  {formatFunnelCost(stage.cost, snapshot.meta.currency ?? null)}
                </div>
              </li>
            ))}
          </ol>
        )}
      </OperatorSectionFrame>

      <OperatorSectionFrame
        section={snapshot.actions}
        title="Действия"
        description="Очередь и результат money-операций."
        action={
          <button
            type="button"
            className="min-h-11 rounded-[var(--radius-2)] px-2 text-[14px] font-semibold text-bg-10"
            onClick={() => void navigate({ to: "/actions" })}
          >
            Все
          </button>
        }
        empty={<MiniEmpty text="Активных действий нет." />}
      >
        {(actions) => <MiniActions items={actions.items.slice(0, 4)} />}
      </OperatorSectionFrame>
    </div>
  );
}

export function MiniActions({ items }: { items: OperatorActionItem[] }) {
  if (!items.length) return <MiniEmpty text="Активных действий нет." />;
  return (
    <ol className="mt-3 divide-y divide-[var(--color-hairline)]">
      {items.map((item) => (
        <li key={item.id} className="py-3">
          <div className="flex items-baseline justify-between gap-3">
            <Link
              to="/actions/$actionId"
              params={{ actionId: item.id }}
              className="inline-flex min-h-11 min-w-0 items-center truncate rounded-[var(--radius-2)] px-1 text-[14px] font-semibold text-bg-11 underline-offset-4 focus-visible:outline-2 focus-visible:outline-accent"
            >
              {item.title}
            </Link>
            <span className="shrink-0 font-display text-[12px] text-bg-9">
              {item.public_id}
            </span>
          </div>
          <p className="mt-1 text-[14px] text-bg-9">
            {ACTION_STATE_LABEL[item.state]} · {item.target_label ?? "система"}
          </p>
          {item.state === "unknown" ? (
            <p className="mt-1 text-[12px] text-warning">
              Результат проверяется, успех не подтверждён.
            </p>
          ) : null}
        </li>
      ))}
    </ol>
  );
}

function formatFunnelCost(
  value: string | null,
  currency: string | null,
): string {
  return formatSpend(value, currency);
}

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
  const valuesVisible = completeness !== "unavailable";
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
          valuesVisible
            ? spendSummary(series, currency, completeness)
            : "Значения расхода и порогов не подтверждены и скрыты."
        }
        timezone={timezone}
        asOf={asOf}
        sources={sources}
        completeness={completeness}
        chart={
          <MiniSpendPlot
            rows={series}
            currency={currency}
            timezone={timezone}
            currentAt={currentAt}
            state={completeness}
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

function MiniLoading() {
  return (
    <div
      role="status"
      aria-label="Загрузка операторского снимка"
      className="space-y-4 p-4"
    >
      {[90, 180, 320, 240].map((height, index) => (
        <div
          key={index}
          className="animate-pulse rounded-[var(--radius-3)] bg-bg-2 motion-reduce:animate-none"
          style={{ height }}
        />
      ))}
    </div>
  );
}
