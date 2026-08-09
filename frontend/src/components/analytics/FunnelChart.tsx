import type { DataState } from "@fb/shared/operator/contracts";
import { buildAnalyticsFunnelModel } from "@fb/shared/analytics/chartModel";
import { AccessibleChartFrame } from "@fb/operator-ui";
import { Bar, BarChart, CartesianGrid, ResponsiveContainer, Tooltip, XAxis, YAxis } from "recharts";

interface FunnelChartProps {
  clicks: number | null;
  registrations: number | null;
  ftds: number | null;
  confirmedDeposits: number | null;
  spend: string | null;
  currency: string | null;
  timezone: string;
  asOf: string | null;
  completeness: DataState;
  sources: string[];
}

const count = new Intl.NumberFormat("ru-RU");

export function FunnelChart({
  clicks,
  registrations,
  ftds,
  confirmedDeposits,
  spend,
  currency,
  timezone,
  asOf,
  completeness,
  sources,
}: FunnelChartProps) {
  const valuesVisible = completeness !== "unavailable";
  const stages = buildAnalyticsFunnelModel(
    {
      spend: valuesVisible ? spend : null,
      clicks: valuesVisible ? clicks : null,
      registrations: valuesVisible ? registrations : null,
      ftds: valuesVisible ? ftds : null,
      confirmed_deposits: valuesVisible ? confirmedDeposits : null,
    },
    currency === "USD" ? "USD" : null,
  );
  const knownValues = stages.flatMap((stage) => (stage.count === null ? [] : [stage.count]));
  const summary = knownValues.length
    ? `Клики ${formatCount(clicks)}, регистрации ${formatCount(registrations)}, FTD ${formatCount(ftds)}, подтверждённые депозиты ${formatCount(confirmedDeposits)}.`
    : "Данные воронки не подтверждены источниками.";

  const chart = (
    <div className="grid gap-4">
      <div className="h-[220px] min-w-0">
        <ResponsiveContainer
          width="100%"
          height="100%"
          minWidth={0}
          minHeight={220}
          initialDimension={{ width: 420, height: 220 }}
        >
          <BarChart
            accessibilityLayer
            data={stages.map((stage) => ({ ...stage, plotValue: stage.count }))}
            layout="vertical"
            margin={{ top: 4, right: 12, bottom: 4, left: 0 }}
          >
            <CartesianGrid horizontal={false} stroke="var(--color-hairline)" />
            <XAxis type="number" hide domain={[0, "dataMax"]} />
            <YAxis
              type="category"
              dataKey="label"
              width={132}
              axisLine={false}
              tickLine={false}
              tick={{ fill: "var(--color-bg-9)", fontSize: 12 }}
            />
            <Tooltip
              formatter={(value) => [
                typeof value === "number" ? count.format(value) : "—",
                "Количество",
              ]}
              contentStyle={{
                background: "var(--color-bg-1)",
                border: "1px solid var(--color-hairline-strong)",
                borderRadius: "var(--radius-2)",
                fontSize: 14,
              }}
            />
            <Bar
              dataKey="plotValue"
              fill="var(--color-accent)"
              radius={[0, 3, 3, 0]}
              isAnimationActive={false}
            />
          </BarChart>
        </ResponsiveContainer>
      </div>
      <ol className="grid gap-2" aria-label="Этапы воронки">
        {stages.map((stage) => (
          <li
            key={stage.key}
            className="grid grid-cols-[minmax(110px,1fr)_auto] gap-x-3 border-t border-[var(--color-hairline)] pt-2 text-[14px]"
          >
            <span className="text-bg-9">{stage.label}</span>
            <strong className="font-display tabular-nums text-bg-11">
              {formatCount(stage.count)}
            </strong>
            <span className="col-span-2 text-right text-[12px] tabular-nums text-bg-8">
              CR {stage.conversion === null ? "—" : `${stage.conversion.toFixed(1)}%`} · стоимость{" "}
              {stage.cost}
            </span>
          </li>
        ))}
      </ol>
    </div>
  );

  const table = (
    <table className="w-full border-collapse text-left text-[14px]">
      <caption className="sr-only">Количество, конверсия и стоимость этапов воронки</caption>
      <thead>
        <tr>
          <th scope="col">Этап</th>
          <th scope="col">Количество</th>
          <th scope="col">CR от предыдущего</th>
          <th scope="col">Стоимость</th>
        </tr>
      </thead>
      <tbody>
        {stages.map((stage) => (
          <tr key={stage.key}>
            <th scope="row">{stage.label}</th>
            <td>{formatCount(stage.count)}</td>
            <td>{stage.conversion === null ? "—" : `${stage.conversion.toFixed(2)}%`}</td>
            <td>{stage.cost}</td>
          </tr>
        ))}
      </tbody>
    </table>
  );

  return (
    <AccessibleChartFrame
      title="Воронка"
      summary={summary}
      timezone={timezone}
      asOf={asOf}
      sources={sources}
      completeness={completeness}
      chart={chart}
      table={table}
    />
  );
}

function formatCount(value: number | null): string {
  return value === null ? "—" : count.format(value);
}
