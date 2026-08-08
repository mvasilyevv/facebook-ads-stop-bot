import type { DataState } from "@fb/shared/operator/contracts";
import { formatSpendPerUnit } from "@fb/shared/format/number";
import { AccessibleChartFrame } from "@fb/operator-ui";

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
  const stages = [
    { label: "Клики", value: clicks },
    { label: "Регистрации", value: registrations },
    { label: "FTD", value: ftds },
    { label: "Подтверждённые депозиты", value: confirmedDeposits },
  ].map((stage, index, values) => {
    const previous = values[index - 1]?.value ?? null;
    return {
      ...stage,
      cr:
        previous !== null && previous > 0 && stage.value !== null
          ? (stage.value / previous) * 100
          : null,
      cost: formatSpendPerUnit(spend, stage.value, currency),
    };
  });
  const knownValues = stages.flatMap((stage) => (stage.value === null ? [] : [stage.value]));
  const max = Math.max(...knownValues, 1);
  const summary = knownValues.length
    ? `Клики ${formatCount(clicks)}, регистрации ${formatCount(registrations)}, FTD ${formatCount(ftds)}, подтверждённые депозиты ${formatCount(confirmedDeposits)}.`
    : "Данные воронки не подтверждены источниками.";

  const chart = (
    <div className="flex flex-col gap-3" role="group" aria-label="Воронка Meta в Tracker">
      {stages.map((stage) => (
        <div
          key={stage.label}
          className="grid grid-cols-[minmax(100px,1fr)_minmax(70px,2fr)_minmax(56px,auto)] items-center gap-x-3 gap-y-1"
        >
          <span className="row-span-2 text-[14px] text-bg-9">{stage.label}</span>
          <div className="h-6 overflow-hidden rounded-[var(--radius-1)] bg-bg-2" aria-hidden="true">
            {stage.value !== null ? (
              <div
                className="h-full rounded-[var(--radius-1)] bg-accent transition-[width] duration-300 motion-reduce:transition-none"
                style={{ width: `${Math.max((stage.value / max) * 100, stage.value ? 2 : 0)}%` }}
              />
            ) : (
              <div className="h-full w-full bg-[repeating-linear-gradient(135deg,var(--color-bg-3),var(--color-bg-3)_4px,transparent_4px,transparent_8px)]" />
            )}
          </div>
          <span className="text-right font-display text-[14px] tabular-nums text-bg-11">
            {formatCount(stage.value)}
          </span>
          <span className="col-span-2 col-start-2 min-w-0 text-right text-[12px] leading-4 tabular-nums text-bg-8">
            CR {stage.cr === null ? "—" : `${stage.cr.toFixed(1)}%`} · стоимость {stage.cost}
          </span>
        </div>
      ))}
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
          <tr key={stage.label}>
            <th scope="row">{stage.label}</th>
            <td>{formatCount(stage.value)}</td>
            <td>{stage.cr === null ? "—" : `${stage.cr.toFixed(2)}%`}</td>
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
