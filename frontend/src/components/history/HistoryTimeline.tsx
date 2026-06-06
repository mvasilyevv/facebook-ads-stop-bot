/**
 * HistoryTimeline — таймлайн объединённой ленты alert+task за период.
 * Группирует события по датам (day-separator), использует Timeline из @/components/data/timeline.
 * Клик по строке alert открывает drill-down drawer (onEventClick).
 */

import { useMemo, type FC } from "react";
import { Calendar } from "lucide-react";
import { Timeline, type TimelineItem } from "@/components/data/timeline/Timeline";
import { Skeleton } from "@/components/ui/Skeleton";
import { ErrorState } from "@/components/ui/ErrorState";
import { EmptyState } from "@/components/ui/EmptyState";
import { formatDateTime } from "@fb/shared";
import type { HistoryTimelineItem } from "@fb/shared";

// ─── Маппинг event_type → TimelineItemType ────────────────────────────────────

function toTimelineType(item: HistoryTimelineItem): TimelineItem["type"] {
  if (item.event_type === "alert") {
    if (item.stage === "stop") return "stop";
    if (item.stage === "warning") return "warning";
    return "default";
  }
  if (item.event_type === "task") return "task";
  return "default";
}

/** Заголовок события для таймлайна. */
function toTitle(item: HistoryTimelineItem): string {
  if (item.event_type === "alert") {
    const stage = item.stage === "stop" ? "STOP" : item.stage === "warning" ? "WARNING" : "ALERT";
    return `${stage} · ${item.ad_name ?? item.fb_ad_id ?? "—"}`;
  }
  if (item.event_type === "task") {
    const type = item.task_type?.replace("_", " ").toUpperCase() ?? "TASK";
    const status = item.task_status ? ` · ${item.task_status}` : "";
    const name = item.ad_name ?? item.fb_ad_id;
    return `${type}${status}${name ? ` · ${name}` : ""}`;
  }
  return item.event_type;
}

/** Сгруппировать события по дате UTC (YYYY-MM-DD). */
function groupByDate(
  items: HistoryTimelineItem[],
): Map<string, HistoryTimelineItem[]> {
  const map = new Map<string, HistoryTimelineItem[]>();
  for (const item of items) {
    const day = item.ts.slice(0, 10);
    const arr = map.get(day) ?? [];
    arr.push(item);
    map.set(day, arr);
  }
  return map;
}

// ─── Конвертация в TimelineItem ───────────────────────────────────────────────

function toTimelineItem(
  item: HistoryTimelineItem,
  onAlertClick?: (item: HistoryTimelineItem) => void,
): TimelineItem {
  const type = toTimelineType(item);
  const isClickable = item.event_type === "alert" && !!onAlertClick;
  return {
    id: `${item.ts}_${item.fb_ad_id ?? item.event_type}`,
    ts: item.ts,
    type,
    title: toTitle(item),
    ruleCodes: item.rule_codes ?? undefined,
    meta: isClickable ? (
      <button
        type="button"
        onClick={() => onAlertClick?.(item)}
        className="text-[10.5px] text-accent hover:underline font-display underline-offset-2"
        aria-label={`Подробнее о событии ${toTitle(item)}`}
      >
        подробнее →
      </button>
    ) : item.campaign_name ? (
      <span>{item.campaign_name}</span>
    ) : undefined,
  };
}

// ─── Компонент ────────────────────────────────────────────────────────────────

interface HistoryTimelineProps {
  items: HistoryTimelineItem[] | undefined;
  isLoading: boolean;
  error: unknown;
  onRetry?: () => void;
  onAlertClick?: (item: HistoryTimelineItem) => void;
}

export const HistoryTimeline: FC<HistoryTimelineProps> = ({
  items,
  isLoading,
  error,
  onRetry,
  onAlertClick,
}) => {
  if (isLoading) {
    return (
      <div className="space-y-4">
        {Array.from({ length: 6 }).map((_, i) => (
          <Skeleton key={i} className="h-12 w-full" />
        ))}
      </div>
    );
  }

  if (error) {
    return <ErrorState error={error} onRetry={onRetry} />;
  }

  if (!items || items.length === 0) {
    return (
      <EmptyState
        icon={<Calendar size={28} />}
        title="Событий нет"
        description="За выбранный период алертов и задач не найдено."
      />
    );
  }

  return <GroupedTimeline items={items} onAlertClick={onAlertClick} />;
};

// ─── GroupedTimeline — с day-separator'ами ────────────────────────────────────

function GroupedTimeline({
  items,
  onAlertClick,
}: {
  items: HistoryTimelineItem[];
  onAlertClick?: (item: HistoryTimelineItem) => void;
}) {
  const grouped = useMemo(() => {
    // Сортировка DESC
    const sorted = [...items].sort(
      (a, b) => new Date(b.ts).getTime() - new Date(a.ts).getTime(),
    );
    return groupByDate(sorted);
  }, [items]);

  // Дни в порядке убывания
  const days = useMemo(
    () => [...grouped.keys()].sort((a, b) => b.localeCompare(a)),
    [grouped],
  );

  return (
    <div className="space-y-6">
      {days.map((day) => {
        const dayItems = grouped.get(day) ?? [];
        const timelineItems = dayItems.map((item) => toTimelineItem(item, onAlertClick));

        // Читаемая дата дня
        const dayLabel = formatDateTime(`${day}T00:00:00Z`).slice(0, 10);

        return (
          <div key={day}>
            {/* Day separator */}
            <div className="flex items-center gap-3 mb-3">
              <span className="font-display text-[10px] uppercase tracking-[0.1em] text-bg-8">
                {dayLabel}
              </span>
              <div className="flex-1 h-px bg-bg-4" aria-hidden="true" />
              <span className="font-display text-[10px] text-bg-7 tabular-nums">
                {dayItems.length}
              </span>
            </div>

            {/* События дня */}
            <Timeline
              items={timelineItems}
              emptyMessage="Нет событий за день"
            />
          </div>
        );
      })}
    </div>
  );
}
