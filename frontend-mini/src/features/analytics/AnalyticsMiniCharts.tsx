import type {
  AnalyticsLiveBudgetSeries,
  AnalyticsPerformance,
} from "@fb/shared";
import { formatSpendPerUnit } from "@fb/shared/format/number";
import type {
  DataState,
  OperatorEconomyData,
} from "@fb/shared/operator/contracts";
import { AccessibleChartFrame } from "@fb/operator-ui";

import { Card } from "@/components/ui";
import { MiniEconomy } from "@/features/operator/OperatorMiniDashboard";

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
  const rawStages = [
    { key: "clicks" as const, label: "Клики", count: totals.clicks },
    {
      key: "registrations" as const,
      label: "Регистрации",
      count: totals.registrations,
    },
    { key: "ftd" as const, label: "FTD", count: totals.ftds },
    {
      key: "confirmed_deposits" as const,
      label: "Подтверждённые депозиты",
      count: totals.confirmed_deposits,
    },
  ];
  const stages = rawStages.map((stage, index) => {
    const previous = rawStages[index - 1]?.count ?? null;
    return {
      ...stage,
      conversion:
        previous != null && previous > 0 && stage.count != null
          ? ((stage.count / previous) * 100).toFixed(2)
          : null,
      cost: formatSpendPerUnit(totals.spend, stage.count, currency),
    };
  });
  const known = stages.flatMap((stage) =>
    stage.count == null ? [] : [stage.count],
  );
  const max = Math.max(...known, 1);
  const valuesVisible = completeness !== "unavailable";
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
        chart={
          <ol className="grid gap-3" aria-label="Этапы воронки">
            {stages.map((stage) => (
              <li
                key={stage.key}
                className="grid grid-cols-[minmax(96px,1fr)_minmax(72px,1.5fr)_auto] items-center gap-x-2 gap-y-1"
              >
                <span className="text-[13px] leading-5 text-bg-9">
                  {stage.label}
                </span>
                <span
                  className="h-5 overflow-hidden rounded-[var(--radius-1)] bg-bg-2"
                  aria-hidden="true"
                >
                  {valuesVisible && stage.count != null ? (
                    <span
                      className="block h-full rounded-[var(--radius-1)] bg-accent"
                      style={{
                        width: `${Math.max((stage.count / max) * 100, stage.count ? 2 : 0)}%`,
                      }}
                    />
                  ) : null}
                </span>
                <span className="min-w-11 text-right font-display text-[14px] tabular-nums text-bg-11">
                  {valuesVisible ? formatCount(stage.count) : "—"}
                </span>
                <span className="col-span-2 col-start-2 min-w-0 text-right text-[12px] leading-4 tabular-nums text-bg-9">
                  CR{" "}
                  {valuesVisible && stage.conversion != null
                    ? `${stage.conversion}%`
                    : "—"}{" "}
                  · стоимость {valuesVisible ? stage.cost : "—"}
                </span>
              </li>
            ))}
          </ol>
        }
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
                      ? `${stage.conversion}%`
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
