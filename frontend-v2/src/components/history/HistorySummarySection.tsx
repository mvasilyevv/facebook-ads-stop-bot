/**
 * HistorySummarySection — KPI-strip + breakdown по stage + by_rule.
 * Отображает агрегаты за выбранный период.
 */

import { KPICard, KPIStrip } from "@/components/data/KPICard";
import { RuleBadge } from "@/components/domain/RuleBadge";
import { Skeleton } from "@/components/ui/Skeleton";
import { ErrorState } from "@/components/ui/ErrorState";
import { formatSpend, formatInt } from "@/lib/utils/format";
import type { HistorySummary } from "@/lib/types/api";

interface HistorySummarySectionProps {
  summary: HistorySummary | undefined;
  isLoading: boolean;
  isError: boolean;
  error?: unknown;
  onRetry?: () => void;
}

export function HistorySummarySection({
  summary,
  isLoading,
  isError,
  error,
  onRetry,
}: HistorySummarySectionProps) {
  if (isError) {
    return (
      <ErrorState
        title="Не удалось загрузить сводку за период."
        error={error}
        onRetry={onRetry}
        className="mb-10"
      />
    );
  }

  if (isLoading) {
    return <SummarySkeleton />;
  }

  const warningCount = summary?.alerts.warning_count ?? 0;
  const stopCount = summary?.alerts.stop_count ?? 0;
  const totalAlerts = warningCount + stopCount;

  return (
    <div className="mb-10">
      {/* KPI strip: spend / leads / deposits / alerts */}
      <KPIStrip>
        <KPICard
          label="Spend"
          value={formatSpend(summary?.totals.spend)}
          hint="за период"
          variant="muted"
        />
        <KPICard
          label="Лиды"
          value={formatInt(summary?.totals.leads)}
          hint="лиды"
          variant="success"
        />
        <KPICard
          label="Депозиты"
          value={formatInt(summary?.totals.deposits)}
          hint="депозиты"
          variant="info"
        />
        <KPICard
          label="Алерты"
          value={formatInt(totalAlerts)}
          hint="всего событий"
          variant="warning"
        />
      </KPIStrip>

      {/* Breakdown по stage + by_rule */}
      <div className="grid grid-cols-2 gap-6 mt-6">
        {/* По stage */}
        <div className="border border-bg-5 bg-bg-1 p-5">
          <h3 className="font-display text-[10px] uppercase tracking-[0.14em] text-bg-8 mb-4">
            <span className="text-bg-7 mr-1.5">02</span>
            Алерты · по стадиям
          </h3>
          <div className="space-y-3">
            {["warning", "stop"].map((stage) => {
              const count = stage === "stop" ? stopCount : warningCount;
              const pct = totalAlerts > 0 ? Math.round((count / totalAlerts) * 100) : 0;
              return (
                <div key={stage} className="flex items-center gap-3">
                  <span
                    className={`font-display text-[11px] uppercase tracking-wider w-14 ${
                      stage === "stop" ? "text-danger" : "text-warning"
                    }`}
                  >
                    {stage}
                  </span>
                  {/* Прогресс-бар */}
                  <div className="flex-1 h-1 bg-bg-3">
                    <div
                      className={`h-full ${stage === "stop" ? "bg-danger" : "bg-warning"}`}
                      style={{ width: `${pct}%` }}
                    />
                  </div>
                  <span className="font-display text-[13px] tabular-nums text-bg-11 w-10 text-right">
                    {formatInt(count)}
                  </span>
                </div>
              );
            })}
          </div>
        </div>

        {/* По правилам */}
        <div className="border border-bg-5 bg-bg-1 p-5">
          <h3 className="font-display text-[10px] uppercase tracking-[0.14em] text-bg-8 mb-4">
            <span className="text-bg-7 mr-1.5">03</span>
            Алерты · по правилам
          </h3>
          {!summary?.alerts.by_rule?.length ? (
            <span className="font-display text-[12px] text-bg-9">Нет данных</span>
          ) : (
            <div className="space-y-2">
              {summary.alerts.by_rule.slice(0, 8).map((r) => (
                <div key={r.rule_code} className="flex items-center gap-3">
                  <RuleBadge code={r.rule_code} />
                  <span className="font-display text-[13px] tabular-nums text-bg-10 ml-auto">
                    {formatInt(r.count)}
                  </span>
                </div>
              ))}
            </div>
          )}
        </div>
      </div>
    </div>
  );
}

/** Skeleton для секции Summary. */
function SummarySkeleton() {
  return (
    <div className="mb-10">
      {/* KPI skeleton */}
      <div className="grid grid-cols-4 border border-bg-5 bg-bg-1 divide-x divide-bg-5">
        {[0, 1, 2, 3].map((i) => (
          <div key={i} className="p-6 pb-7">
            <Skeleton height={10} width="50%" className="mb-4" />
            <Skeleton height={48} width="70%" className="mb-3" />
            <Skeleton height={10} width="40%" />
          </div>
        ))}
      </div>
      {/* Breakdown skeleton */}
      <div className="grid grid-cols-2 gap-6 mt-6">
        {[0, 1].map((i) => (
          <div key={i} className="border border-bg-5 bg-bg-1 p-5">
            <Skeleton height={10} width="40%" className="mb-4" />
            <div className="space-y-3">
              {[0, 1, 2].map((j) => (
                <Skeleton key={j} height={14} />
              ))}
            </div>
          </div>
        ))}
      </div>
    </div>
  );
}
