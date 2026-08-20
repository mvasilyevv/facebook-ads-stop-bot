import { formatSpend } from "../format/number";
import { formatZonedDateTime } from "../format/time";
import { operatorActionReason } from "./actionLabels";
import type {
  OperatorActionItem,
  OperatorAttentionItem,
  OperatorSnapshot,
} from "./contracts";

/** Shared operator-ledger semantics used by both web and TMA shells. */
export function isActiveOperatorAction(item: OperatorActionItem): boolean {
  return item.state === "queued" || item.state === "running";
}

export function formatOperatorUsd(value: string | number | null): string {
  return formatSpend(value, "USD").replace(/^USD\s*/, "$");
}

export function formatOperatorScaleTick(value: number): string {
  return `$${Math.round(value)}`;
}

export function formatOperatorDateTime(
  value: string,
  timezone: string | null | undefined,
): string {
  if (!timezone) return "не подтверждено";
  const formatted = formatZonedDateTime(value, timezone);
  return formatted === "—" ? "не подтверждено" : formatted;
}

export function formatOperatorFreshness(seconds: number | null): string {
  if (seconds === null) return "не подтверждено";
  if (seconds < 60) return `${seconds} сек`;
  return `${Math.max(1, Math.round(seconds / 60))} мин`;
}

export function operatorReasonNoun(value: number): string {
  const remainder100 = value % 100;
  const remainder10 = value % 10;
  if (remainder100 >= 11 && remainder100 <= 14) return "причин";
  if (remainder10 === 1) return "причина";
  if (remainder10 >= 2 && remainder10 <= 4) return "причины";
  return "причин";
}

export function operatorSourceLabel(source: string): string {
  const labels: Record<string, string> = {
    meta: "Meta",
    offer_rules: "правила",
    meta_account_snapshot: "кабинеты",
    tracker: "Tracker",
    adsetpro: "Tracker",
    observer: "Observer",
    incidents: "инциденты",
    task_queue: "CommandService",
    worker_telemetry: "воркеры",
    cabinet_runtime: "акторы кабинетов",
    postgresql: "PostgreSQL",
  };
  return labels[source] ?? "Источник данных";
}

/** Keep operator-facing copy only when its money scope is confirmed as USD. */
export function operatorAttentionCopy(
  item: OperatorAttentionItem,
  usdScopeConfirmed: boolean,
): {
  title: string;
  summary: string | null;
  reason: string | null;
} {
  if (item.kind === "source") {
    return {
      title: "Источник требует проверки",
      summary: null,
      reason: null,
    };
  }
  if (item.kind === "action") {
    return {
      title: "Команда требует сверки",
      summary: null,
      reason: null,
    };
  }
  if (item.kind !== "incident" || !usdScopeConfirmed) {
    return {
      title: "Сигнал требует проверки",
      summary: null,
      reason: null,
    };
  }
  return {
    title: item.title.trim() || "Сигнал требует проверки",
    summary: item.summary.trim() || null,
    reason: item.reason?.trim() || null,
  };
}

/**
 * Cabinet identity as the operator reads it: без транспортного префикса `act_`.
 *
 * `act_` — приставка адреса Meta, а не часть идентичности кабинета: в списках она
 * одинакова у всех строк, удлиняет их и мешает сверить номер глазами.
 */
export function operatorCabinetDisplayName(raw: string | null | undefined): string {
  return String(raw ?? "").replace(/^act_/, "");
}

/** Cabinet routes use the cabinet row's timezone instead of the global display timezone. */
export function operatorCabinetTimezone(
  snapshot: OperatorSnapshot,
  cabinetId: string,
): string | null {
  const normalizedId = cabinetId.replace(/^act_/, "");
  const cabinet = snapshot.portfolio.data?.currency_groups
    .flatMap((group) => group.cabinets)
    .find((candidate) => candidate.id.replace(/^act_/, "") === normalizedId);
  if (cabinet?.timezone) return cabinet.timezone;
  if (
    snapshot.meta.cabinet_timezone_state === "single" &&
    snapshot.meta.cabinet_timezone
  ) {
    return snapshot.meta.cabinet_timezone;
  }
  return null;
}

/** Global timezone is only a formatting fallback, never cabinet evidence. */
export function operatorLedgerTimezone(
  snapshot: OperatorSnapshot,
  cabinetId?: string,
): string | null {
  if (!cabinetId) return snapshot.meta.timezone;
  return operatorCabinetTimezone(snapshot, cabinetId);
}

export interface CollapsedAttentionItem {
  item: OperatorAttentionItem;
  count: number;
}

/** Свести сигналы, неотличимые для оператора, в одну строку со счётчиком.
 *
 * Тексты источников и команд намеренно детерминированы, чтобы внутренности
 * бэкенда не попадали на экран. Побочный эффект — несколько разных проблем
 * выглядят одной фразой, повторённой подряд: три одинаковых карточки не
 * сообщают больше, чем одна, но занимают весь первый экран. Различие по
 * severity сохраняется: критичное и неподтверждённое не сливаются.
 */
export function collapseOperatorAttentionItems(
  items: OperatorAttentionItem[],
  usdScopeConfirmed: boolean,
): CollapsedAttentionItem[] {
  const collapsed: CollapsedAttentionItem[] = [];
  const seen = new Map<string, CollapsedAttentionItem>();
  for (const item of items) {
    const copy = operatorAttentionCopy(item, usdScopeConfirmed);
    const key = [
      item.severity,
      item.kind,
      copy.title,
      copy.summary ?? "",
      copy.reason ?? "",
      item.action?.label ?? "",
    ].join(" ");
    const known = seen.get(key);
    if (known) {
      known.count += 1;
      continue;
    }
    const entry: CollapsedAttentionItem = { item, count: 1 };
    seen.set(key, entry);
    collapsed.push(entry);
  }
  return collapsed;
}

export interface CollapsedOperatorAction {
  /** Самый свежий элемент группы — он же задаёт ссылку, ключ и время строки. */
  item: OperatorActionItem;
  count: number;
}

/**
 * Сворачивает ТОЛЬКО подряд идущие одинаковые записи ленты действий.
 *
 * Лента хронологическая: слияние по всему списку (как для сигналов внимания
 * в collapseOperatorAttentionItems) перемешало бы порядок событий, поэтому
 * сравниваются исключительно соседи.
 *
 * «Одинаковость» — это то, что оператор реально видит в строке: заголовок,
 * конкретная цель (иначе разные объявления с одним типом команды слились бы
 * в одну ложную запись), состояние и текст причины. Причина теперь приходит из
 * события, а не из состояния: два отказа с разными причинами — это две записи,
 * иначе свёртка спрячет вторую причину за «×2».
 *
 * Живёт в shared, а не в оболочке: web и TMA показывают одну и ту же ленту,
 * и две копии стратегии свёртки неизбежно разошлись бы.
 */
export function collapseConsecutiveOperatorActions(
  items: OperatorActionItem[],
): CollapsedOperatorAction[] {
  const groups: CollapsedOperatorAction[] = [];
  for (const item of items) {
    const group = groups.at(-1);
    if (group && isSameOperatorActionRow(group.item, item)) {
      group.count += 1;
      if (isFresherOperatorAction(item, group.item)) group.item = item;
      continue;
    }
    groups.push({ item, count: 1 });
  }
  return groups;
}

function isSameOperatorActionRow(
  a: OperatorActionItem,
  b: OperatorActionItem,
): boolean {
  return (
    a.title === b.title &&
    a.target_label === b.target_label &&
    a.state === b.state &&
    operatorActionReason(a) === operatorActionReason(b)
  );
}

function isFresherOperatorAction(
  candidate: OperatorActionItem,
  current: OperatorActionItem,
): boolean {
  return (
    new Date(candidate.updated_at).getTime() >
    new Date(current.updated_at).getTime()
  );
}
