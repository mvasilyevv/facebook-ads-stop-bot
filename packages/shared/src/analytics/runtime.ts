import type {
  AnalyticsDaypart,
  AnalyticsLiveBudgetSeries,
  AnalyticsPerformance,
} from "../api/types";
import { isSupportedCurrencyCode } from "../format/number";
import {
  isIncreasingTimestampRange,
  isRfc3339Timestamp,
} from "../runtime/rfc3339";

type JsonObject = Record<string, unknown>;
type Predicate = (value: unknown) => boolean;

const DECIMAL = /^-?\d+(?:\.\d+)?$/;
const CONTEXT_STATES = ["single", "mixed", "unknown"] as const;
const MONEY_METRIC_FIELDS = [
  "spend",
  "revenue",
  "cpc",
  "cost_per_registration",
  "cost_per_ftd",
  "roi_pct",
  "roas",
] as const;

function isObject(value: unknown): value is JsonObject {
  return typeof value === "object" && value !== null && !Array.isArray(value);
}

function isString(value: unknown): value is string {
  return typeof value === "string";
}

function isNonEmptyString(value: unknown): value is string {
  return isString(value) && value.length > 0;
}

function isTimestamp(value: unknown): value is string {
  return isRfc3339Timestamp(value);
}

function isDecimal(value: unknown): value is string {
  return isString(value) && DECIMAL.test(value);
}

function isCount(value: unknown): value is number {
  return typeof value === "number" && Number.isInteger(value) && value >= 0;
}

function isStringArray(value: unknown): value is string[] {
  return Array.isArray(value) && value.every(isString);
}

function isIanaTimezone(value: unknown): value is string {
  if (!isNonEmptyString(value)) return false;
  try {
    new Intl.DateTimeFormat("en-US", { timeZone: value }).format(0);
    return true;
  } catch {
    return false;
  }
}

function isContextState(
  value: unknown,
): value is (typeof CONTEXT_STATES)[number] {
  return CONTEXT_STATES.includes(
    value as (typeof CONTEXT_STATES)[number],
  );
}

function isScopeEvidence(value: unknown): value is JsonObject {
  if (!isObject(value)) return false;
  if (
    !isStringArray(value.account_ids) ||
    value.account_ids.some((accountId) => accountId.trim().length === 0) ||
    new Set(value.account_ids).size !== value.account_ids.length ||
    !isIanaTimezone(value.display_timezone) ||
    !isContextState(value.cabinet_timezone_state) ||
    !isStringArray(value.missing_timezone_account_ids) ||
    !isContextState(value.currency_state) ||
    !isStringArray(value.missing_currency_account_ids) ||
    !Object.hasOwn(value, "currency_observed_at") ||
    !isNullable(value.currency_observed_at, isTimestamp)
  ) {
    return false;
  }

  if (value.cabinet_timezone_state === "single") {
    if (!isIanaTimezone(value.cabinet_timezone) || value.account_ids.length === 0) {
      return false;
    }
  } else if (value.cabinet_timezone !== null) {
    return false;
  }
  if (
    value.cabinet_timezone_state !== "unknown" &&
    value.missing_timezone_account_ids.length > 0
  ) {
    return false;
  }

  if (value.currency_state === "single") {
    if (
      !isSupportedCurrencyCode(value.currency) ||
      !isTimestamp(value.currency_observed_at) ||
      value.account_ids.length === 0
    ) {
      return false;
    }
  } else if (value.currency !== null) {
    return false;
  }
  return !(
    value.currency_state !== "unknown" &&
    value.missing_currency_account_ids.length > 0
  );
}

function isOptional(value: unknown, predicate: Predicate): boolean {
  return value === undefined || value === null || predicate(value);
}

function isNullable(value: unknown, predicate: Predicate): boolean {
  return value === null || predicate(value);
}

function hasNullableCounts(
  value: JsonObject,
  fields: readonly string[],
): boolean {
  return fields.every(
    (field) => Object.hasOwn(value, field) && isNullable(value[field], isCount),
  );
}

function hasOptionalDecimals(
  value: JsonObject,
  fields: readonly string[],
): boolean {
  return fields.every((field) => isOptional(value[field], isDecimal));
}

function isMetrics(value: unknown): value is JsonObject {
  if (!isObject(value)) return false;
  return (
    Object.hasOwn(value, "spend") &&
    isNullable(value.spend, isDecimal) &&
    Object.hasOwn(value, "revenue") &&
    isNullable(value.revenue, isDecimal) &&
    hasNullableCounts(value, [
      "impressions",
      "clicks",
      "leads",
      "registrations",
      "ftds",
      "confirmed_deposits",
      "redeposits",
    ]) &&
    hasOptionalDecimals(value, [
      "cpc",
      "ctr_pct",
      "click_registration_cr_pct",
      "registration_ftd_cr_pct",
      "cost_per_registration",
      "cost_per_ftd",
      "roi_pct",
      "roas",
    ])
  );
}

function isLiveBudget(value: unknown): value is JsonObject {
  if (!isObject(value)) return false;
  return (
    ["click", "lead", "registration", "deposit", "mixed"].includes(
      String(value.stage),
    ) &&
    isOptional(value.base_unit, isDecimal) &&
    isOptional(value.stop_unit, isDecimal) &&
    isOptional(value.quantity, isCount) &&
    isDecimal(value.base_budget) &&
    isDecimal(value.stop_budget) &&
    isDecimal(value.base_delta) &&
    isDecimal(value.stop_delta)
  );
}

function isAnalyticsSource(
  value: unknown,
  source: "meta" | "tracker",
): boolean {
  if (!isObject(value)) return false;
  return (
    value.source === source &&
    ["good", "degraded", "missing", "unknown"].includes(String(value.status)) &&
    isOptional(value.last_event_at, isTimestamp) &&
    isOptional(value.lag_seconds, isCount) &&
    isCount(value.unmatched_events) &&
    isOptional(
      value.timezone_known,
      (candidate) => typeof candidate === "boolean",
    ) &&
    (value.missing_timezone_account_ids === undefined ||
      (Array.isArray(value.missing_timezone_account_ids) &&
        value.missing_timezone_account_ids.every(isString))) &&
    (value.issues === undefined ||
      (Array.isArray(value.issues) && value.issues.every(isString))) &&
    isOptional(value.note, isString)
  );
}

function isAnalyticsWindow(value: unknown): boolean {
  if (!isObject(value)) return false;
  const missingTimezoneAccountIds = value.missing_timezone_account_ids;
  const structurallyValid =
    isTimestamp(value.from_iso) &&
    isTimestamp(value.to_iso) &&
    isIncreasingTimestampRange(value.from_iso, value.to_iso) &&
    typeof value.is_live === "boolean" &&
    Object.hasOwn(value, "timezone") &&
    isNullable(value.timezone, isIanaTimezone) &&
    typeof value.timezone_known === "boolean" &&
    isContextState(value.timezone_state) &&
    isStringArray(missingTimezoneAccountIds) &&
    (value.issues === undefined ||
      (Array.isArray(value.issues) && value.issues.every(isString))) &&
    isOptional(value.cabinet_day_note, isString);
  if (!structurallyValid) return false;
  if (value.timezone_state === "single") {
    return (
      isIanaTimezone(value.timezone) &&
      value.timezone_known === true &&
      missingTimezoneAccountIds.length === 0
    );
  }
  if (value.timezone_state === "mixed") {
    return (
      value.timezone === null &&
      value.timezone_known === true &&
      missingTimezoneAccountIds.length === 0
    );
  }
  return value.timezone === null && value.timezone_known === false;
}

function isDataState(value: unknown): boolean {
  return ["ready", "empty", "partial", "stale", "unavailable"].includes(
    String(value),
  );
}

function isAnalyticsSectionMeta(value: JsonObject): boolean {
  const structurallyValid =
    isDataState(value.state) &&
    Object.hasOwn(value, "as_of") &&
    isNullable(value.as_of, isTimestamp) &&
    Object.hasOwn(value, "freshness_seconds") &&
    isNullable(value.freshness_seconds, isCount) &&
    Array.isArray(value.issues) &&
    value.issues.every(isString);
  if (!structurallyValid) return false;
  return (
    value.state !== "ready" ||
    (value.as_of !== null && value.freshness_seconds !== null)
  );
}

function isAnalyticsSources(value: unknown): boolean {
  return (
    isObject(value) &&
    isAnalyticsSource(value.meta, "meta") &&
    isAnalyticsSource(value.tracker, "tracker")
  );
}

function hasNoIssues(value: unknown): boolean {
  return (
    isObject(value) &&
    (value.issues === undefined ||
      (Array.isArray(value.issues) && value.issues.length === 0))
  );
}

function analyticsSourcesAreReady(value: unknown): boolean {
  if (!isObject(value)) return false;
  return [value.meta, value.tracker].every(
    (source) =>
      isObject(source) &&
      source.status === "good" &&
      isTimestamp(source.last_event_at) &&
      isCount(source.lag_seconds) &&
      hasNoIssues(source),
  );
}

function analyticsWindowIsReady(value: unknown): boolean {
  return (
    isObject(value) &&
    value.timezone_known === true &&
    value.timezone_state !== "unknown" &&
    hasNoIssues(value)
  );
}

function isPerformanceRow(value: unknown): boolean {
  if (!isMetrics(value)) return false;
  const structurallyValid =
    isNonEmptyString(value.id) &&
    isString(value.name) &&
    ["campaign", "adset", "ad"].includes(String(value.level)) &&
    typeof value.has_children === "boolean" &&
    Object.hasOwn(value, "cabinet_timezone") &&
    isNullable(value.cabinet_timezone, isIanaTimezone) &&
    typeof value.timezone_known === "boolean" &&
    isContextState(value.timezone_state) &&
    isOptional(value.fb_id, isString) &&
    isOptional(value.parent_id, isString) &&
    isOptional(value.parent_name, isString) &&
    isOptional(value.ad_account_id, isString) &&
    isOptional(value.offer_id, isString) &&
    isOptional(value.offer_code, isString) &&
    isDataState(value.state) &&
    Array.isArray(value.issues) &&
    value.issues.every(isString) &&
    isOptional(value.live_budget, isLiveBudget) &&
    isOptional(value.budget_unavailable_reason, isString);
  if (!structurallyValid) return false;
  if (value.timezone_state === "single") {
    return isIanaTimezone(value.cabinet_timezone) && value.timezone_known === true;
  }
  if (value.timezone_state === "mixed") {
    return value.cabinet_timezone === null && value.timezone_known === true;
  }
  return value.cabinet_timezone === null && value.timezone_known === false;
}

function isFilterOptions(value: unknown): boolean {
  if (!isObject(value)) return false;
  return ["accounts", "offers", "campaigns"].every((field) => {
    const options = value[field];
    return (
      options === undefined ||
      (Array.isArray(options) &&
        options.every(
          (option) =>
            isObject(option) &&
            isString(option.value) &&
            isString(option.label),
        ))
    );
  });
}

function isAnalyticsPerformance(value: unknown): value is AnalyticsPerformance {
  if (!isObject(value)) return false;
  const sources = value.sources;
  const scope = value.scope;
  const pagination = value.pagination;
  if (
    !isAnalyticsSectionMeta(value) ||
    !isScopeEvidence(scope) ||
    !isAnalyticsWindow(value.window) ||
    !isAnalyticsSources(sources) ||
    !isMetrics(value.totals) ||
    !isOptional(value.total_live_budget, isLiveBudget) ||
    !isOptional(value.total_budget_unavailable_reason, isString) ||
    !isObject(pagination) ||
    !isCount(pagination.page) ||
    pagination.page < 1 ||
    !isCount(pagination.page_size) ||
    pagination.page_size < 1 ||
    !isCount(pagination.total) ||
    !isCount(pagination.pages) ||
    !isFilterOptions(value.filter_options) ||
    !Array.isArray(value.rows) ||
    !value.rows.every(isPerformanceRow)
  ) {
    return false;
  }

  const expectedPages =
    pagination.total === 0
      ? 0
      : Math.ceil(pagination.total / pagination.page_size);
  const expectedRows =
    pagination.total === 0 || pagination.page > expectedPages
      ? 0
      : Math.min(
          pagination.page_size,
          pagination.total - (pagination.page - 1) * pagination.page_size,
        );
  if (
    pagination.pages !== expectedPages ||
    value.rows.length > pagination.page_size ||
    value.rows.length > pagination.total ||
    (pagination.total > 0 &&
      (pagination.page > pagination.pages ||
        expectedRows <= 0 ||
        value.rows.length !== expectedRows))
  ) {
    return false;
  }
  if (scope.currency_state !== "single") {
    if (
      value.state === "ready" ||
      !moneyMetricsAreNull(value.totals) ||
      value.total_live_budget != null ||
      !value.rows.every(
        (row) =>
          isObject(row) &&
          moneyMetricsAreNull(row) &&
          row.live_budget == null,
      )
    ) {
      return false;
    }
  }
  if (value.state === "empty") {
    return (
      value.rows.length === 0 &&
      pagination.total === 0 &&
      pagination.pages === 0
    );
  }
  if (value.state === "ready") {
    return (
      scope.currency_state === "single" &&
      Array.isArray(value.issues) &&
      value.issues.length === 0 &&
      analyticsSourcesAreReady(sources) &&
      analyticsWindowIsReady(value.window) &&
      pagination.total > 0 &&
      pagination.pages > 0 &&
      metricsAreComplete(value.totals) &&
      value.rows.every(
        (row) =>
          isObject(row) &&
          row.state === "ready" &&
          row.timezone_known === true &&
          Array.isArray(row.issues) &&
          row.issues.length === 0 &&
          metricsAreComplete(row),
      ) &&
      value.rows.length > 0
    );
  }
  return true;
}

function moneyMetricsAreNull(value: unknown): boolean {
  return (
    isObject(value) &&
    MONEY_METRIC_FIELDS.every((field) => value[field] == null)
  );
}

function metricsAreComplete(value: unknown): boolean {
  if (!isObject(value)) return false;
  return [
    "spend",
    "revenue",
    "impressions",
    "clicks",
    "leads",
    "registrations",
    "ftds",
    "confirmed_deposits",
    "redeposits",
  ].every((field) => value[field] !== null && value[field] !== undefined);
}

function isBudgetPoint(value: unknown): boolean {
  if (!isObject(value)) return false;
  return (
    isTimestamp(value.ts) &&
    Object.hasOwn(value, "actual") &&
    isNullable(value.actual, isDecimal) &&
    Object.hasOwn(value, "base") &&
    isNullable(value.base, isDecimal) &&
    Object.hasOwn(value, "stop") &&
    isNullable(value.stop, isDecimal) &&
    isCount(value.available_ads) &&
    isCount(value.unavailable_ads)
  );
}

function isAnalyticsLiveBudgetSeries(
  value: unknown,
): value is AnalyticsLiveBudgetSeries {
  if (
    !isObject(value) ||
    !isAnalyticsSectionMeta(value) ||
    !isScopeEvidence(value.scope) ||
    !isAnalyticsWindow(value.window) ||
    !isAnalyticsSources(value.sources) ||
    !Array.isArray(value.points) ||
    !value.points.every(isBudgetPoint)
  ) {
    return false;
  }
  if (
    value.scope.currency_state !== "single" &&
    (value.state === "ready" ||
      value.points.some(
        (point) =>
          isObject(point) &&
          (point.actual !== null ||
            point.base !== null ||
            point.stop !== null),
      ))
  ) {
    return false;
  }
  if (value.state === "empty") return value.points.length === 0;
  if (value.state === "ready") {
    return (
      Array.isArray(value.issues) &&
      value.issues.length === 0 &&
      analyticsSourcesAreReady(value.sources) &&
      analyticsWindowIsReady(value.window) &&
      value.points.length > 0 &&
      value.points.every(
        (point) =>
          isObject(point) &&
          point.actual !== null &&
          point.base !== null &&
          point.stop !== null &&
          point.unavailable_ads === 0,
      )
    );
  }
  return true;
}

function isDaypartCell(value: unknown): boolean {
  if (!isObject(value)) return false;
  return (
    isCount(value.weekday) &&
    Number(value.weekday) >= 1 &&
    Number(value.weekday) <= 7 &&
    isCount(value.hour) &&
    Number(value.hour) <= 23 &&
    Object.hasOwn(value, "clicks") &&
    isNullable(value.clicks, isCount) &&
    Object.hasOwn(value, "registrations") &&
    isNullable(value.registrations, isCount) &&
    Object.hasOwn(value, "ftds") &&
    isNullable(value.ftds, isCount)
  );
}

function isAnalyticsDaypart(value: unknown): value is AnalyticsDaypart {
  if (!isObject(value)) return false;
  const scope = value.scope;
  if (
    !isAnalyticsSectionMeta(value) ||
    !isScopeEvidence(scope) ||
    !isAnalyticsSources(value.sources) ||
    !isIanaTimezone(value.timezone) ||
    value.timezone !== scope.display_timezone ||
    !isTimestamp(value.from_iso) ||
    !isTimestamp(value.to_iso) ||
    !isIncreasingTimestampRange(value.from_iso, value.to_iso) ||
    !Array.isArray(value.cells) ||
    !value.cells.every(isDaypartCell)
  ) {
    return false;
  }
  const keys = value.cells.map((cell) => {
    const item = cell as JsonObject;
    return `${String(item.weekday)}:${String(item.hour)}`;
  });
  if (new Set(keys).size !== keys.length) return false;
  if (value.state === "empty") return value.cells.length === 0;
  if (value.state === "ready") {
    return (
      Array.isArray(value.issues) &&
      value.issues.length === 0 &&
      analyticsSourcesAreReady(value.sources) &&
      value.cells.length > 0 &&
      value.cells.every(
        (cell) =>
          isObject(cell) &&
          cell.clicks !== null &&
          cell.registrations !== null &&
          cell.ftds !== null,
      )
    );
  }
  return true;
}

export class AnalyticsPayloadError extends Error {
  readonly code = "ANALYTICS_PAYLOAD_INVALID";

  constructor() {
    super(
      "Ответ аналитики неполный или повреждён. Неподтверждённые значения скрыты; повторите запрос.",
    );
    this.name = "AnalyticsPayloadError";
  }
}

/** Runtime guard for GET /api/analytics/performance. */
export function normalizeAnalyticsPerformance(
  payload: unknown,
): AnalyticsPerformance {
  if (!isAnalyticsPerformance(payload)) throw new AnalyticsPayloadError();
  return payload;
}

/** Runtime guard for GET /api/analytics/live-budget. */
export function normalizeAnalyticsLiveBudgetSeries(
  payload: unknown,
): AnalyticsLiveBudgetSeries {
  if (!isAnalyticsLiveBudgetSeries(payload)) throw new AnalyticsPayloadError();
  return payload;
}

/** Runtime guard for sparse GET /api/analytics/daypart. */
export function normalizeAnalyticsDaypart(payload: unknown): AnalyticsDaypart {
  if (!isAnalyticsDaypart(payload)) throw new AnalyticsPayloadError();
  return payload;
}
