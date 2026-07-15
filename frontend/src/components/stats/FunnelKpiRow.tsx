/**
 * FunnelKpiRow — переиспользуемая KPI-строка воронки залива.
 *
 * Паттерн ячейки — как SparklineKpiRow (eyebrow + число + note), но без
 * FSM-тона (нет warning/stop/disabled семантики — воронка залива, не FSM
 * объявлений) и без sparkline (честной почасовой истории money-метрик
 * per-cell в API нет, спарклайн здесь не рисуем — принцип no-fake-data).
 *
 * Два режима:
 *   full    — 5 ячеек (spend/клики/лиды/реги/депы), CPL+CPA в note-строке
 *             ячейки «Лиды»/«Депозиты».
 *   compact — 4 ячейки (клики/лиды/реги/депы; spend уже в шапке hero-графика) для Dashboard.
 */

import { Eyebrow } from "@/components/data/Eyebrow";
import { Skeleton } from "@/components/ui/Skeleton";
import { cn } from "@/lib/utils/cn";
import { formatTrackerCount, readTrackerRealtime } from "@/lib/types/trackerRealtime";
import { formatInt, formatSpend } from "@fb/shared";
import type { FunnelDerived, FunnelTotals } from "@fb/shared";

interface FunnelKpiRowProps {
  data?: { totals: FunnelTotals; derived: FunnelDerived };
  /** Event-driven AdSet.pro блок. Если передан, реги/депы не берём из Meta. */
  trackerData?: unknown;
  loading?: boolean;
  /** compact — 4 ячейки воронки без spend (для Dashboard). Default false (5 ячеек). */
  compact?: boolean;
  className?: string;
}

interface Cell {
  key: string;
  eyebrow: string;
  value: string;
  note: string;
}

export function FunnelKpiRow({
  data,
  trackerData,
  loading,
  compact = false,
  className,
}: FunnelKpiRowProps) {
  if (loading || !data) {
    return <FunnelKpiRowSkeleton cellCount={compact ? 4 : 5} className={className} />;
  }

  const { totals, derived } = data;
  const tracker = trackerData !== undefined ? readTrackerRealtime(trackerData) : null;
  const useTracker = trackerData !== undefined;
  const trackerAvailable = tracker?.available !== false;
  const registrations = useTracker
    ? formatTrackerCount(trackerAvailable ? (tracker?.registrations ?? null) : null)
    : formatInt(totals.registrations);
  const confirmedDeposits = useTracker
    ? formatTrackerCount(trackerAvailable ? (tracker?.confirmedDeposits ?? null) : null)
    : formatInt(totals.deposits);

  // compact (Dashboard): БЕЗ spend — он уже в шапке hero-графика «SPEND × ЧАС»
  // (жалоба владельца на дубль); вместо него клики — полная воронка одним взглядом.
  const cells: Cell[] = compact
    ? [
        { key: "clicks", eyebrow: "КЛИКИ", value: formatInt(totals.clicks), note: "переходов" },
        {
          key: "leads",
          eyebrow: "ЛИДЫ",
          value: formatInt(totals.leads),
          note: `CPL ${formatSpend(derived.cpl)}`,
        },
        {
          key: "registrations",
          eyebrow: "РЕГИ",
          value: registrations,
          note: useTracker ? "AdSet.pro · Live" : "регистраций",
        },
        {
          key: "deposits",
          eyebrow: "ПОДТВ. ДЕПЫ",
          value: confirmedDeposits,
          note: useTracker ? "регистрация + FTD" : "депозитов",
        },
      ]
    : [
        { key: "spend", eyebrow: "SPEND", value: formatSpend(totals.spend), note: "потрачено" },
        { key: "clicks", eyebrow: "КЛИКИ", value: formatInt(totals.clicks), note: "переходов" },
        {
          key: "leads",
          eyebrow: "ЛИДЫ",
          value: formatInt(totals.leads),
          note: `CPL ${formatSpend(derived.cpl)}`,
        },
        {
          key: "registrations",
          eyebrow: "РЕГИ",
          value: registrations,
          note: useTracker ? "AdSet.pro · Live" : "регистраций",
        },
        {
          key: "deposits",
          eyebrow: "ПОДТВ. ДЕПЫ",
          value: confirmedDeposits,
          note: useTracker ? "регистрация + FTD" : `CPA ${formatSpend(derived.cpa)}`,
        },
      ];

  return (
    <div
      className={cn(
        "grid grid-cols-2 gap-px overflow-hidden rounded-[var(--radius-3)] border border-[var(--hairline)] bg-[var(--hairline)]",
        compact ? "lg:grid-cols-4" : "md:grid-cols-3 xl:grid-cols-5",
        className,
      )}
      role="list"
      aria-label="Воронка залива"
    >
      {cells.map((c) => (
        <div
          key={c.key}
          className="flex flex-col gap-2 bg-bg-0 p-5"
        >
          <Eyebrow>{c.eyebrow}</Eyebrow>
          <span
            className="font-display font-medium tabular-nums text-bg-11"
            style={{ fontSize: 28, lineHeight: 1 }}
          >
            {c.value}
          </span>
          <div className="text-[12px] text-bg-9">{c.note}</div>
        </div>
      ))}
    </div>
  );
}

// ─── Скелетон ─────────────────────────────────────────────────────────────────

function FunnelKpiRowSkeleton({
  cellCount,
  className,
}: {
  cellCount: number;
  className?: string;
}) {
  return (
    <div
      className={cn(
        "grid grid-cols-2 gap-px overflow-hidden rounded-[var(--radius-3)] border border-[var(--hairline)] bg-[var(--hairline)]",
        cellCount === 4 ? "lg:grid-cols-4" : "md:grid-cols-3 xl:grid-cols-5",
        className,
      )}
      role="status"
      aria-label="Загрузка воронки"
    >
      {Array.from({ length: cellCount }).map((_, i) => (
        <div
          key={i}
          className="flex flex-col gap-2 bg-bg-0 p-5"
        >
          <Skeleton height={10} width="55%" />
          <Skeleton height={28} width="60%" />
          <Skeleton height={12} width="70%" />
        </div>
      ))}
    </div>
  );
}
