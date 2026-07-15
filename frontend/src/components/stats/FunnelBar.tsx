/**
 * FunnelBar — горизонтальная воронка ступеней клики → лиды → реги → депы.
 *
 * Div-бары editorial-стиля (без сторонних chart-либ). Ширина бара — доля
 * от первой ненулевой ступени (клики), линейная (не log — воронка обычно
 * не на порядки различается между соседними ступенями, линейная нагляднее
 * для CR%). Между ступенями — подпись CR% (переход i → i+1).
 *
 * Пустая воронка (все ступени 0) — не рисуем бары, только «Нет данных».
 */

import { Card } from "@/components/ui/Card";
import { Skeleton } from "@/components/ui/Skeleton";
import { formatTrackerCount, readTrackerRealtime } from "@/lib/types/trackerRealtime";
import { formatInt } from "@fb/shared";
import type { FunnelTotals } from "@fb/shared";

interface FunnelBarProps {
  data?: FunnelTotals;
  trackerData?: unknown;
  loading?: boolean;
  className?: string;
}

interface Stage {
  key: string;
  label: string;
  value: number | null;
  color: string;
}

/** CR% между соседними ступенями. null — знаменатель нулевой (не «0%», а «—»). */
function crBetween(from: number | null, to: number | null): string {
  if (from == null || from <= 0 || to == null) return "—";
  return `${((to / from) * 100).toFixed(1)}%`;
}

export function FunnelBar({ data, trackerData, loading, className }: FunnelBarProps) {
  if (loading || !data) {
    return <FunnelBarSkeleton className={className} />;
  }

  const tracker = trackerData !== undefined ? readTrackerRealtime(trackerData) : null;
  const useTracker = trackerData !== undefined;
  const trackerAvailable = tracker?.available !== false;
  const stages: Stage[] = [
    { key: "clicks", label: "Клики", value: data.clicks, color: "var(--info)" },
    { key: "leads", label: "Лиды", value: data.leads, color: "var(--accent)" },
    {
      key: "registrations",
      label: "Реги · AdSet.pro",
      value: useTracker && trackerAvailable ? (tracker?.registrations ?? null) : useTracker ? null : data.registrations,
      color: "var(--success)",
    },
    {
      key: "deposits",
      label: "Подтв. депы",
      value: useTracker && trackerAvailable ? (tracker?.confirmedDeposits ?? null) : useTracker ? null : data.deposits,
      color: "var(--warning)",
    },
  ];

  const max = Math.max(...stages.map((s) => s.value ?? 0), 0);

  if (max <= 0) {
    return (
      <Card eyebrow="ВОРОНКА" className={className}>
        <div className="text-[13px] text-bg-9 py-4 text-center">Нет данных</div>
      </Card>
    );
  }

  return (
    <Card eyebrow="ВОРОНКА" className={className}>
      <div className="flex flex-col gap-3" role="list" aria-label="Ступени воронки">
        {stages.map((s, i) => {
          const widthPct =
            max > 0 && s.value != null
              ? Math.max((s.value / max) * 100, s.value > 0 ? 2 : 0)
              : 0;
          const next = stages[i + 1];
          return (
            <div key={s.key}>
              <div className="flex items-center justify-between mb-1">
                <span className="text-[12px] text-bg-9">{s.label}</span>
                <span className="font-display tabular-nums text-[13px] text-bg-11">
                  {useTracker && (s.key === "registrations" || s.key === "deposits")
                    ? formatTrackerCount(s.value)
                    : s.value == null
                      ? "—"
                      : formatInt(s.value)}
                </span>
              </div>
              <div className="h-2 bg-bg-3 rounded-full overflow-hidden">
                <div
                  className="h-full rounded-full transition-[width]"
                  style={{ width: `${widthPct}%`, background: s.color }}
                />
              </div>
              {next ? (
                <div className="text-[11px] text-bg-8 mt-1 text-right">
                  CR → {next.label.toLowerCase()}: {crBetween(s.value, next.value)}
                </div>
              ) : null}
            </div>
          );
        })}
      </div>
    </Card>
  );
}

// ─── Скелетон ─────────────────────────────────────────────────────────────────

function FunnelBarSkeleton({ className }: { className?: string }) {
  return (
    <Card eyebrow="ВОРОНКА" className={className}>
      <div className="flex flex-col gap-3" role="status" aria-label="Загрузка ступеней воронки">
        {Array.from({ length: 4 }).map((_, i) => (
          <div key={i}>
            <div className="flex items-center justify-between mb-1">
              <Skeleton height={12} width="30%" />
              <Skeleton height={13} width="15%" />
            </div>
            <Skeleton height={8} width="100%" />
          </div>
        ))}
      </div>
    </Card>
  );
}
