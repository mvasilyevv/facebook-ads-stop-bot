/**
 * Переходный frontend-контракт event-driven данных AdSet.pro.
 *
 * OpenAPI обновляется отдельным backend-релизом, поэтому UI намеренно читает
 * optional-поля как из нового `tracker`, так и из временного плоского формата.
 * Старое `deposits` — это только FTD, а не подтверждённый депозит: подтвердить
 * его можно лишь новой проекцией registration + FTD для одного click_id.
 */

export interface TrackerRealtimeSnapshot {
  available: boolean | null;
  registrations: number | null;
  ftds: number | null;
  confirmedDeposits: number | null;
  redeposits: number | null;
  unmatchedEvents: number | null;
  lastEventAt: string | null;
  processingLagMs: number | null;
  dataQuality: string | null;
  backlog: number | null;
  duplicateEvents: number | null;
  unsupportedEvents: number | null;
  reconciliationDrift: number | null;
  installs: number | null;
  revenue: string | number | null;
  roiPct: string | number | null;
  attributionNote: string | null;
}

type UnknownRecord = Record<string, unknown>;

function record(value: unknown): UnknownRecord | null {
  return value != null && typeof value === "object" && !Array.isArray(value)
    ? (value as UnknownRecord)
    : null;
}

function number(value: unknown): number | null {
  if (typeof value === "number" && Number.isFinite(value)) return value;
  if (typeof value === "string" && value.trim() !== "") {
    const parsed = Number(value);
    return Number.isFinite(parsed) ? parsed : null;
  }
  return null;
}

function string(value: unknown): string | null {
  return typeof value === "string" && value.trim() !== "" ? value : null;
}

function bool(value: unknown): boolean | null {
  return typeof value === "boolean" ? value : null;
}

function firstNumber(...values: unknown[]): number | null {
  for (const value of values) {
    const parsed = number(value);
    if (parsed != null) return parsed;
  }
  return null;
}

function firstString(...values: unknown[]): string | null {
  for (const value of values) {
    const parsed = string(value);
    if (parsed != null) return parsed;
  }
  return null;
}

/**
 * Извлекает tracker telemetry из:
 * - нового `{ tracker: { totals, ...telemetry } }`;
 * - `{ stats: { tracker: ... } }` у batch-ответа;
 * - самого tracker-объекта;
 * - временных плоских `tracker_*` полей.
 */
export function readTrackerRealtime(source: unknown): TrackerRealtimeSnapshot | null {
  const root = record(source);
  if (!root) return null;

  const stats = record(root.stats);
  const nested = record(root.tracker) ?? record(stats?.tracker);
  const rootTotals = record(root.totals);
  const looksLikeTracker =
    "available" in root ||
    "ftds" in root ||
    "confirmed_deposits" in root ||
    "unmatched_events" in root ||
    Boolean(
      rootTotals &&
        ("ftds" in rootTotals ||
          "confirmed_deposits" in rootTotals ||
          "redeposits" in rootTotals),
    );
  const tracker = nested ?? (looksLikeTracker ? root : null);
  const totals = record(tracker?.totals) ?? tracker;

  // Плоский fallback читаем только из корня: это позволяет пережить короткий
  // rollout между backend-схемой и OpenAPI, не смешивая Meta metrics.
  const flat = root;

  const snapshot: TrackerRealtimeSnapshot = {
    available: bool(tracker?.available ?? flat.tracker_available),
    registrations: firstNumber(
      totals?.registrations,
      tracker?.registrations,
      flat.tracker_registrations,
    ),
    // Legacy totals.deposits = accepted FTD. В confirmedDeposits его не копируем.
    ftds: firstNumber(totals?.ftds, tracker?.ftds, flat.tracker_ftds, totals?.deposits),
    confirmedDeposits: firstNumber(
      totals?.confirmed_deposits,
      tracker?.confirmed_deposits,
      flat.tracker_confirmed_deposits,
    ),
    redeposits: firstNumber(
      totals?.redeposits,
      tracker?.redeposits,
      flat.tracker_redeposits,
    ),
    unmatchedEvents: firstNumber(tracker?.unmatched_events, flat.tracker_unmatched_events),
    lastEventAt: firstString(tracker?.last_event_at, flat.tracker_last_event_at),
    processingLagMs: firstNumber(
      tracker?.processing_lag_ms,
      flat.tracker_processing_lag_ms,
    ),
    dataQuality: firstString(tracker?.data_quality, flat.tracker_data_quality),
    backlog: firstNumber(tracker?.backlog, flat.tracker_backlog),
    duplicateEvents: firstNumber(
      tracker?.duplicate_events,
      flat.tracker_duplicate_events,
    ),
    unsupportedEvents: firstNumber(
      tracker?.unsupported_events,
      flat.tracker_unsupported_events,
    ),
    reconciliationDrift: firstNumber(
      tracker?.reconciliation_drift,
      flat.tracker_reconciliation_drift,
    ),
    installs: firstNumber(totals?.installs, tracker?.installs, flat.tracker_installs),
    revenue:
      (totals?.revenue as string | number | null | undefined) ??
      (tracker?.revenue as string | number | null | undefined) ??
      (flat.tracker_revenue as string | number | null | undefined) ??
      null,
    roiPct:
      (totals?.roi_pct as string | number | null | undefined) ??
      (tracker?.roi_pct as string | number | null | undefined) ??
      (flat.tracker_roi_pct as string | number | null | undefined) ??
      null,
    attributionNote: firstString(
      tracker?.attribution_note,
      flat.tracker_attribution_note,
    ),
  };

  const hasAnyValue = Object.values(snapshot).some((value) => value != null);
  return tracker || hasAnyValue ? snapshot : null;
}

export function formatTrackerCount(value: number | null): string {
  return value == null ? "—" : value.toLocaleString("ru-RU");
}

export function formatTrackerLag(value: number | null): string {
  if (value == null) return "—";
  if (value < 1_000) return `${Math.round(value)} мс`;
  if (value < 60_000) return `${(value / 1_000).toFixed(value < 10_000 ? 1 : 0)} с`;
  return `${Math.floor(value / 60_000)} мин`;
}

export function formatTrackerTimestamp(value: string | null): string {
  if (!value) return "—";
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) return value;
  return date.toLocaleString("ru-RU", {
    day: "2-digit",
    month: "short",
    hour: "2-digit",
    minute: "2-digit",
  });
}
