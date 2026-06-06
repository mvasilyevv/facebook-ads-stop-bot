/**
 * ActiveIncidents — карточка активных инцидентов на Dashboard.
 * Использует IncidentRow из domain/feed.
 * Источник: incidents из DashboardBatch.
 */

import { Card } from "@/components/ui/Card";
import { Skeleton } from "@/components/ui/Skeleton";
import { EmptyState } from "@/components/ui/EmptyState";
import { ErrorState } from "@/components/ui/ErrorState";
import { IncidentRow } from "@/components/domain/feed/IncidentRow";
import type { Incident } from "@fb/shared";

interface ActiveIncidentsProps {
  incidents: Incident[];
  isLoading: boolean;
  isError: boolean;
  error?: unknown;
  onRetry?: () => void;
  /** Клик по инциденту — открывает drawer объявления. */
  onIncidentClick?: (fbAdId: string) => void;
}

export function ActiveIncidents({
  incidents,
  isLoading,
  isError,
  error,
  onRetry,
  onIncidentClick,
}: ActiveIncidentsProps) {
  const stopCount = incidents.filter((i) => i.alert_state === "stop_sent").length;
  const warningCount = incidents.filter(
    (i) => i.alert_state === "warning_sent" || i.alert_state === "claimed",
  ).length;

  const metaParts: string[] = [];
  if (stopCount > 0) metaParts.push(`${stopCount} stop`);
  if (warningCount > 0) metaParts.push(`${warningCount} warning`);
  const metaText = isLoading ? "—" : metaParts.length > 0 ? metaParts.join(" · ") : "all clear";

  return (
    <Card
      eyebrow="03 INCIDENTS"
      title="Активные инциденты"
      meta={<span className="tabular-nums">{metaText}</span>}
      padded={false}
    >
      <div className="px-6 pb-2 pt-1">
        {isError ? (
          <ErrorState
            title="Не удалось загрузить инциденты."
            error={error}
            onRetry={onRetry}
          />
        ) : isLoading ? (
          // Skeleton 5 строк
          <div role="status" aria-label="Загрузка инцидентов">
            {Array.from({ length: 5 }).map((_, i) => (
              <div key={i} className="flex items-center gap-3 py-3.5 border-b border-bg-3 last:border-b-0">
                <Skeleton width={60} height={22} />
                <Skeleton height={13} className="flex-1" />
                <Skeleton width={56} height={22} />
                <Skeleton width={32} height={11} />
              </div>
            ))}
          </div>
        ) : incidents.length === 0 ? (
          // Editorial empty state — всё нормально
          <EmptyState
            title="Инцидентов нет"
            description="Алертов за 24ч нет — правила работают."
          />
        ) : (
          <div>
            {incidents.slice(0, 10).map((incident) => (
              <IncidentRow
                key={incident.internal_id}
                incident={incident}
                onClick={
                  onIncidentClick
                    ? () => onIncidentClick(incident.fb_ad_id)
                    : undefined
                }
              />
            ))}
          </div>
        )}
      </div>
    </Card>
  );
}
