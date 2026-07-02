/**
 * TrackerBlockMini — карточка «Трекер (AdSet.pro)»: реги/депы/revenue/ROI
 * + attribution_note. available=false → «Нет данных трекера» (пустое состояние,
 * не ошибка — сбой источника уже обработан бэком, ответ не роняется).
 */
import type { StatsToday } from "@fb/shared";
import { formatInt, formatSpend, formatPercentValue } from "@fb/shared";
import { Eyebrow } from "@/components/data/Eyebrow";
import { EmptyState, Skeleton } from "@/components/ui";

/** Блок tracker — общая форма для StatsToday и StatsPeriod (StatsPeriod не экспортирован
 * отдельным публичным алиасом в @fb/shared, поэтому достаём тип через StatsToday). */
export type TrackerBlock = StatsToday["tracker"];

interface TrackerBlockMiniProps {
  tracker?: TrackerBlock;
  loading?: boolean;
}

function Metric({ eyebrow, value }: { eyebrow: string; value: string }) {
  return (
    <div className="flex flex-col gap-0.5">
      <span className="font-display text-[10px] uppercase tracking-[0.1em] text-bg-9">{eyebrow}</span>
      <span className="font-display tabular-nums text-[18px] text-bg-11">{value}</span>
    </div>
  );
}

export function TrackerBlockMini({ tracker, loading }: TrackerBlockMiniProps) {
  return (
    <section>
      <Eyebrow className="mb-2.5 flex">ТРЕКЕР (ADSET.PRO)</Eyebrow>
      <div className="border border-[var(--hairline)] bg-bg-1 rounded-[var(--radius-3)] p-4">
        {loading ? (
          <div className="grid grid-cols-2 gap-4">
            {Array.from({ length: 4 }, (_, i) => (
              <div key={i} className="space-y-1.5">
                <Skeleton className="h-3 w-14" />
                <Skeleton className="h-5 w-16" />
              </div>
            ))}
          </div>
        ) : !tracker || !tracker.available ? (
          <EmptyState title="Нет данных трекера" description="AdSet.pro не вернул данные за этот период" />
        ) : (
          <>
            <div className="grid grid-cols-2 gap-4">
              <Metric eyebrow="РЕГИСТРАЦИИ" value={formatInt(tracker.totals?.registrations ?? null)} />
              <Metric eyebrow="ДЕПОЗИТЫ" value={formatInt(tracker.totals?.deposits ?? null)} />
              <Metric eyebrow="REVENUE" value={formatSpend(tracker.totals?.revenue ?? null)} />
              <Metric eyebrow="ROI" value={formatPercentValue(tracker.totals?.roi_pct ?? null)} />
            </div>
            {tracker.attribution_note && (
              <p className="text-[11px] text-bg-8 mt-3.5 leading-snug">{tracker.attribution_note}</p>
            )}
          </>
        )}
      </div>
    </section>
  );
}
