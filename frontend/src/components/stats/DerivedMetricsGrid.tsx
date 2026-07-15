/**
 * DerivedMetricsGrid — сетка производных метрик воронки.
 *
 * CPC/CPL/CPR/CPA — денежные (formatSpend), CTR/CR-ступени — проценты
 * (formatPercentValue, бэк уже отдаёт число в процентах, не дробь 0..1).
 * None (знаменатель нулевой) → «—» — formatSpend/formatPercentValue уже
 * это делают сами, здесь ничего дополнительно не подставляем (no-fake-data).
 */

import { Card } from "@/components/ui/Card";
import { Skeleton } from "@/components/ui/Skeleton";
import { cn } from "@/lib/utils/cn";
import { readTrackerRealtime } from "@/lib/types/trackerRealtime";
import { formatPercentValue, formatSpend } from "@fb/shared";
import type { FunnelDerived } from "@fb/shared";

interface DerivedMetricsGridProps {
  data?: FunnelDerived;
  /** Meta totals нужны для честного cross-source CPR/CPA/CR. */
  metaTotals?: { spend?: string | null; leads?: number | null };
  trackerData?: unknown;
  loading?: boolean;
  className?: string;
}

interface MetricItem {
  key: string;
  label: string;
  value: string;
}

export function DerivedMetricsGrid({
  data,
  metaTotals,
  trackerData,
  loading,
  className,
}: DerivedMetricsGridProps) {
  if (loading || !data) {
    return <DerivedMetricsGridSkeleton className={className} />;
  }

  const tracker = trackerData !== undefined ? readTrackerRealtime(trackerData) : null;
  const useTracker = trackerData !== undefined;
  const trackerAvailable = tracker?.available !== false;
  const spend = metaTotals?.spend == null ? null : Number(metaTotals.spend);
  const leads = metaTotals?.leads ?? null;
  const registrations = trackerAvailable ? (tracker?.registrations ?? null) : null;
  const confirmed = trackerAvailable ? (tracker?.confirmedDeposits ?? null) : null;
  const moneyRatio = (amount: number | null, count: number | null) =>
    amount != null && Number.isFinite(amount) && count != null && count > 0
      ? String(amount / count)
      : null;
  const percentRatio = (from: number | null, to: number | null) =>
    from != null && from > 0 && to != null ? (to / from) * 100 : null;

  const items: MetricItem[] = [
    { key: "cpc", label: "CPC", value: formatSpend(data.cpc) },
    { key: "cpl", label: "CPL", value: formatSpend(data.cpl) },
    {
      key: "cpr",
      label: "CPR",
      value: formatSpend(useTracker ? moneyRatio(spend, registrations) : data.cpr),
    },
    {
      key: "cpa",
      label: "CPA",
      value: formatSpend(useTracker ? moneyRatio(spend, confirmed) : data.cpa),
    },
    { key: "ctr", label: "CTR", value: formatPercentValue(data.ctr_pct) },
    { key: "cr_click_lead", label: "CR клик→лид", value: formatPercentValue(data.cr_click_lead_pct) },
    {
      key: "cr_lead_reg",
      label: "CR лид→рег",
      value: formatPercentValue(
        useTracker ? percentRatio(leads, registrations) : data.cr_lead_reg_pct,
      ),
    },
    {
      key: "cr_reg_dep",
      label: "CR рег→деп",
      value: formatPercentValue(
        useTracker ? percentRatio(registrations, confirmed) : data.cr_reg_dep_pct,
      ),
    },
  ];

  return (
    <Card eyebrow="ПРОИЗВОДНЫЕ МЕТРИКИ" className={className}>
      <div className="grid grid-cols-2 gap-4 sm:grid-cols-4" role="list" aria-label="Производные метрики">
        {items.map((it) => (
          <div key={it.key} className="flex flex-col gap-1">
            <span className="text-[11px] text-bg-9">{it.label}</span>
            <span className="font-display tabular-nums text-[16px] text-bg-11">{it.value}</span>
          </div>
        ))}
      </div>
    </Card>
  );
}

// ─── Скелетон ─────────────────────────────────────────────────────────────────

function DerivedMetricsGridSkeleton({ className }: { className?: string }) {
  return (
    <Card eyebrow="ПРОИЗВОДНЫЕ МЕТРИКИ" className={className}>
      <div
        className={cn("grid grid-cols-2 gap-4 sm:grid-cols-4")}
        role="status"
        aria-label="Загрузка производных метрик"
      >
        {Array.from({ length: 8 }).map((_, i) => (
          <div key={i} className="flex flex-col gap-1">
            <Skeleton height={11} width="50%" />
            <Skeleton height={16} width="70%" />
          </div>
        ))}
      </div>
    </Card>
  );
}
