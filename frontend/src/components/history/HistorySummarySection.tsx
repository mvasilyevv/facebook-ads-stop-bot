/**
 * HistorySummarySection — KPI-сводка за период: spend/impressions/clicks/leads/regs/deposits
 * + алерты по stage + топ-правила + задачи disable/enable.
 * Слева от таймлайна (sticky колонка).
 */

import { type FC } from "react";
import { Card } from "@/components/ui/Card";
import { Skeleton } from "@/components/ui/Skeleton";
import { ErrorState } from "@/components/ui/ErrorState";
import { formatSpend, formatCompact, formatInt, ruleCodeLabel } from "@fb/shared";
import type { HistorySummary } from "@fb/shared";

// ─── KV-строка ────────────────────────────────────────────────────────────────

interface KVRowProps {
  label: string;
  value: string | number;
  accent?: "warn" | "bad" | "muted";
}

function KVRow({ label, value, accent }: KVRowProps) {
  const valClass =
    accent === "bad"
      ? "text-danger"
      : accent === "warn"
        ? "text-warning"
        : accent === "muted"
          ? "text-bg-9"
          : "text-bg-11";

  return (
    <div className="flex items-baseline justify-between gap-2 py-1.5 border-b border-bg-3 last:border-b-0">
      <span className="font-display text-[11px] uppercase tracking-[0.08em] text-bg-8 shrink-0">
        {label}
      </span>
      <span className={`font-display text-[14px] tabular-nums font-medium ${valClass}`}>
        {value}
      </span>
    </div>
  );
}

// ─── Секция ───────────────────────────────────────────────────────────────────

interface HistorySummarySectionProps {
  data: HistorySummary | undefined;
  isLoading: boolean;
  error: unknown;
  onRetry?: () => void;
}

export const HistorySummarySection: FC<HistorySummarySectionProps> = ({
  data,
  isLoading,
  error,
  onRetry,
}) => {
  if (isLoading) {
    return (
      <div className="space-y-2">
        {Array.from({ length: 8 }).map((_, i) => (
          <Skeleton key={i} className="h-7 w-full" />
        ))}
      </div>
    );
  }

  if (error) {
    return <ErrorState error={error} onRetry={onRetry} />;
  }

  if (!data) return null;

  const { totals, alerts, tasks } = data;

  // Топ-правила (до 5)
  const topRules = [...alerts.by_rule]
    .sort((a, b) => b.count - a.count)
    .slice(0, 5);

  return (
    <div className="space-y-4">
      {/* Метрики */}
      <Card eyebrow="Метрики" padded>
        <div>
          <KVRow label="Spend" value={formatSpend(totals.spend)} />
          <KVRow label="Impressions" value={formatCompact(totals.impressions)} />
          <KVRow label="Clicks" value={formatInt(totals.clicks)} />
          <KVRow label="Leads" value={formatInt(totals.leads)} />
          <KVRow label="Regs" value={formatInt(totals.registrations)} />
          <KVRow label="Deposits" value={formatInt(totals.deposits)} />
          <KVRow label="Active ads" value={formatInt(totals.active_ads_count)} accent="muted" />
        </div>
      </Card>

      {/* Алерты */}
      <Card eyebrow="Алерты" padded>
        <div>
          <KVRow label="Warning" value={formatInt(alerts.warning_count)} accent="warn" />
          <KVRow label="Stop" value={formatInt(alerts.stop_count)} accent="bad" />
        </div>
        {topRules.length > 0 && (
          <div className="mt-3 pt-3 border-t border-bg-3 space-y-1.5">
            <div className="font-display text-[10px] uppercase tracking-[0.1em] text-bg-7 mb-2">
              Топ правила
            </div>
            {topRules.map((r) => (
              <div key={r.rule_code} className="flex items-baseline justify-between gap-2">
                <span className="font-display text-[11px] text-bg-9 truncate">
                  {ruleCodeLabel(r.rule_code, true)}
                </span>
                <span className="font-display text-[12px] tabular-nums text-danger font-medium shrink-0">
                  {r.count}
                </span>
              </div>
            ))}
          </div>
        )}
      </Card>

      {/* Задачи */}
      <Card eyebrow="Задачи" padded>
        <div>
          <KVRow label="Disable ОК" value={formatInt(tasks.disable_completed)} accent="muted" />
          <KVRow label="Disable сбой" value={formatInt(tasks.disable_failed)} accent={tasks.disable_failed > 0 ? "bad" : "muted"} />
          <KVRow label="Enable ОК" value={formatInt(tasks.enable_completed)} accent="muted" />
        </div>
      </Card>
    </div>
  );
};
