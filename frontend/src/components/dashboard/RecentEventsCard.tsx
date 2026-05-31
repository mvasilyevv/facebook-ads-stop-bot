/**
 * RecentEventsCard — лента последних alert_events (широкий блок Dashboard).
 *
 * Данные: recent_alerts из useDashboardBatch. Click по строке → /ads/$fbAdId.
 * Заголовок секции и "See all"-ссылка на /history рендерятся в родителе
 * (SectionTitle), сюда приходит готовый массив событий.
 *
 * Состояния: Loading (skeleton-строки), Error (ErrorState), Empty.
 */

import { Inbox } from "lucide-react";
import { Card } from "@/components/ui/Card";
import { Skeleton } from "@/components/ui/Skeleton";
import { ErrorState } from "@/components/ui/ErrorState";
import { EmptyState } from "@/components/ui/EmptyState";
import { AlertEventRow } from "@/components/domain/AlertEventRow";
import type { AlertEvent } from "@/lib/types/api";

interface RecentEventsCardProps {
  events: AlertEvent[];
  isLoading: boolean;
  isError: boolean;
  error?: unknown;
  onRetry?: () => void;
  onSelect: (fbAdId: string) => void;
}

export function RecentEventsCard({
  events,
  isLoading,
  isError,
  error,
  onRetry,
  onSelect,
}: RecentEventsCardProps) {
  // padded=false: AlertEventRow сам тянет -mx-4 hover; компенсируем px-4 py-2.
  return (
    <Card padded={false} className="px-4 py-2">
      {isError ? (
        <div className="py-2">
          <ErrorState title="Не удалось загрузить ленту событий." error={error} onRetry={onRetry} />
        </div>
      ) : isLoading ? (
        <div className="flex flex-col">
          {Array.from({ length: 6 }).map((_, i) => (
            <div key={i} className="grid grid-cols-[64px_116px_1fr_auto_24px] gap-4 items-center py-3.5">
              <Skeleton width={48} height={11} />
              <Skeleton width={96} height={18} />
              <Skeleton height={13} className="w-full" />
              <Skeleton width={80} height={16} />
              <span />
            </div>
          ))}
        </div>
      ) : events.length === 0 ? (
        <EmptyState
          icon={<Inbox size={36} strokeWidth={1.25} aria-hidden="true" />}
          title="Событий за последние 24ч нет"
          description="Лента алертов пуста — система спокойна."
        />
      ) : (
        <div className="flex flex-col">
          {events.map((event) => (
            <AlertEventRow
              key={event.id}
              event={event}
              onClick={() => event.fb_ad_id && onSelect(event.fb_ad_id)}
            />
          ))}
        </div>
      )}
    </Card>
  );
}
