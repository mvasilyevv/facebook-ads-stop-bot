import { useMemo, useState, type FC } from "react";
import { Calendar, ChevronRight, Search } from "lucide-react";

import { RulePill } from "@/components/domain/ads/RulePill";
import { EmptyState } from "@/components/ui/EmptyState";
import { ErrorState } from "@/components/ui/ErrorState";
import { Skeleton } from "@/components/ui/Skeleton";
import { cn } from "@/lib/utils/cn";
import type { components } from "@fb/shared/api/generated";

const STAGE_DOT_COLOR: Record<string, string> = {
  warning: "var(--fsm-warning)",
  stop: "var(--fsm-stop)",
  claimed: "var(--fsm-claimed)",
  disabled: "var(--fsm-disabled)",
};

const SUCCESS_STATUSES = new Set(["SUCCEEDED", "SUCCESS", "DONE", "COMPLETED"]);
const FAILURE_STATUSES = new Set(["FAILED", "ERROR", "DEAD"]);

type HistoryTimelineItem = components["schemas"]["OperatorEventItem"];
type HistoryFilter = "all" | "alerts" | "actions" | "errors";

interface DisplayEvent {
  primary: HistoryTimelineItem;
  result?: HistoryTimelineItem;
  key: string;
}

function stageDotColor(item: DisplayEvent): string {
  if (item.result && FAILURE_STATUSES.has(item.result.task_status?.toUpperCase() ?? "")) {
    return "var(--color-danger)";
  }
  return (item.primary.stage && STAGE_DOT_COLOR[item.primary.stage]) ?? "var(--color-bg-9)";
}

function isSuccess(status: string | null | undefined): boolean {
  return SUCCESS_STATUSES.has(status?.toUpperCase() ?? "");
}

function isFailure(status: string | null | undefined): boolean {
  return FAILURE_STATUSES.has(status?.toUpperCase() ?? "");
}

function taskAction(item: HistoryTimelineItem): string {
  const type = item.task_type?.toLowerCase() ?? "";
  if (type.includes("enable") || type.includes("activate")) return "включение в Meta";
  if (type.includes("disable") || type.includes("pause")) return "отключение в Meta";
  if (type.includes("meta_api")) return "изменение в Meta";
  return "операция";
}

function displayTitle(item: DisplayEvent): string {
  const { primary, result } = item;
  const name = primary.ad_name ?? primary.fb_ad_id ?? "Объявление";

  if (primary.event_type === "alert") {
    const stage =
      primary.stage === "stop" ? "стоп" : primary.stage === "warning" ? "предупреждение" : "алерт";
    if (!result) return `${name}: ${stage} по правилу`;

    const seconds = Math.max(
      0,
      Math.round((new Date(result.ts).getTime() - new Date(primary.ts).getTime()) / 1000),
    );
    if (isFailure(result.task_status)) return `${name}: ${stage} → не удалось отключить`;
    if (isSuccess(result.task_status)) {
      return `${name}: ${stage} → отключено в Meta${seconds > 0 ? ` за ${seconds} с` : ""}`;
    }
    return `${name}: ${stage} → отключение выполняется`;
  }

  const action = taskAction(primary);
  if (isFailure(primary.task_status)) return `${name}: ${action} не выполнено`;
  if (isSuccess(primary.task_status)) return `${name}: ${action} выполнено`;
  return `${name}: ${action}`;
}

function relatedEvents(items: HistoryTimelineItem[]): DisplayEvent[] {
  const tasks = items.filter((item) => item.event_type === "task");
  const usedTasks = new Set<HistoryTimelineItem>();
  const display: DisplayEvent[] = [];

  for (const alert of items.filter((item) => item.event_type === "alert")) {
    const alertTs = new Date(alert.ts).getTime();
    const result = tasks
      .filter((task) => {
        if (usedTasks.has(task) || task.fb_ad_id !== alert.fb_ad_id) return false;
        const delta = new Date(task.ts).getTime() - alertTs;
        return delta >= -30_000 && delta <= 10 * 60_000;
      })
      .sort(
        (a, b) =>
          Math.abs(new Date(a.ts).getTime() - alertTs) -
          Math.abs(new Date(b.ts).getTime() - alertTs),
      )[0];
    if (result) usedTasks.add(result);
    display.push({ primary: alert, result, key: `alert-${alert.ts}-${alert.fb_ad_id ?? ""}` });
  }

  for (const task of tasks) {
    if (!usedTasks.has(task)) {
      display.push({
        primary: task,
        key: `task-${task.ts}-${task.fb_ad_id ?? task.task_type ?? ""}`,
      });
    }
  }

  return display.sort(
    (a, b) => new Date(b.primary.ts).getTime() - new Date(a.primary.ts).getTime(),
  );
}

function formatDayLabel(day: string, timeZone: string): string {
  try {
    const date = new Date(`${day}T12:00:00Z`);
    const today = new Date();
    const todayStr = zonedDateKey(today.toISOString(), timeZone);
    const yesterday = new Date(`${todayStr}T12:00:00Z`);
    yesterday.setUTCDate(yesterday.getUTCDate() - 1);
    const yesterdayStr = yesterday.toISOString().slice(0, 10);
    const formatted = date.toLocaleDateString("ru-RU", {
      day: "numeric",
      month: "long",
      year: date.getUTCFullYear() === today.getUTCFullYear() ? undefined : "numeric",
      timeZone: "UTC",
    });
    if (day === todayStr) return `СЕГОДНЯ · ${formatted.toUpperCase()}`;
    if (day === yesterdayStr) return `ВЧЕРА · ${formatted.toUpperCase()}`;
    return formatted.toUpperCase();
  } catch {
    return day;
  }
}

function formatEventTime(ts: string, timeZone: string): string {
  try {
    return new Date(ts).toLocaleTimeString("ru-RU", {
      hour: "2-digit",
      minute: "2-digit",
      timeZone,
    });
  } catch {
    return ts.slice(11, 16);
  }
}

function matchesFilter(item: DisplayEvent, filter: HistoryFilter): boolean {
  if (filter === "all") return true;
  if (filter === "alerts") return item.primary.event_type === "alert";
  if (filter === "actions") return item.primary.event_type === "task" || !!item.result;
  return isFailure(item.primary.task_status) || isFailure(item.result?.task_status);
}

function EventRow({
  item,
  timeZone,
  onAlertClick,
}: {
  item: DisplayEvent;
  timeZone: string;
  onAlertClick?: (item: HistoryTimelineItem) => void;
}) {
  const alert = item.primary.event_type === "alert" ? item.primary : null;
  const ruleCodes = alert?.rule_codes ?? [];
  const title = displayTitle(item);

  return (
    <div className="grid min-h-[52px] grid-cols-[auto_auto_minmax(0,1fr)_auto] items-center gap-3 border-b border-[var(--color-hairline)] px-4 py-2 sm:px-5">
      <span className="min-w-11 font-display text-[12px] tabular-nums text-bg-9">
        {formatEventTime(item.primary.ts, timeZone)}
      </span>
      <span
        aria-hidden="true"
        className="size-[7px] shrink-0 rounded-full"
        style={{ background: stageDotColor(item) }}
      />
      <div className="flex min-w-0 flex-col gap-1 sm:flex-row sm:items-center sm:gap-2">
        <span className="min-w-0 truncate text-[14px] font-medium text-bg-11" title={title}>
          {title}
        </span>
        {ruleCodes.length > 0 ? <RulePill code={ruleCodes[0]!} /> : null}
      </div>
      {alert && onAlertClick ? (
        <button
          type="button"
          onClick={() => onAlertClick(alert)}
          className="inline-flex min-h-11 items-center rounded-[var(--radius-1)] px-3 font-display text-[12px] text-accent transition-colors hover:bg-bg-3 focus-visible:outline-2 focus-visible:outline-offset-1 focus-visible:outline-accent"
          aria-label={`Подробнее о событии ${title}`}
        >
          Подробнее
        </button>
      ) : (
        <ChevronRight size={14} className="shrink-0 text-bg-9" aria-hidden="true" />
      )}
    </div>
  );
}

interface HistoryTimelineProps {
  items: HistoryTimelineItem[] | undefined;
  isLoading: boolean;
  error: unknown;
  onRetry?: () => void;
  onAlertClick?: (item: HistoryTimelineItem) => void;
  timeZone?: string;
}

export const HistoryTimeline: FC<HistoryTimelineProps> = ({
  items,
  isLoading,
  error,
  onRetry,
  onAlertClick,
  timeZone = "UTC",
}) => {
  if (isLoading) {
    return (
      <div className="space-y-2">
        {Array.from({ length: 6 }).map((_, index) => (
          <Skeleton key={index} className="h-11 w-full" />
        ))}
      </div>
    );
  }
  if (error) return <ErrorState error={error} onRetry={onRetry} />;
  if (!items || items.length === 0) {
    return (
      <EmptyState
        icon={<Calendar size={28} />}
        title="Событий нет"
        description="За выбранный период алертов и действий не найдено."
      />
    );
  }
  return (
    <GroupedTimeline
      items={items}
      timeZone={timeZone}
      onAlertClick={onAlertClick}
    />
  );
};

function GroupedTimeline({
  items,
  timeZone,
  onAlertClick,
}: {
  items: HistoryTimelineItem[];
  timeZone: string;
  onAlertClick?: (item: HistoryTimelineItem) => void;
}) {
  const [filter, setFilter] = useState<HistoryFilter>("all");
  const [search, setSearch] = useState("");

  const filtered = useMemo(() => {
    const query = search.trim().toLocaleLowerCase("ru");
    return relatedEvents(items).filter((item) => {
      if (!matchesFilter(item, filter)) return false;
      if (!query) return true;
      const haystack = [
        displayTitle(item),
        item.primary.campaign_name,
        ...(item.primary.rule_codes ?? []),
      ]
        .filter(Boolean)
        .join(" ")
        .toLocaleLowerCase("ru");
      return haystack.includes(query);
    });
  }, [filter, items, search]);

  const grouped = useMemo(() => {
    const map = new Map<string, DisplayEvent[]>();
    for (const item of filtered) {
      const day = zonedDateKey(item.primary.ts, timeZone);
      map.set(day, [...(map.get(day) ?? []), item]);
    }
    return map;
  }, [filtered, timeZone]);

  const days = [...grouped.keys()].sort((a, b) => b.localeCompare(a));

  return (
    <div className="overflow-hidden rounded-[var(--radius-3)] border border-[var(--color-hairline)] bg-bg-1">
      <div className="flex flex-col gap-3 border-b border-[var(--color-hairline)] p-3 sm:flex-row sm:items-center sm:justify-between">
        <div className="flex flex-wrap gap-1" role="group" aria-label="Фильтр истории">
          {(
            [
              ["all", "Все"],
              ["alerts", "Алерты"],
              ["actions", "Действия"],
              ["errors", "Ошибки"],
            ] as const
          ).map(([value, label]) => (
            <button
              key={value}
              type="button"
              aria-pressed={filter === value}
              onClick={() => setFilter(value)}
              className={cn(
                "min-h-11 rounded-[var(--radius-1)] px-3 text-[12px] transition-colors focus-visible:outline-2 focus-visible:outline-offset-1 focus-visible:outline-accent",
                filter === value
                  ? "bg-accent text-bg-0"
                  : "text-bg-10 hover:bg-bg-3 hover:text-bg-11",
              )}
            >
              {label}
            </button>
          ))}
        </div>
        <label className="relative min-w-0 sm:w-56">
          <span className="sr-only">Поиск по истории</span>
          <Search
            size={14}
            aria-hidden="true"
            className="pointer-events-none absolute left-2.5 top-1/2 -translate-y-1/2 text-bg-9"
          />
          <input
            value={search}
            onChange={(event) => setSearch(event.target.value)}
            placeholder="Объявление или правило"
            className="h-11 w-full rounded-[var(--radius-2)] border border-[var(--color-hairline)] bg-bg-2 pl-8 pr-3 text-[12px] text-bg-11 placeholder:text-bg-9 focus-visible:outline-2 focus-visible:outline-offset-1 focus-visible:outline-accent"
          />
        </label>
      </div>

      {filtered.length === 0 ? (
        <EmptyState title="Ничего не найдено" description="Измените фильтр или поисковый запрос." />
      ) : (
        days.map((day) => (
          <section key={day} aria-label={formatDayLabel(day, timeZone)}>
            <div className="border-b border-[var(--color-hairline)] px-5 pb-2 pt-3">
              <span className="font-display text-[12px] uppercase tracking-[0.12em] text-bg-9">
                {formatDayLabel(day, timeZone)}
              </span>
            </div>
            {(grouped.get(day) ?? []).map((item) => (
              <EventRow
                key={item.key}
                item={item}
                timeZone={timeZone}
                onAlertClick={onAlertClick}
              />
            ))}
          </section>
        ))
      )}
    </div>
  );
}

function zonedDateKey(value: string, timeZone: string): string {
  try {
    const parts = new Intl.DateTimeFormat("en-CA", {
      year: "numeric",
      month: "2-digit",
      day: "2-digit",
      timeZone,
    }).formatToParts(new Date(value));
    const lookup = new Map(parts.map((part) => [part.type, part.value]));
    const year = lookup.get("year");
    const month = lookup.get("month");
    const day = lookup.get("day");
    return year && month && day ? `${year}-${month}-${day}` : value.slice(0, 10);
  } catch {
    return value.slice(0, 10);
  }
}
