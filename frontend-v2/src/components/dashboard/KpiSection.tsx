/**
 * KpiSection — KPI-strip из 4 карточек поверх useDashboardStats.
 *
 * Маппинг (по brief'у Round 8.X, не по визуальному моку):
 *   ADS MONITORED   → total_ads_monitored (muted)
 *   IN WARNING      → ads_in_warning      (warning)
 *   IN STOP         → ads_in_stop         (danger)
 *   ACTIVE INCIDENTS→ active_incidents    (danger если >0, иначе info)
 *
 * Loading → 4 skeleton-карточки в том же strip-каркасе.
 * Error   → ErrorState с retry (общий с остальной страницей через onRetry).
 */

import { KPICard, KPIStrip } from "@/components/data/KPICard";
import { Skeleton } from "@/components/ui/Skeleton";
import { ErrorState } from "@/components/ui/ErrorState";
import { formatInt } from "@/lib/utils/format";
import type { DashboardStats } from "@/lib/types/api";

interface KpiSectionProps {
  stats: DashboardStats | undefined;
  isLoading: boolean;
  isError: boolean;
  error?: unknown;
  onRetry?: () => void;
}

export function KpiSection({ stats, isLoading, isError, error, onRetry }: KpiSectionProps) {
  if (isError) {
    return (
      <div className="mb-8">
        <ErrorState title="Не удалось загрузить overview." error={error} onRetry={onRetry} />
      </div>
    );
  }

  if (isLoading || !stats) {
    return (
      <div className="mb-8">
        <KPIStrip>
          {Array.from({ length: 4 }).map((_, i) => (
            <KpiSkeleton key={i} />
          ))}
        </KPIStrip>
      </div>
    );
  }

  const incidents = stats.active_incidents;

  return (
    <div className="mb-8">
      <KPIStrip>
        <KPICard
          variant="muted"
          label="Ads monitored"
          value={formatInt(stats.total_ads_monitored)}
          hint="today scan"
        />
        <KPICard
          variant="warning"
          label="In warning"
          value={formatInt(stats.ads_in_warning)}
          hint="now"
        />
        <KPICard
          variant="danger"
          label="In stop"
          value={formatInt(stats.ads_in_stop)}
          hint="now"
        />
        <KPICard
          variant={incidents > 0 ? "danger" : "info"}
          label="Active incidents"
          value={formatInt(incidents)}
          hint="open now"
        />
      </KPIStrip>
    </div>
  );
}

/** Скелет одной KPI-карточки — повторяет внутреннюю геометрию KPICard. */
function KpiSkeleton() {
  return (
    <div className="p-6 pb-7">
      <Skeleton width={88} height={10} className="mb-4" />
      <Skeleton width={120} height={44} className="mb-3" />
      <Skeleton width={64} height={11} />
    </div>
  );
}
