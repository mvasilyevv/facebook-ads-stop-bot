/**
 * LiveTail — live-tail лента событий по объявлениям на Dashboard.
 *
 * Канон design_handoff/web-dashboard.jsx + dashboard-shared.jsx (LiveFeed):
 *   строка = время (mono) + state-dot (stop пульсирует) + ad-name (mono) +
 *   rule-pills + chevron. Новые строки въезжают сверху (.feed-row-new:
 *   fbSlideDown + accent-flash). Калм-empty: «Алертов за 24ч нет …».
 *
 * Данные — реальные AlertEvent (batch.recent_alerts, обновляется WS-инвалидацией).
 * Новые строки определяются диффом id'шников между рендерами (id не в прошлом
 * наборе → подсветка). Заморожен, когда observer выключен (paused → без подсветки).
 */

import { useEffect, useRef, useState } from "react";
import { ChevronRight } from "lucide-react";
import { PulseDot } from "@/components/data/PulseDot";
import { Pill } from "@/components/ui/Pill";
import { EmptyState } from "@/components/ui/EmptyState";
import { formatDisplayDateTime, formatDisplayTime } from "@/lib/timezone";
import { ruleCodeLabel } from "@fb/shared";
import type { AlertEvent } from "@fb/shared";
import type { MonitoringState } from "./monitoringState";

// Цвет state-dot по стадии алерта (канон fsm-*).
const STAGE_DOT: Record<string, string> = {
  warning: "var(--fsm-warning)",
  stop: "var(--fsm-stop)",
  claimed: "var(--fsm-claimed)",
  normal: "var(--fsm-normal)",
};

interface LiveTailProps {
  /** Реальные события (batch.recent_alerts). */
  events: AlertEvent[];
  /** Максимум строк. */
  max?: number;
  /** Заморожен ли поток (observer выключен) — отключает подсветку новых. */
  frozen?: boolean;
  /** Подтверждённое runtime-состояние — определяет честный empty-copy. */
  monitoringState?: MonitoringState;
  /** Клик по строке (открыть детали объявления). */
  onRow?: (event: AlertEvent) => void;
}

function FeedRow({
  event,
  fresh,
  onRow,
}: {
  event: AlertEvent;
  fresh: boolean;
  onRow?: (event: AlertEvent) => void;
}) {
  const stage = event.stage ?? "warning";
  const dot = STAGE_DOT[stage] ?? STAGE_DOT.warning!;
  const codes = (event.matched_rule_codes ?? []).slice(0, 3);
  const extra = (event.matched_rule_codes ?? []).length - codes.length;

  return (
    <button
      type="button"
      onClick={onRow ? () => onRow(event) : undefined}
      className={`grid w-full items-center gap-3 border-b border-[var(--hairline)] px-4 text-left last:border-b-0 transition-colors hover:bg-bg-2 focus-visible:bg-bg-2 ${
        fresh ? "feed-row-new" : ""
      }`}
      style={{ gridTemplateColumns: "auto auto 1fr auto auto", height: "var(--row-h)" }}
    >
      <span
        className="font-display tabular-nums text-bg-9"
        style={{ fontSize: "var(--row-fs)", minWidth: 62 }}
        title={formatDisplayDateTime(event.created_at)}
      >
        {formatDisplayTime(event.created_at)}
      </span>
      {stage === "stop" ? (
        <PulseDot size={7} color={dot} />
      ) : (
        <span
          aria-hidden="true"
          className="size-[7px] shrink-0 rounded-full"
          style={{ background: dot }}
        />
      )}
      <span className="truncate font-display text-bg-11" style={{ fontSize: "var(--row-fs)" }}>
        {event.ad_name ?? event.fb_ad_id ?? "—"}
      </span>
      <span className="flex shrink-0 items-center gap-1.5">
        {codes.map((c) => (
          <Pill key={c} className="text-[10.5px]" title={c}>
            {ruleCodeLabel(c, true)}
          </Pill>
        ))}
        {extra > 0 ? (
          <span className="font-display text-[10px] tracking-wider text-bg-9">+{extra}</span>
        ) : null}
      </span>
      <ChevronRight size={14} aria-hidden="true" className="text-bg-8" />
    </button>
  );
}

const EMPTY_COPY: Record<MonitoringState, { title: string; description: string }> = {
  healthy: {
    title: "Алертов за 24ч нет",
    description: "Мониторинг активен, пороги не были пересечены.",
  },
  paused: {
    title: "Мониторинг на паузе",
    description: "Новые события не поступают, пока Observer выключен.",
  },
  degraded: {
    title: "Поток событий неполный",
    description: "Часть контура недоступна — отсутствие алертов нельзя считать нормой.",
  },
  offline: {
    title: "Мониторинг недоступен",
    description: "Критические воркеры offline. Авто-disable сейчас не подтверждён.",
  },
  unknown: {
    title: "Нет подтверждения мониторинга",
    description: "Health/runtime ещё не получены. Нулевой поток не означает отсутствие проблем.",
  },
};

export function LiveTail({
  events,
  max = 8,
  frozen = false,
  monitoringState = "unknown",
  onRow,
}: LiveTailProps) {
  // Множество id из прошлого рендера — чтобы подсветить только реально новые строки.
  const seenRef = useRef<Set<string>>(new Set());
  const [freshIds, setFreshIds] = useState<Set<string>>(new Set());

  const rows = events.slice(0, max);

  // Список id текущих строк — стабильный ключ зависимости эффекта.
  const rowIdsKey = rows.map((e) => e.id).join(",");

  useEffect(() => {
    const currentIds = new Set(rows.map((e) => e.id));

    if (frozen) {
      // Заморожено — фиксируем текущие как «виденные», без подсветки.
      seenRef.current = currentIds;
      setFreshIds(new Set());
      return undefined;
    }

    const incomingFresh = new Set<string>();
    for (const id of currentIds) {
      if (!seenRef.current.has(id)) incomingFresh.add(id);
    }
    // Первый рендер (seen пуст) — не подсвечиваем всю пачку, только фиксируем.
    const firstRender = seenRef.current.size === 0;
    seenRef.current = currentIds;

    if (firstRender || incomingFresh.size === 0) {
      setFreshIds(new Set());
      return undefined;
    }

    setFreshIds(incomingFresh);
    const t = window.setTimeout(() => setFreshIds(new Set()), 1900);
    return () => window.clearTimeout(t);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [rowIdsKey, frozen]);

  if (rows.length === 0) {
    const copy = EMPTY_COPY[monitoringState];
    return <EmptyState title={copy.title} description={copy.description} />;
  }

  return (
    <div className="overflow-hidden">
      {rows.map((e) => (
        <FeedRow key={e.id} event={e} fresh={!frozen && freshIds.has(e.id)} onRow={onRow} />
      ))}
    </div>
  );
}
