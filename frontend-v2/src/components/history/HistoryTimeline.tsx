/**
 * HistoryTimeline — лента событий (alert + task) в хронологическом порядке DESC.
 * Группировка по дням. Фильтр по типу события.
 */

import { useState } from "react";
import { AlertEventRow } from "@/components/domain/AlertEventRow";
import { TaskQueueRow } from "@/components/domain/TaskQueueRow";
import { Skeleton } from "@/components/ui/Skeleton";
import { EmptyState } from "@/components/ui/EmptyState";
import { ErrorState } from "@/components/ui/ErrorState";
import { cn } from "@/lib/utils/cn";
import { Clock } from "lucide-react";
import type { AlertEvent, TaskQueueRow as TaskQueueRowData } from "@/lib/types/api";

// Объединённый тип события (discriminated union по наличию stage).
type TimelineEvent = AlertEvent | TaskQueueRowData;

/** Тип-guard: AlertEvent имеет поле stage. */
function isAlertEvent(e: TimelineEvent): e is AlertEvent {
  return "stage" in e;
}

/** Получить ISO-дату из события. */
function getEventDate(e: TimelineEvent): string {
  const iso = isAlertEvent(e) ? e.created_at : (e.created_at ?? "");
  return iso ? iso.slice(0, 10) : "unknown";
}

/** Форматирует дату в заголовок группы: "MAY 28" / "TODAY" / "YESTERDAY". */
function formatGroupLabel(dateIso: string): string {
  const today = new Date().toISOString().slice(0, 10);
  const yesterday = new Date(Date.now() - 86400000).toISOString().slice(0, 10);
  if (dateIso === today) return "СЕГОДНЯ";
  if (dateIso === yesterday) return "ВЧЕРА";
  const d = new Date(dateIso);
  return d
    .toLocaleDateString("ru-RU", { day: "2-digit", month: "short", year: "numeric" })
    .toUpperCase();
}

type FilterType = "all" | "alert" | "task";

interface HistoryTimelineProps {
  events: TimelineEvent[] | undefined;
  isLoading: boolean;
  isError: boolean;
  error?: unknown;
  onRetry?: () => void;
  /** Клик по alert-событию. */
  onAlertClick?: (event: AlertEvent) => void;
}

export function HistoryTimeline({
  events,
  isLoading,
  isError,
  error,
  onRetry,
  onAlertClick,
}: HistoryTimelineProps) {
  const [filter, setFilter] = useState<FilterType>("all");

  if (isError) {
    return (
      <ErrorState
        title="Не удалось загрузить ленту событий."
        error={error}
        onRetry={onRetry}
      />
    );
  }

  if (isLoading) {
    return <TimelineSkeleton />;
  }

  // Фильтрация
  const filtered = (events ?? []).filter((e) => {
    if (filter === "alert") return isAlertEvent(e);
    if (filter === "task") return !isAlertEvent(e);
    return true;
  });

  // Группировка по дате
  const groups = new Map<string, TimelineEvent[]>();
  for (const e of filtered) {
    const d = getEventDate(e);
    if (!groups.has(d)) groups.set(d, []);
    groups.get(d)!.push(e);
  }

  return (
    <div>
      {/* Фильтр-таблы */}
      <FilterBar active={filter} onChange={setFilter} total={(events ?? []).length} filtered={filtered.length} />

      {filtered.length === 0 ? (
        <EmptyState
          icon={<Clock size={40} strokeWidth={1.25} aria-hidden="true" />}
          title="Событий за период нет."
          description="Попробуйте расширить диапазон дат или изменить фильтр типа."
          className="py-16"
        />
      ) : (
        <div className="space-y-8">
          {Array.from(groups.entries()).map(([date, groupEvents]) => (
            <section key={date} aria-label={`События ${date}`}>
              {/* Day-separator в стиле design spec */}
              <div className="flex items-center gap-4 mb-4">
                <span className="font-display text-[10px] uppercase tracking-[0.14em] text-bg-8 whitespace-nowrap">
                  {formatGroupLabel(date)}
                </span>
                <div className="flex-1 h-px bg-bg-5" aria-hidden="true" />
              </div>

              <div className="border border-bg-5 bg-bg-1">
                {groupEvents.map((e, idx) => (
                  <div key={isAlertEvent(e) ? e.id : (e as TaskQueueRowData).id} className={cn(idx > 0 && "")}>
                    {isAlertEvent(e) ? (
                      <div className="px-4">
                        <AlertEventRow
                          event={e}
                          onClick={onAlertClick ? () => onAlertClick(e) : undefined}
                        />
                      </div>
                    ) : (
                      <div className="px-4 py-1">
                        <TaskQueueRow task={e as TaskQueueRowData} />
                      </div>
                    )}
                  </div>
                ))}
              </div>
            </section>
          ))}
        </div>
      )}
    </div>
  );
}

/** Фильтр-бар типа событий. */
function FilterBar({
  active,
  onChange,
  total,
  filtered,
}: {
  active: FilterType;
  onChange: (v: FilterType) => void;
  total: number;
  filtered: number;
}) {
  const items: Array<{ key: FilterType; label: string }> = [
    { key: "all", label: "Все" },
    { key: "alert", label: "Алерты" },
    { key: "task", label: "Задачи" },
  ];

  return (
    <div className="flex items-center justify-between mb-5">
      <div className="flex items-center gap-1 border border-bg-5 p-0.5">
        {items.map((it) => (
          <button
            key={it.key}
            type="button"
            onClick={() => onChange(it.key)}
            className={cn(
              "h-7 px-3 font-display text-[11px] uppercase tracking-wider transition-colors",
              active === it.key
                ? "bg-bg-4 text-accent"
                : "text-bg-9 hover:text-bg-11 hover:bg-bg-2",
            )}
          >
            {it.label}
          </button>
        ))}
      </div>
      <span className="font-display text-[11px] text-bg-9 tracking-tight tabular-nums">
        {filtered !== total ? `${filtered} из ${total}` : `${total} событий`}
      </span>
    </div>
  );
}

/** Skeleton для ленты. */
function TimelineSkeleton() {
  return (
    <div className="space-y-6">
      {[0, 1, 2].map((g) => (
        <div key={g}>
          <Skeleton height={10} width="15%" className="mb-4" />
          <div className="border border-bg-5 bg-bg-1 p-4 space-y-3">
            {[0, 1, 2, 3].map((i) => (
              <Skeleton key={i} height={40} />
            ))}
          </div>
        </div>
      ))}
    </div>
  );
}
