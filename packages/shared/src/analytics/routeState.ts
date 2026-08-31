export type AnalyticsPeriod = "today" | "7d" | "30d" | "custom";
export type AnalyticsTab = "uploads" | "events";
export type AnalyticsPreset = "economy" | "funnel" | "delivery";
/**
 * Under-tab navigation for the "Заливы" tab. Each value mounts exactly one
 * heavy chart/table group so the analytics page never renders every graph at
 * once. `summary` carries no heavy chart — it is the cheap default landing
 * section (totals + source quality, both rendered above the section switch).
 */
export type AnalyticsSection = "summary" | "dynamics" | "funnel" | "results";
export type AnalyticsDirection = "asc" | "desc";
export type AnalyticsSort =
  | "name"
  | "spend"
  | "clicks"
  | "registrations"
  | "ftds"
  | "confirmed_deposits"
  | "revenue"
  | "base_delta";

export interface AnalyticsRouteSearch {
  tab: AnalyticsTab;
  period: AnalyticsPeriod;
  from_date?: string;
  to_date?: string;
  account_id?: string;
  offer_id?: string;
  campaign_id?: string;
  search?: string;
  section: AnalyticsSection;
  preset: AnalyticsPreset;
  sort: AnalyticsSort;
  direction: AnalyticsDirection;
  page: number;
  event_level?: string;
  task_result?: string;
}

const PERIODS = new Set<AnalyticsPeriod>(["today", "7d", "30d", "custom"]);
const SECTIONS = new Set<AnalyticsSection>(["summary", "dynamics", "funnel", "results"]);
const PRESETS = new Set<AnalyticsPreset>(["economy", "funnel", "delivery"]);
const SORTS = new Set<AnalyticsSort>([
  "name",
  "spend",
  "clicks",
  "registrations",
  "ftds",
  "confirmed_deposits",
  "revenue",
  "base_delta",
]);

/** Canonical URL state used by both web and Telegram Mini App analytics. */
export function parseAnalyticsRouteSearch(
  search: Record<string, unknown>,
): AnalyticsRouteSearch {
  return {
    tab: search.tab === "events" ? "events" : "uploads",
    period: member(search.period, PERIODS, "today"),
    from_date: isoDate(search.from_date),
    to_date: isoDate(search.to_date),
    account_id: nonEmptyString(search.account_id),
    offer_id: nonEmptyString(search.offer_id),
    campaign_id: nonEmptyString(search.campaign_id),
    search: nonEmptyString(search.search),
    section: member(search.section, SECTIONS, "summary"),
    preset: member(search.preset, PRESETS, "economy"),
    sort: member(search.sort, SORTS, "spend"),
    direction: search.direction === "asc" ? "asc" : "desc",
    page: positiveInteger(search.page),
    event_level: nonEmptyString(search.event_level),
    task_result: nonEmptyString(search.task_result),
  };
}

function member<T extends string>(
  value: unknown,
  allowed: ReadonlySet<T>,
  fallback: T,
): T {
  return typeof value === "string" && allowed.has(value as T)
    ? (value as T)
    : fallback;
}

function nonEmptyString(value: unknown): string | undefined {
  if (typeof value !== "string") return undefined;
  const normalized = value.trim();
  return normalized || undefined;
}

function isoDate(value: unknown): string | undefined {
  const normalized = nonEmptyString(value);
  if (!normalized || !/^\d{4}-\d{2}-\d{2}$/.test(normalized)) {
    return undefined;
  }
  const [year, month, day] = normalized.split("-").map(Number);
  return new Date(Date.UTC(year!, month! - 1, day!))
    .toISOString()
    .startsWith(normalized)
    ? normalized
    : undefined;
}

function positiveInteger(value: unknown): number {
  const parsed =
    typeof value === "number"
      ? value
      : typeof value === "string" && /^\d+$/.test(value)
        ? Number(value)
        : Number.NaN;
  return Number.isSafeInteger(parsed) && parsed > 0 ? parsed : 1;
}
