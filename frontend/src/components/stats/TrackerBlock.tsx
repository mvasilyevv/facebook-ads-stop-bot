/**
 * TrackerBlock — карточка «Трекер (AdSet.pro)».
 *
 * available=false — данных нет / запрос к трекеру упал на бэке (ответ не
 * роняем, TrackerBlockOut.available=false) → приглушённое «Нет данных
 * трекера», без фейковых нулей поверх реальных чисел.
 */

import { Card } from "@/components/ui/Card";
import { Skeleton } from "@/components/ui/Skeleton";
import { formatInt, formatPercentValue, formatSpend } from "@fb/shared";
import type { StatsToday } from "@fb/shared";

// TrackerBlockOut не экспортирован отдельным алиасом в @fb/shared — берём
// его indexed-access типом от StatsToday (без правок shared-пакета).
type TrackerBlockData = StatsToday["tracker"];

interface TrackerBlockProps {
  data?: TrackerBlockData;
  loading?: boolean;
  className?: string;
}

interface MetricItem {
  key: string;
  label: string;
  value: string;
}

export function TrackerBlock({ data, loading, className }: TrackerBlockProps) {
  if (loading || !data) {
    return <TrackerBlockSkeleton className={className} />;
  }

  if (!data.available) {
    return (
      <Card eyebrow="ТРЕКЕР (ADSET.PRO)" className={className}>
        <div className="text-[13px] text-bg-9 py-4 text-center">Нет данных трекера</div>
      </Card>
    );
  }

  const totals = data.totals;
  const items: MetricItem[] = [
    { key: "installs", label: "Installs", value: formatInt(totals?.installs ?? 0) },
    { key: "registrations", label: "Реги", value: formatInt(totals?.registrations ?? 0) },
    { key: "deposits", label: "Депы", value: formatInt(totals?.deposits ?? 0) },
    { key: "revenue", label: "Revenue", value: formatSpend(totals?.revenue) },
    { key: "roi", label: "ROI", value: formatPercentValue(totals?.roi_pct) },
  ];

  return (
    <Card eyebrow="ТРЕКЕР (ADSET.PRO)" className={className}>
      <div className="grid grid-cols-5 gap-4 mb-3" role="list" aria-label="Метрики трекера">
        {items.map((it) => (
          <div key={it.key} className="flex flex-col gap-1">
            <span className="text-[11px] text-bg-9">{it.label}</span>
            <span className="font-display tabular-nums text-[16px] text-bg-11">{it.value}</span>
          </div>
        ))}
      </div>
      {data.attribution_note ? (
        <div className="text-[11px] text-bg-8 pt-2 border-t border-[var(--hairline)]">
          {data.attribution_note}
        </div>
      ) : null}
    </Card>
  );
}

// ─── Скелетон ─────────────────────────────────────────────────────────────────

function TrackerBlockSkeleton({ className }: { className?: string }) {
  return (
    <Card eyebrow="ТРЕКЕР (ADSET.PRO)" className={className}>
      <div className="grid grid-cols-5 gap-4" role="status" aria-label="Загрузка трекера">
        {Array.from({ length: 5 }).map((_, i) => (
          <div key={i} className="flex flex-col gap-1">
            <Skeleton height={11} width="50%" />
            <Skeleton height={16} width="70%" />
          </div>
        ))}
      </div>
    </Card>
  );
}
