/**
 * Metric / MetricCell — подпись + измеренное значение.
 *
 * Значение — число, деньги или счётчик, поэтому всегда набирается
 * `font-numeric` (JetBrains Mono) с `tabular-nums`: цифры выравниваются по
 * колонкам и не «прыгают» при обновлении. Канон закреплён
 * packages/shared/src/tokens/monoDiscipline.test.ts.
 *
 * Два варианта покрывают оба реальных места использования:
 * - `MetricCell` — ячейка `<td>` строки таблицы (OperatorAdsTable);
 * - `Metric` — пара `<dt>/<dd>` внутри `<dl>` (карточка объявления).
 *
 * Раньше `Metric` в frontend-mini использовал `font-display` вместо
 * `font-numeric` — числа набирались другим шрифтом, чем на web.
 */

export interface MetricCellProps {
  value: string;
  className?: string;
}

export function MetricCell({ value, className }: MetricCellProps) {
  const classes = ["px-3 py-3 text-right font-numeric tabular-nums text-[14px] text-bg-11", className]
    .filter(Boolean)
    .join(" ");
  return <td className={classes}>{value}</td>;
}

export interface MetricProps {
  label: string;
  value: string;
  className?: string;
}

export function Metric({ label, value, className }: MetricProps) {
  return (
    <div className={className}>
      <dt className="text-[12px] text-bg-8">{label}</dt>
      <dd className="mt-1 font-numeric tabular-nums text-[14px] text-bg-11">{value}</dd>
    </div>
  );
}
