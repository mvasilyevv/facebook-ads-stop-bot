import { useState } from "react";
import type {
  AnalyticsLiveBudgetSeries,
  AnalyticsPerformance,
} from "@fb/shared";
import {
  buildAnalyticsFunnelModel,
  type AnalyticsFunnelStageModel,
} from "@fb/shared/analytics/chartModel";
import type {
  DataState,
  OperatorEconomyData,
} from "@fb/shared/operator/contracts";
import { AccessibleChartFrame } from "@fb/operator-ui";

import { Card } from "@/components/ui";
import { MiniEconomy } from "@/features/analytics/MiniEconomy";

interface LiveBudgetChartProps {
  performance: AnalyticsPerformance;
  series: AnalyticsLiveBudgetSeries;
  completeness: DataState;
  timezone: string;
  currency: string | null;
}

export function LiveBudgetChart({
  performance,
  series,
  completeness,
  timezone,
  currency,
}: LiveBudgetChartProps) {
  const budget = performance.total_live_budget;
  const economy: OperatorEconomyData = {
    totals: {
      spend: performance.totals.spend,
      base: budget?.base_budget ?? null,
      stop: budget?.stop_budget ?? null,
      base_delta: budget?.base_delta ?? null,
    },
    series: series.points.map((point) => ({
      at: point.ts,
      actual: point.actual,
      base: point.base,
      stop: point.stop,
    })),
  };

  return (
    <Card padding="sm" data-state={completeness}>
      <MiniEconomy
        economy={economy}
        currency={currency}
        timezone={timezone}
        completeness={completeness}
        asOf={series.as_of}
        currentAt={series.as_of}
        sources={sourceLabels(completeness)}
      />
    </Card>
  );
}

interface FunnelSummaryProps {
  performance: AnalyticsPerformance;
  completeness: DataState;
  timezone: string;
  currency: string | null;
}

export function FunnelSummary({
  performance,
  completeness,
  timezone,
  currency,
}: FunnelSummaryProps) {
  const totals = performance.totals;
  const valuesVisible = completeness !== "unavailable";
  const stages = buildAnalyticsFunnelModel(
    {
      spend: valuesVisible ? totals.spend : null,
      clicks: valuesVisible ? totals.clicks : null,
      registrations: valuesVisible ? totals.registrations : null,
      ftds: valuesVisible ? totals.ftds : null,
      confirmed_deposits: valuesVisible ? totals.confirmed_deposits : null,
    },
    currency === "USD" ? "USD" : null,
  );
  const summary = valuesVisible
    ? stages
        .map((stage) => `${stage.label}: ${formatCount(stage.count)}`)
        .join(" · ")
    : "Значения воронки не подтверждены и скрыты.";

  return (
    <Card padding="sm" data-state={completeness}>
      <AccessibleChartFrame
        title="Воронка"
        summary={summary}
        timezone={timezone}
        asOf={performance.as_of}
        sources={sourceLabels(completeness)}
        completeness={completeness}
        chart={<MiniFunnelPlot stages={stages} valuesVisible={valuesVisible} />}
        table={
          <table>
            <caption className="sr-only">
              Количество, конверсия и стоимость этапов воронки
            </caption>
            <thead>
              <tr>
                <th scope="col">Этап</th>
                <th scope="col">Количество</th>
                <th scope="col">CR</th>
                <th scope="col">Стоимость</th>
              </tr>
            </thead>
            <tbody>
              {stages.map((stage) => (
                <tr key={stage.key}>
                  <th scope="row">{stage.label}</th>
                  <td>{valuesVisible ? formatCount(stage.count) : "—"}</td>
                  <td>
                    {valuesVisible && stage.conversion != null
                      ? `${stage.conversion.toFixed(2)}%`
                      : "—"}
                  </td>
                  <td>{valuesVisible ? stage.cost : "—"}</td>
                </tr>
              ))}
            </tbody>
          </table>
        }
      />
    </Card>
  );
}

function MiniFunnelPlot({
  stages,
  valuesVisible,
}: {
  stages: AnalyticsFunnelStageModel[];
  valuesVisible: boolean;
}) {
  const [selected, setSelected] = useState(0);
  const maximum = Math.max(
    ...stages.flatMap((stage) => (stage.count === null ? [] : [stage.count])),
    1,
  );
  const active = stages[selected] ?? null;
  return (
    <div className="grid gap-3">
      <svg
        viewBox="0 0 360 112"
        className="block h-auto w-full"
        role="img"
        aria-label="Сравнение этапов воронки по общей шкале"
      >
        {stages.map((stage, index) => {
          const y = 8 + index * 27;
          const width =
            stage.count === null
              ? 0
              : Math.max((stage.count / maximum) * 336, stage.count ? 4 : 2);
          return stage.count === null ? (
            <rect
              key={stage.key}
              x="12"
              y={y}
              width="336"
              height="16"
              rx="2"
              fill="transparent"
              stroke="var(--color-bg-8)"
              strokeDasharray="3 3"
            />
          ) : (
            <rect
              key={stage.key}
              x="12"
              y={y}
              width={width}
              height="16"
              rx="2"
              fill="var(--color-accent)"
              opacity={selected === index ? 1 : 0.72}
            />
          );
        })}
      </svg>
      <div
        role="status"
        aria-live="polite"
        className="min-h-11 rounded-[var(--radius-2)] border border-[var(--color-hairline-strong)] bg-bg-2 px-3 py-2 text-[12px] leading-5 text-bg-9"
      >
        {active
          ? `${active.label}: ${formatCount(active.count)} · CR ${active.conversion === null ? "—" : `${active.conversion.toFixed(2)}%`} · стоимость ${active.cost}`
          : "Выберите этап воронки."}
      </div>
      <ol className="grid gap-1" aria-label="Этапы воронки">
        {stages.map((stage, index) => (
          <li key={stage.key}>
            <button
              type="button"
              onFocus={() => setSelected(index)}
              onPointerDown={() => setSelected(index)}
              onClick={() => setSelected(index)}
              className="grid min-h-11 w-full grid-cols-[minmax(100px,1fr)_auto] items-center gap-x-3 rounded-[var(--radius-2)] px-2 text-left focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-accent"
            >
              <span className="text-[13px] text-bg-9">{stage.label}</span>
              <strong className="font-display text-[14px] tabular-nums text-bg-11">
                {valuesVisible ? formatCount(stage.count) : "—"}
              </strong>
              <span className="col-span-2 text-right text-[12px] tabular-nums text-bg-9">
                CR{" "}
                {valuesVisible && stage.conversion !== null
                  ? `${stage.conversion.toFixed(2)}%`
                  : "—"}{" "}
                · стоимость {valuesVisible ? stage.cost : "—"}
              </span>
            </button>
          </li>
        ))}
      </ol>
    </div>
  );
}

function sourceLabels(state: DataState): string[] {
  const suffix =
    state === "stale"
      ? "снимок устарел"
      : state === "unavailable"
        ? "не подтверждено"
        : state === "partial"
          ? "частично"
          : "актуально";
  return [`Meta (${suffix})`, `AdSet.pro (${suffix})`];
}

function formatCount(value: number | null): string {
  return value == null ? "—" : value.toLocaleString("ru-RU");
}
