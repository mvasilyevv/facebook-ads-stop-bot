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
import { formatPercentValue, formatSpend } from "@fb/shared";
import type { FunnelDerived } from "@fb/shared";

interface DerivedMetricsGridProps {
  data?: FunnelDerived;
  loading?: boolean;
  className?: string;
}

interface MetricItem {
  key: string;
  label: string;
  value: string;
}

export function DerivedMetricsGrid({ data, loading, className }: DerivedMetricsGridProps) {
  if (loading || !data) {
    return <DerivedMetricsGridSkeleton className={className} />;
  }

  const items: MetricItem[] = [
    { key: "cpc", label: "CPC", value: formatSpend(data.cpc) },
    { key: "cpl", label: "CPL", value: formatSpend(data.cpl) },
    { key: "cpr", label: "CPR", value: formatSpend(data.cpr) },
    { key: "cpa", label: "CPA", value: formatSpend(data.cpa) },
    { key: "ctr", label: "CTR", value: formatPercentValue(data.ctr_pct) },
    { key: "cr_click_lead", label: "CR клик→лид", value: formatPercentValue(data.cr_click_lead_pct) },
    { key: "cr_lead_reg", label: "CR лид→рег", value: formatPercentValue(data.cr_lead_reg_pct) },
    { key: "cr_reg_dep", label: "CR рег→деп", value: formatPercentValue(data.cr_reg_dep_pct) },
  ];

  return (
    <Card eyebrow="ПРОИЗВОДНЫЕ МЕТРИКИ" className={className}>
      <div className="grid grid-cols-4 gap-4" role="list" aria-label="Производные метрики">
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
        className={cn("grid grid-cols-4 gap-4")}
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
