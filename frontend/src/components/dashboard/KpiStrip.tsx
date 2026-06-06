/**
 * KpiStrip — горизонтальный ряд из 4 KPI-карточек Dashboard.
 *
 * Карточки (по макету dashboard.html):
 *   Active muted / Warning / Stop danger / Disabled success
 * Каждая: большое число + meta-строка + trend (из DashboardStats).
 */

import { Skeleton } from "@/components/ui/Skeleton";
import { cn } from "@/lib/utils/cn";
import { formatInt } from "@fb/shared";
import type { DashboardStats } from "@fb/shared";

// ─── Одна KPI-карточка ────────────────────────────────────────────────────────

type KpiVariant = "muted" | "warning" | "danger" | "success";

interface KpiCardProps {
  label: string;
  value: number | null | undefined;
  meta?: string;
  variant: KpiVariant;
}

const VARIANT_CLASSES: Record<KpiVariant, string> = {
  muted: "border-bg-5",
  warning: "border-warning/30",
  danger: "border-danger/30",
  success: "border-success/30",
};

const VALUE_CLASSES: Record<KpiVariant, string> = {
  muted: "text-bg-11",
  warning: "text-warning",
  danger: "text-danger",
  success: "text-success",
};

function KpiCard({ label, value, meta, variant }: KpiCardProps) {
  return (
    <div
      className={cn(
        "flex-1 border bg-bg-1 p-5 min-w-0",
        VARIANT_CLASSES[variant],
      )}
    >
      <div className="font-display text-[10px] tracking-[0.14em] uppercase text-bg-8 mb-3">
        {label}
      </div>
      <div
        className={cn(
          "font-display text-[40px] font-medium leading-none tabular-nums mb-1.5",
          VALUE_CLASSES[variant],
        )}
      >
        {value != null ? formatInt(value) : "—"}
      </div>
      {meta ? (
        <div className="font-display text-[11px] text-bg-9 tracking-wide">
          {meta}
        </div>
      ) : null}
    </div>
  );
}

// ─── Скелетон ─────────────────────────────────────────────────────────────────

export function KpiStripSkeleton() {
  return (
    <div className="flex gap-4" aria-label="Загрузка KPI" role="status">
      {Array.from({ length: 4 }).map((_, i) => (
        <div key={i} className="flex-1 border border-bg-5 bg-bg-1 p-5">
          <Skeleton height={10} width="60%" className="mb-3" />
          <Skeleton height={40} width="50%" className="mb-2" />
          <Skeleton height={11} width="70%" />
        </div>
      ))}
    </div>
  );
}

// ─── Основной компонент ───────────────────────────────────────────────────────

interface KpiStripProps {
  stats: DashboardStats;
}

export function KpiStrip({ stats }: KpiStripProps) {
  const totalActive =
    (stats.ads_in_normal ?? 0) +
    (stats.ads_in_warning ?? 0) +
    (stats.ads_in_stop ?? 0) +
    (stats.ads_in_claimed ?? 0);

  const pendingTasks = stats.pending_disable_tasks ?? 0;
  const failedTasks = stats.failed_tasks_24h ?? 0;

  return (
    <div className="flex gap-4" role="list" aria-label="Ключевые показатели">
      <KpiCard
        label="Активны"
        value={stats.ads_in_normal ?? null}
        meta={`всего ${formatInt(totalActive)} · pending ${formatInt(pendingTasks)}`}
        variant="muted"
      />
      <KpiCard
        label="Предупреждение"
        value={stats.ads_in_warning ?? null}
        meta="требуют внимания"
        variant="warning"
      />
      <KpiCard
        label="Стоп"
        value={stats.ads_in_stop ?? null}
        meta={failedTasks > 0 ? `${formatInt(failedTasks)} задач с ошибкой за 24ч` : "ожидают отключения"}
        variant="danger"
      />
      <KpiCard
        label="Отключено"
        value={stats.ads_in_disabled ?? null}
        meta={`${formatInt(stats.total_ads_monitored)} всего мониторится`}
        variant="success"
      />
    </div>
  );
}
