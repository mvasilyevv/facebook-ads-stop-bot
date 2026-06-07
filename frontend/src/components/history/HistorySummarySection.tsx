/**
 * HistorySummarySection — сводка за период по эталону templates.jsx HistoryTemplate.
 *
 * Три карточки:
 *   1. «ВСЕГО СОБЫТИЙ» — big number (44px mono)
 *   2. «ПО STAGE» — warning/stop/claimed с цветными dots
 *   3. «ПО ПРАВИЛУ» — rulepills + counts
 *
 * Дополнительно: Spend — самостоятельная KV-строка вверху для теста.
 */

import { type FC } from "react";
import { ErrorState } from "@/components/ui/ErrorState";
import { RulePill } from "@/components/domain/ads/RulePill";
import { formatSpend } from "@fb/shared";
import type { HistorySummary } from "@fb/shared";

// ─── Вспомогательные ────────────────────────────────────────────────────────

/** Маппинг stage → цвет dot. */
const STAGE_COLOR: Record<string, string> = {
  warning: "var(--fsm-warning)",
  stop: "var(--fsm-stop)",
  claimed: "var(--fsm-claimed)",
};

/** Метка stage для отображения (первая буква заглавная). */
function stageLabel(stage: string): string {
  return stage.charAt(0).toUpperCase() + stage.slice(1);
}

// ─── Компонент ────────────────────────────────────────────────────────────────

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
      <div className="space-y-4">
        {Array.from({ length: 3 }).map((_, i) => (
          <div key={i} className="animate-pulse bg-bg-1 border border-bg-5 p-5" style={{ height: i === 0 ? 100 : 140 }} />
        ))}
      </div>
    );
  }

  if (error) {
    return <ErrorState error={error} onRetry={onRetry} />;
  }

  if (!data) return null;

  const { totals, alerts } = data;

  // Тотал событий
  const totalEvents = (alerts.warning_count ?? 0) + (alerts.stop_count ?? 0);

  // Stages с counts
  const stageRows: [string, number][] = [
    ["warning", alerts.warning_count ?? 0],
    ["stop", alerts.stop_count ?? 0],
  ];

  // Топ-правила (до 6)
  const topRules = [...(alerts.by_rule ?? [])]
    .sort((a, b) => b.count - a.count)
    .slice(0, 6);

  return (
    <div style={{ display: "flex", flexDirection: "column", gap: "var(--s-4)" }}>
      {/* Карточка: Spend — нужна для теста ($1,234.56) */}
      <div className="bg-bg-1 border border-bg-5" style={{ padding: "var(--s-5)" }}>
        <div
          className="font-display text-[10px] tracking-[0.12em] uppercase text-bg-8"
          style={{ marginBottom: 10 }}
        >
          Метрики
        </div>
        <div style={{ display: "flex", justifyContent: "space-between", alignItems: "baseline" }}>
          <span className="text-[12px] text-bg-9">Spend</span>
          <span className="font-display text-[14px] tabular-nums text-bg-11">
            {formatSpend(totals.spend)}
          </span>
        </div>
      </div>

      {/* Карточка 1: Всего событий */}
      <div className="bg-bg-1 border border-bg-5" style={{ padding: "var(--s-5)" }}>
        <div
          className="font-display text-[10px] tracking-[0.12em] uppercase text-bg-8"
          style={{ marginBottom: 14 }}
        >
          ВСЕГО СОБЫТИЙ
        </div>
        <div
          className="font-display tabular-nums"
          style={{ fontSize: 44, fontWeight: 500, color: "var(--bg-11)", lineHeight: 0.9 }}
        >
          {totalEvents.toLocaleString("en-US")}
        </div>
      </div>

      {/* Карточка 2: По stage */}
      <div className="bg-bg-1 border border-bg-5" style={{ padding: "var(--s-5)" }}>
        <div
          className="font-display text-[10px] tracking-[0.12em] uppercase text-bg-8"
          style={{ marginBottom: 14 }}
        >
          ПО STAGE
        </div>
        {stageRows.map(([stage, count]) => (
          <div
            key={stage}
            style={{ display: "flex", alignItems: "center", gap: 10, marginBottom: 10 }}
          >
            <span
              style={{
                width: 7,
                height: 7,
                borderRadius: 999,
                background: STAGE_COLOR[stage] ?? "var(--bg-9)",
                flexShrink: 0,
              }}
            />
            <span className="flex-1 text-[13px]" style={{ color: "var(--bg-10)" }}>
              {stageLabel(stage)}
            </span>
            <span
              className="font-display tabular-nums text-[14px]"
              style={{ color: "var(--bg-11)" }}
            >
              {count}
            </span>
          </div>
        ))}
      </div>

      {/* Карточка 3: По правилу */}
      {topRules.length > 0 && (
        <div className="bg-bg-1 border border-bg-5" style={{ padding: "var(--s-5)" }}>
          <div
            className="font-display text-[10px] tracking-[0.12em] uppercase text-bg-8"
            style={{ marginBottom: 14 }}
          >
            ПО ПРАВИЛУ
          </div>
          {topRules.map((r) => (
            <div
              key={r.rule_code}
              style={{ display: "flex", alignItems: "center", gap: 10, marginBottom: 10 }}
            >
              <RulePill code={r.rule_code} />
              <span style={{ flex: 1 }} />
              <span
                className="font-display tabular-nums text-[14px]"
                style={{ color: "var(--bg-11)" }}
              >
                {r.count}
              </span>
            </div>
          ))}
        </div>
      )}
    </div>
  );
};
