/**
 * BreakdownTable — простая таблица разреза «Статистики залива» по офферу/кампании
 * (только режим today — /stats/period breakdown не отдаёт).
 *
 * Стиль — как таблица порогов в OfferRulesFields.tsx (semantic <table>, hairline-строки).
 */

import { Card } from "@/components/ui/Card";
import { EmptyState } from "@/components/ui/EmptyState";
import { Skeleton } from "@/components/ui/Skeleton";
import { formatInt, formatSpend } from "@fb/shared";
import type { StatsToday } from "@fb/shared";

type BreakdownRow = NonNullable<StatsToday["breakdown"]>[number];

interface BreakdownTableProps {
  rows?: BreakdownRow[] | null;
  breakdownKind: "offer" | "campaign";
  loading?: boolean;
  className?: string;
}

export function BreakdownTable({ rows, breakdownKind, loading, className }: BreakdownTableProps) {
  const title = breakdownKind === "offer" ? "РАЗРЕЗ ПО ОФФЕРУ" : "РАЗРЕЗ ПО КАМПАНИИ";

  if (loading) {
    return (
      <Card eyebrow={title} className={className}>
        <div className="flex flex-col gap-2" role="status" aria-label="Загрузка разреза">
          {Array.from({ length: 4 }).map((_, i) => (
            <Skeleton key={i} height={16} />
          ))}
        </div>
      </Card>
    );
  }

  if (!rows || rows.length === 0) {
    return (
      <Card eyebrow={title} className={className}>
        <EmptyState title="Нет данных" description="За текущие сутки разреза нет." />
      </Card>
    );
  }

  return (
    <Card eyebrow={title} padded={false} className={className}>
      <div className="overflow-x-auto p-4 sm:p-6">
        <table className="min-w-[720px] w-full text-[12.5px]">
          <thead>
            <tr className="text-bg-8 font-display text-[10px] tracking-wider uppercase">
              <th className="text-left font-normal pb-1.5">{breakdownKind === "offer" ? "Оффер" : "Кампания"}</th>
              <th className="text-right font-normal pb-1.5">Траты</th>
              <th className="text-right font-normal pb-1.5">Клики</th>
              <th className="text-right font-normal pb-1.5">Лиды</th>
              <th className="text-right font-normal pb-1.5">Реги</th>
              <th className="text-right font-normal pb-1.5">Депы</th>
              <th className="text-right font-normal pb-1.5">CPL</th>
            </tr>
          </thead>
          <tbody>
            {rows.map((r) => (
              <tr key={r.key} className="border-t border-[var(--hairline)]">
                <td className="py-1.5 text-bg-10">{r.label}</td>
                <td className="py-1.5 text-right font-display tabular-nums text-bg-11">
                  {formatSpend(r.spend)}
                </td>
                <td className="py-1.5 text-right font-display tabular-nums text-bg-11">
                  {formatInt(r.clicks)}
                </td>
                <td className="py-1.5 text-right font-display tabular-nums text-bg-11">
                  {formatInt(r.leads)}
                </td>
                <td className="py-1.5 text-right font-display tabular-nums text-bg-11">
                  {formatInt(r.registrations)}
                </td>
                <td className="py-1.5 text-right font-display tabular-nums text-bg-11">
                  {formatInt(r.deposits)}
                </td>
                <td className="py-1.5 text-right font-display tabular-nums text-bg-11">
                  {formatSpend(r.cpl)}
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </Card>
  );
}
