/**
 * RecentEvents — секция "Последние события" на Dashboard.
 * Источник: alerts из DashboardBatch (последние 24ч, лимит 12).
 * EventRow + ссылка "View all" → /history.
 */

import { useRouter } from "@tanstack/react-router";
import { SectionTitleRow } from "@/components/layout/PageHeader";
import { EventRow } from "@/components/domain/feed/EventRow";
import { Skeleton } from "@/components/ui/Skeleton";
import { EmptyState } from "@/components/ui/EmptyState";
import { ErrorState } from "@/components/ui/ErrorState";
import { Button } from "@/components/ui/Button";
import type { AlertEvent } from "@fb/shared";

interface RecentEventsProps {
  events: AlertEvent[];
  isLoading: boolean;
  isError: boolean;
  error?: unknown;
  onRetry?: () => void;
}

export function RecentEvents({
  events,
  isLoading,
  isError,
  error,
  onRetry,
}: RecentEventsProps) {
  const router = useRouter();

  return (
    <section aria-label="Последние события">
      <SectionTitleRow
        eyebrow="04 FEED"
        title="Последние события"
        action={
          <Button
            variant="ghost"
            size="sm"
            onClick={() => void router.navigate({ to: "/history" })}
          >
            Все события →
          </Button>
        }
        className="mb-4"
      />

      {isError ? (
        <ErrorState
          title="Не удалось загрузить события."
          error={error}
          onRetry={onRetry}
        />
      ) : isLoading ? (
        <div role="status" aria-label="Загрузка событий">
          {Array.from({ length: 6 }).map((_, i) => (
            <div
              key={i}
              className="flex items-center gap-4 py-3.5 px-4 border-b border-bg-3 last:border-b-0"
            >
              <Skeleton width={64} height={11} />
              <Skeleton width={116} height={22} />
              <Skeleton height={13} className="flex-1" />
              <Skeleton width={80} height={22} />
            </div>
          ))}
        </div>
      ) : events.length === 0 ? (
        <EmptyState
          title="Событий нет"
          description="Алертов за 24ч нет — правила работают."
        />
      ) : (
        <div className="border border-bg-5 bg-bg-1 px-4">
          {events.slice(0, 12).map((event) => (
            <EventRow key={event.id} event={event} />
          ))}
        </div>
      )}
    </section>
  );
}
