/**
 * IncidentsCard — список активных инцидентов (правая колонка Dashboard).
 *
 * Данные: recent_incidents из useDashboardBatch (graceful — секция batch'а
 * может быть пустой при partial-failure). Каждая строка кликабельна →
 * навигация на /ads/$fbAdId.
 *
 * Состояния: Loading (skeleton-строки), Error (ErrorState), Empty ("Всё чисто").
 */

import { ShieldCheck } from "lucide-react";
import { Card } from "@/components/ui/Card";
import { Eyebrow } from "@/components/layout/Eyebrow";
import { Skeleton } from "@/components/ui/Skeleton";
import { ErrorState } from "@/components/ui/ErrorState";
import { EmptyState } from "@/components/ui/EmptyState";
import { IncidentRow } from "@/components/domain/IncidentRow";
import type { Incident } from "@/lib/types/api";

interface IncidentsCardProps {
  incidents: Incident[];
  isLoading: boolean;
  isError: boolean;
  error?: unknown;
  onRetry?: () => void;
  /** Навигация по клику на строку. */
  onSelect: (fbAdId: string) => void;
}

export function IncidentsCard({
  incidents,
  isLoading,
  isError,
  error,
  onRetry,
  onSelect,
}: IncidentsCardProps) {
  return (
    <Card
      action={
        <span className="text-[11px] font-display text-bg-9 tracking-wider tabular-nums">
          {isLoading ? "—" : `${incidents.length} открытых`}
        </span>
      }
    >
      <div className="mb-5">
        <Eyebrow num="03">Live</Eyebrow>
        <h3 className="mt-1.5 font-display text-[13px] font-medium tracking-wider text-bg-11 m-0">
          Активные инциденты
        </h3>
      </div>

      {isError ? (
        <ErrorState title="Не удалось загрузить инциденты." error={error} onRetry={onRetry} />
      ) : isLoading ? (
        <div className="flex flex-col gap-1">
          {Array.from({ length: 6 }).map((_, i) => (
            <div key={i} className="flex items-center gap-3.5 py-3.5">
              <Skeleton width={56} height={22} />
              <Skeleton height={14} className="flex-1" />
              <Skeleton width={48} height={11} />
            </div>
          ))}
        </div>
      ) : incidents.length === 0 ? (
        <EmptyState
          icon={<ShieldCheck size={36} strokeWidth={1.25} aria-hidden="true" />}
          title="Всё чисто"
          description="Активных инцидентов нет — значит правила работают."
        />
      ) : (
        <div className="flex flex-col">
          {incidents.map((incident) => (
            <IncidentRow
              key={incident.fb_ad_id ?? incident.internal_id}
              incident={incident}
              onClick={() => incident.fb_ad_id && onSelect(incident.fb_ad_id)}
            />
          ))}
        </div>
      )}
    </Card>
  );
}
