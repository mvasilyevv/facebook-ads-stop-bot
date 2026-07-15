/**
 * TrackerBlock — карточка «Трекер (AdSet.pro)».
 *
 * available=false — данных нет / запрос к трекеру упал на бэке (ответ не
 * роняем, TrackerBlockOut.available=false) → приглушённое «Нет данных
 * трекера», без фейковых нулей поверх реальных чисел.
 */

import { Card } from "@/components/ui/Card";
import { Skeleton } from "@/components/ui/Skeleton";
import { cn } from "@/lib/utils/cn";
import {
  formatTrackerCount,
  formatTrackerLag,
  formatTrackerTimestamp,
  readTrackerRealtime,
} from "@/lib/types/trackerRealtime";
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
      <Card
        eyebrow={<span className="text-success">ADSET.PRO · LIVE</span>}
        action={<QualityPill quality={null} available={false} />}
        className={className}
      >
        <div className="py-4 text-center text-[13px] text-bg-9">Нет данных трекера</div>
      </Card>
    );
  }

  const realtime = readTrackerRealtime(data);
  const totals = data.totals;
  const items: MetricItem[] = [
    {
      key: "registrations",
      label: "Регистрации",
      value: formatTrackerCount(realtime?.registrations ?? null),
    },
    { key: "ftds", label: "FTD", value: formatTrackerCount(realtime?.ftds ?? null) },
    {
      key: "confirmed_deposits",
      label: "Подтверждены",
      value: formatTrackerCount(realtime?.confirmedDeposits ?? null),
    },
    {
      key: "redeposits",
      label: "Редепозиты",
      value: formatTrackerCount(realtime?.redeposits ?? null),
    },
    {
      key: "unmatched",
      label: "Не сопоставлено",
      value: formatTrackerCount(realtime?.unmatchedEvents ?? null),
    },
    { key: "revenue", label: "Revenue", value: formatSpend(totals?.revenue) },
  ];

  return (
    <Card
      eyebrow={<span className="text-success">ADSET.PRO · LIVE</span>}
      action={
        <QualityPill quality={realtime?.dataQuality ?? null} available={realtime?.available ?? true} />
      }
      className={className}
    >
      <div
        className="mb-3 grid grid-cols-2 gap-4 sm:grid-cols-3 xl:grid-cols-6"
        role="list"
        aria-label="Метрики трекера"
      >
        {items.map((it) => (
          <div key={it.key} className="flex flex-col gap-1">
            <span className="text-[11px] text-bg-9">{it.label}</span>
            <span
              className={cn(
                "font-display tabular-nums text-[16px] text-bg-11",
                it.key === "unmatched" && (realtime?.unmatchedEvents ?? 0) > 0 && "text-warning",
              )}
            >
              {it.value}
            </span>
          </div>
        ))}
      </div>
      <div className="flex flex-wrap gap-x-4 gap-y-1 border-t border-[var(--hairline)] pt-2 text-[11px] text-bg-8">
        <span>
          Последний postback:{" "}
          <span className="text-bg-10">{formatTrackerTimestamp(realtime?.lastEventAt ?? null)}</span>
        </span>
        <span>
          Обработка:{" "}
          <span className="text-bg-10">{formatTrackerLag(realtime?.processingLagMs ?? null)}</span>
        </span>
        <span>
          Очередь:{" "}
          <span className="tabular-nums text-bg-10">{formatTrackerCount(realtime?.backlog ?? null)}</span>
        </span>
        {realtime?.duplicateEvents != null ? (
          <span>
            Дубли:{" "}
            <span className="tabular-nums text-bg-10">
              {formatTrackerCount(realtime.duplicateEvents)}
            </span>
          </span>
        ) : null}
        {realtime?.unsupportedEvents != null ? (
          <span>
            Неподдерживаемые:{" "}
            <span className="tabular-nums text-bg-10">
              {formatTrackerCount(realtime.unsupportedEvents)}
            </span>
          </span>
        ) : null}
        {realtime?.reconciliationDrift != null ? (
          <span>
            Расхождение:{" "}
            <span
              className={cn(
                "tabular-nums",
                realtime.reconciliationDrift === 0 ? "text-success" : "text-warning",
              )}
            >
              {formatTrackerCount(realtime.reconciliationDrift)}
            </span>
          </span>
        ) : null}
        <span>
          Installs: <span className="tabular-nums text-bg-10">{formatInt(totals?.installs ?? 0)}</span>
        </span>
        <span>
          ROI: <span className="tabular-nums text-bg-10">{formatPercentValue(totals?.roi_pct)}</span>
        </span>
      </div>
      {data.attribution_note ? (
        <div className="mt-2 border-t border-[var(--hairline)] pt-2 text-[11px] text-bg-8">
          {data.attribution_note}
        </div>
      ) : null}
    </Card>
  );
}

// ─── Скелетон ─────────────────────────────────────────────────────────────────

function TrackerBlockSkeleton({ className }: { className?: string }) {
  return (
    <Card eyebrow="ADSET.PRO · LIVE" className={className}>
      <div className="grid grid-cols-2 gap-4 sm:grid-cols-3 xl:grid-cols-6" role="status" aria-label="Загрузка трекера">
        {Array.from({ length: 6 }).map((_, i) => (
          <div key={i} className="flex flex-col gap-1">
            <Skeleton height={11} width="50%" />
            <Skeleton height={16} width="70%" />
          </div>
        ))}
      </div>
    </Card>
  );
}

function QualityPill({
  quality,
  available,
}: {
  quality: string | null;
  available: boolean | null;
}) {
  const normalized = quality?.toLowerCase() ?? "";
  const healthy = ["live", "good", "healthy", "ok", "exact"].includes(normalized);
  const warning =
    available === false ||
    ["degraded", "partial", "stale", "drift", "incomplete"].includes(normalized);
  const label = available === false
    ? "Источник недоступен"
    : healthy
      ? "Данные согласованы"
      : warning
        ? "Требует внимания"
        : "Качество не подтверждено";
  return (
    <span
      className={cn(
        "rounded-full border px-2 py-1 font-display text-[9px] uppercase tracking-[0.08em]",
        healthy && "border-success/30 bg-success-bg text-success",
        warning && "border-warning/30 bg-warning-bg text-warning",
        !healthy && !warning && "border-[var(--hairline)] bg-bg-2 text-bg-9",
      )}
    >
      {label}
    </span>
  );
}
