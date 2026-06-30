/**
 * AdMetricsPanel — секции «МЕТРИКИ · СНИМОК» + «CPL · 8 ТОЧЕК» в AdDrawer.
 * Выделено из AdDrawer.tsx (было >600 строк в одном файле — god-component).
 */
import { useMemo } from "react";
import type { components } from "@fb/shared/api/generated";

import { Skeleton } from "@/components/ui/Skeleton";
import { Sparkline } from "@/components/data/charts/Sparkline";
import { Eyebrow } from "@/components/data/Eyebrow";
import { cn } from "@/lib/utils/cn";
import { num, type AdMetricsView } from "./adHelpers";
import { money1, isCplBad, isFreqBad, isRoasBad } from "./adHelpers";

type MetricRow = components["schemas"]["MetricRow"];

interface MetricCell {
  k: string;
  v: string;
  flag?: boolean;
}

interface AdMetricsPanelProps {
  /** Метрики снимка объявления (текущая строка таблицы). */
  metrics: AdMetricsView;
  age: string;
  /** Сырые точки timeline для CPL-спарклайна (8 последних). */
  metricsRows: MetricRow[];
  metricsRowsLoading: boolean;
}

export function AdMetricsPanel({ metrics: m, age, metricsRows, metricsRowsLoading }: AdMetricsPanelProps) {
  // CPL sparkline (8 точек): CPL = spend/leads по точкам timeline.
  const cplSpark = useMemo<number[]>(() => {
    const pts: number[] = [];
    for (const row of metricsRows) {
      const spend = num(row.spend);
      const leads = row.leads ?? null;
      if (spend != null && leads != null && leads > 0) pts.push(spend / leads);
    }
    return pts.slice(-8);
  }, [metricsRows]);

  const metricCells: MetricCell[] = [
    { k: "spend", v: money1(m.spend) },
    { k: "CPL", v: m.cpl != null ? money1(m.cpl) : "—", flag: isCplBad(m.cpl) },
    { k: "CPM", v: m.cpm != null ? money1(m.cpm) : "—" },
    { k: "CTR", v: m.ctr != null ? `${m.ctr.toFixed(1)}%` : "—" },
    { k: "freq", v: m.freq != null ? m.freq.toFixed(1) : "—", flag: isFreqBad(m.freq) },
    { k: "ROAS", v: m.roas != null ? `${m.roas.toFixed(1)}×` : "—", flag: isRoasBad(m.roas) },
    { k: "leads", v: m.leads != null ? String(m.leads) : "—" },
    { k: "age", v: age },
  ];

  return (
    <>
      {/* Метрики-снимок */}
      <section>
        <Eyebrow className="mb-3">МЕТРИКИ · СНИМОК</Eyebrow>
        <div className="grid grid-cols-4 border border-[var(--hairline)] rounded-[var(--radius-2)] overflow-hidden">
          {metricCells.map((c, i) => (
            <div
              key={c.k}
              className={cn(
                "px-3 py-2.5",
                i % 4 !== 3 && "border-r border-[var(--hairline)]",
                i >= 4 && "border-t border-[var(--hairline)]",
                c.flag && "bg-danger-bg",
              )}
            >
              <div
                className={cn(
                  "font-display text-[9px] font-semibold uppercase tracking-[0.1em]",
                  c.flag ? "text-danger" : "text-bg-9",
                )}
              >
                {c.k}
              </div>
              <div
                className={cn(
                  "font-display tabular-nums text-[15px] mt-1",
                  c.flag ? "text-danger" : "text-bg-11",
                )}
              >
                {c.v}
              </div>
            </div>
          ))}
        </div>
      </section>

      {/* CPL sparkline */}
      <section>
        <Eyebrow className="mb-3">CPL · 8 ТОЧЕК</Eyebrow>
        <div className="bg-bg-1 border border-[var(--hairline)] rounded-[var(--radius-2)] p-4">
          {metricsRowsLoading ? (
            <Skeleton height={70} className="w-full" />
          ) : cplSpark.length >= 2 ? (
            <Sparkline
              data={cplSpark}
              color={isCplBad(m.cpl) ? "var(--danger)" : "var(--accent)"}
              w={496}
              h={70}
              fill
            />
          ) : (
            <div className="text-[13px] text-bg-9">Недостаточно данных по CPL.</div>
          )}
        </div>
      </section>
    </>
  );
}
