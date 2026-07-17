/**
 * Timeline — вертикальный таймлайн событий объявления.
 *
 * Спека из макета (ads.html .timeline):
 *   - Вертикальная линия: left 7px от контейнера, bg-5, top 8px / bottom 8px.
 *   - Dot 14×14px: default bg-2 border-bg-7 | warning: warning-bg/border-warning |
 *     stop: danger-bg/border-danger | task: accent-bg/border-accent.
 *   - .timeline-time  — font-display 10.5px text-bg-9 tracking 0.04em.
 *   - .timeline-title — font-display 13px text-bg-11.
 *   - .timeline-meta  — font-display 11px text-bg-10.
 *   - rule-pills: bg-3 border-bg-6 px-1.5 py-0.5 text-[10.5px] tracking 0.04em.
 *
 * События сортируются DESC по времени (новейшие сверху).
 */

import { useMemo, type ReactNode } from "react";
import { cn } from "@/lib/utils/cn";
import { formatDisplayTime } from "@/lib/timezone";
import { formatRelativeTime, ruleCodeLabel } from "@fb/shared";

// ─── Тип события таймлайна ────────────────────────────────────────────────────

/** Тип точки на таймлайне — влияет на цвет dot. */
export type TimelineItemType = "warning" | "stop" | "task" | "default";

/**
 * TimelineItem — нормализованное событие для таймлайна.
 * Создаётся из AlertEvent / TaskQueueRow на уровне родительского компонента.
 */
export interface TimelineItem {
  /** Уникальный id (ad event id / task id). */
  id: string;
  /** ISO-строка времени события (UTC). */
  ts: string;
  /** Тип точки (определяет цвет dot). */
  type: TimelineItemType;
  /** Заголовок события: "STOP triggered", "Disable task enqueued". */
  title: string;
  /** Мета-строка: "requested by bot_auto_stop · attempt 1/5" */
  meta?: ReactNode;
  /**
   * Коды правил для пилл-бейджей.
   * Берётся из AlertEvent.matched_rule_codes (JSONB → string[]).
   */
  ruleCodes?: string[];
}

// ─── Стили dot по типу ────────────────────────────────────────────────────────

const DOT_CLASSES: Record<TimelineItemType, string> = {
  default: "bg-bg-2 border-bg-7",
  warning: "bg-warning-bg border-warning",
  stop: "bg-danger-bg border-danger",
  task: "bg-accent-bg border-accent",
};

// ─── Rule pill ────────────────────────────────────────────────────────────────

function RulePill({ code }: { code: string }) {
  return (
    <span
      className="inline-block bg-bg-2 border border-[var(--hairline)] rounded-[var(--radius-1)] px-1.5 py-0.5 mr-1 font-display text-[10.5px] tracking-[0.04em] text-bg-10"
      title={ruleCodeLabel(code, false)}
    >
      {ruleCodeLabel(code, true)}
    </span>
  );
}

// ─── TimelineRow ──────────────────────────────────────────────────────────────

interface TimelineRowProps {
  item: TimelineItem;
}

function TimelineRow({ item }: TimelineRowProps) {
  const { type, ts, title, meta, ruleCodes } = item;

  const timeLabel = useMemo(() => {
    const hms = formatDisplayTime(ts);
    const rel = formatRelativeTime(ts);
    return rel !== "—" ? `${hms} · ${rel}` : hms;
  }, [ts]);

  return (
    <div
      className="grid items-start gap-4 py-2.5 relative"
      style={{ gridTemplateColumns: "16px 1fr" }}
      data-timeline-type={type}
    >
      {/* Dot */}
      <div
        className={cn(
          "w-3.5 h-3.5 rounded-full border-2 relative z-10 mt-1 shrink-0",
          DOT_CLASSES[type],
        )}
        aria-hidden="true"
      />

      {/* Контент */}
      <div className="pb-1 min-w-0">
        {/* Время */}
        <div className="font-display text-[10.5px] text-bg-9 tracking-[0.04em] mb-1">
          {timeLabel}
        </div>

        {/* Заголовок */}
        <div className="font-display text-[13px] text-bg-11 mb-1 leading-tight">{title}</div>

        {/* Rule pills + meta */}
        {ruleCodes?.length || meta ? (
          <div className="font-display text-[11px] text-bg-10 leading-snug flex flex-wrap items-center gap-y-1">
            {ruleCodes?.map((code) => (
              <RulePill key={code} code={code} />
            ))}
            {meta ? <span>{meta}</span> : null}
          </div>
        ) : null}
      </div>
    </div>
  );
}

// ─── Timeline ─────────────────────────────────────────────────────────────────

interface TimelineProps {
  items: TimelineItem[];
  /** Дополнительные CSS-классы. */
  className?: string;
  /** Сообщение при пустом списке. */
  emptyMessage?: string;
}

/**
 * Timeline — вертикальный список событий.
 * Сортировка DESC по ts (новейшие сверху).
 *
 * @example
 * <Timeline items={[
 *   {
 *     id: "1",
 *     ts: "2026-06-06T14:32:18Z",
 *     type: "stop",
 *     title: "STOP triggered",
 *     ruleCodes: ["cpl_stop"],
 *     meta: `Open token f3a8c…921`,
 *   },
 * ]} />
 */
export function Timeline({ items, className, emptyMessage = "Событий нет" }: TimelineProps) {
  // Сортировка по времени: новейшие сверху (DESC)
  const sorted = useMemo(
    () =>
      [...items].sort((a, b) => {
        const ta = new Date(a.ts).getTime();
        const tb = new Date(b.ts).getTime();
        return tb - ta; // DESC
      }),
    [items],
  );

  if (sorted.length === 0) {
    return (
      <div className={cn("py-8 text-center font-display text-[12px] text-bg-8", className)}>
        {emptyMessage}
      </div>
    );
  }

  return (
    <div className={cn("relative", className)} role="list" aria-label="Таймлайн событий">
      {/* Вертикальная линия: left 7px (половина dot 14px), от top 8 до bottom 8 */}
      <div
        className="absolute bg-bg-5"
        aria-hidden="true"
        style={{ left: 7, top: 8, bottom: 8, width: 1 }}
      />

      {sorted.map((item) => (
        <div key={item.id} role="listitem">
          <TimelineRow item={item} />
        </div>
      ))}
    </div>
  );
}
