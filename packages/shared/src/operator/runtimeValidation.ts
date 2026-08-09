/** Runtime validation for the operator API boundary.
 *
 * Generated OpenAPI types only protect compile time. Every web/TMA response is
 * still untrusted JSON and must be rejected before it can enter Query caches.
 */

import type { components } from "../api/generated";
import { isSupportedCurrencyCode } from "../format/number";
import {
  isIncreasingTimestampRange,
  isRfc3339Timestamp,
} from "../runtime/rfc3339";
import { safeOperatorAttentionHref } from "./attentionNavigation";

type GeneratedIncidentItem = components["schemas"]["OperatorIncidentItem"];
type GeneratedIncidentAck =
  components["schemas"]["OperatorIncidentAckResponse"];

const DATA_STATES = new Set([
  "ready",
  "empty",
  "partial",
  "stale",
  "unavailable",
]);
const SEVERITIES = new Set(["ok", "warning", "critical", "unknown"]);
const ACTION_STATES = new Set([
  "queued",
  "running",
  "confirmed",
  "failed",
  "cancelled",
  "unknown",
]);
const ACTION_KINDS = new Set([
  "pause",
  "activate",
  "scan",
  "create",
  "duplicate",
  "other",
]);
const ATTENTION_KINDS = new Set([
  "incident",
  "action",
  "source",
  "recommendation",
]);
const TARGET_KINDS = new Set(["ad", "campaign", "account", "system"]);
const WINDOWS = new Set(["today", "24h", "7d", "30d"]);
const FUNNEL_KEYS = new Set([
  "clicks",
  "registrations",
  "ftd",
  "confirmed_deposits",
]);
const CONTEXT_STATES = new Set(["single", "mixed", "unknown"]);
const INCIDENT_STATUSES: ReadonlySet<GeneratedIncidentItem["status"]> = new Set(
  ["open", "acknowledged", "executing", "resolved", "failed"],
);
const ACKNOWLEDGED_STATUS: GeneratedIncidentAck["status"] = "acknowledged";

export class OperatorPayloadValidationError extends Error {
  constructor(
    readonly endpoint: string,
    readonly field: string,
  ) {
    super(`Invalid operator API payload at ${endpoint}:${field}`);
    this.name = "OperatorPayloadValidationError";
  }
}

function fail(endpoint: string, field: string): never {
  throw new OperatorPayloadValidationError(endpoint, field);
}

function record(
  value: unknown,
  endpoint: string,
  field: string,
): Record<string, unknown> {
  if (typeof value !== "object" || value === null || Array.isArray(value)) {
    fail(endpoint, field);
  }
  return value as Record<string, unknown>;
}

function array(value: unknown, endpoint: string, field: string): unknown[] {
  if (!Array.isArray(value)) fail(endpoint, field);
  return value;
}

function string(value: unknown, endpoint: string, field: string): string {
  if (typeof value !== "string") fail(endpoint, field);
  return value;
}

function nullableString(value: unknown, endpoint: string, field: string): void {
  if (value !== null && typeof value !== "string") fail(endpoint, field);
}

function bool(value: unknown, endpoint: string, field: string): void {
  if (typeof value !== "boolean") fail(endpoint, field);
}

function nullableBool(value: unknown, endpoint: string, field: string): void {
  if (value !== null && typeof value !== "boolean") fail(endpoint, field);
}

function integer(
  value: unknown,
  endpoint: string,
  field: string,
  minimum = 0,
): void {
  if (!Number.isSafeInteger(value) || Number(value) < minimum)
    fail(endpoint, field);
}

function nullableInteger(
  value: unknown,
  endpoint: string,
  field: string,
): void {
  if (value !== null) integer(value, endpoint, field);
}

function isoDate(value: unknown, endpoint: string, field: string): void {
  if (!isRfc3339Timestamp(value)) fail(endpoint, field);
}

function nullableIsoDate(
  value: unknown,
  endpoint: string,
  field: string,
): void {
  if (value !== null) isoDate(value, endpoint, field);
}

function decimal(value: unknown, endpoint: string, field: string): void {
  if (typeof value !== "string" || !/^-?\d+(?:\.\d+)?$/.test(value))
    fail(endpoint, field);
}

function nullableDecimal(
  value: unknown,
  endpoint: string,
  field: string,
): void {
  if (value !== null) decimal(value, endpoint, field);
}

function enumValue(
  value: unknown,
  allowed: ReadonlySet<string>,
  endpoint: string,
  field: string,
): void {
  if (typeof value !== "string" || !allowed.has(value)) fail(endpoint, field);
}

function stringArray(value: unknown, endpoint: string, field: string): void {
  array(value, endpoint, field).forEach((item, index) =>
    string(item, endpoint, `${field}[${index}]`),
  );
}

function ianaTimezone(value: unknown, endpoint: string, field: string): void {
  const timezone = string(value, endpoint, field);
  try {
    new Intl.DateTimeFormat("en-US", { timeZone: timezone }).format(0);
  } catch {
    fail(endpoint, field);
  }
}

function supportedCurrency(
  value: unknown,
  endpoint: string,
  field: string,
): void {
  if (!isSupportedCurrencyCode(value)) fail(endpoint, field);
}

function contextEvidence(
  value: unknown,
  endpoint: string,
  field: string,
): Record<string, unknown> {
  const scope = record(value, endpoint, field);
  const accountIds = array(scope.account_ids, endpoint, `${field}.account_ids`);
  accountIds.forEach((item, index) => {
    const accountId = string(item, endpoint, `${field}.account_ids[${index}]`);
    if (!accountId.trim()) fail(endpoint, `${field}.account_ids[${index}]`);
  });
  if (new Set(accountIds).size !== accountIds.length) {
    fail(endpoint, `${field}.account_ids`);
  }
  ianaTimezone(scope.display_timezone, endpoint, `${field}.display_timezone`);
  nullableString(scope.cabinet_timezone, endpoint, `${field}.cabinet_timezone`);
  enumValue(
    scope.cabinet_timezone_state,
    CONTEXT_STATES,
    endpoint,
    `${field}.cabinet_timezone_state`,
  );
  stringArray(
    scope.missing_timezone_account_ids,
    endpoint,
    `${field}.missing_timezone_account_ids`,
  );
  nullableString(scope.currency, endpoint, `${field}.currency`);
  enumValue(
    scope.currency_state,
    CONTEXT_STATES,
    endpoint,
    `${field}.currency_state`,
  );
  stringArray(
    scope.missing_currency_account_ids,
    endpoint,
    `${field}.missing_currency_account_ids`,
  );
  nullableIsoDate(
    scope.currency_observed_at,
    endpoint,
    `${field}.currency_observed_at`,
  );

  if (scope.cabinet_timezone_state === "single") {
    ianaTimezone(scope.cabinet_timezone, endpoint, `${field}.cabinet_timezone`);
    if (accountIds.length === 0) fail(endpoint, `${field}.account_ids`);
  } else if (scope.cabinet_timezone !== null) {
    fail(endpoint, `${field}.cabinet_timezone`);
  }
  if (
    scope.cabinet_timezone_state !== "unknown" &&
    array(
      scope.missing_timezone_account_ids,
      endpoint,
      `${field}.missing_timezone_account_ids`,
    ).length > 0
  ) {
    fail(endpoint, `${field}.missing_timezone_account_ids`);
  }

  if (scope.currency_state === "single") {
    supportedCurrency(scope.currency, endpoint, `${field}.currency`);
    isoDate(
      scope.currency_observed_at,
      endpoint,
      `${field}.currency_observed_at`,
    );
    if (accountIds.length === 0) fail(endpoint, `${field}.account_ids`);
  } else if (scope.currency !== null) {
    fail(endpoint, `${field}.currency`);
  }
  if (
    scope.currency_state !== "unknown" &&
    array(
      scope.missing_currency_account_ids,
      endpoint,
      `${field}.missing_currency_account_ids`,
    ).length > 0
  ) {
    fail(endpoint, `${field}.missing_currency_account_ids`);
  }
  return scope;
}

function issue(value: unknown, endpoint: string, field: string): void {
  const item = record(value, endpoint, field);
  string(item.code, endpoint, `${field}.code`);
  string(item.title, endpoint, `${field}.title`);
  nullableString(item.detail, endpoint, `${field}.detail`);
  enumValue(item.severity, SEVERITIES, endpoint, `${field}.severity`);
  nullableString(item.correlation_id, endpoint, `${field}.correlation_id`);
}

function actionItem(value: unknown, endpoint: string, field: string): void {
  const item = record(value, endpoint, field);
  string(item.id, endpoint, `${field}.id`);
  string(item.public_id, endpoint, `${field}.public_id`);
  enumValue(item.kind, ACTION_KINDS, endpoint, `${field}.kind`);
  enumValue(item.state, ACTION_STATES, endpoint, `${field}.state`);
  string(item.title, endpoint, `${field}.title`);
  nullableString(item.target_label, endpoint, `${field}.target_label`);
  isoDate(item.requested_at, endpoint, `${field}.requested_at`);
  isoDate(item.updated_at, endpoint, `${field}.updated_at`);
  nullableString(item.requested_by, endpoint, `${field}.requested_by`);
  nullableString(item.reason, endpoint, `${field}.reason`);
  string(item.correlation_id, endpoint, `${field}.correlation_id`);
  nullableString(item.account_id, endpoint, `${field}.account_id`);
  nullableString(item.currency, endpoint, `${field}.currency`);
  if (item.currency !== null) {
    supportedCurrency(item.currency, endpoint, `${field}.currency`);
    if (item.currency !== "USD") fail(endpoint, `${field}.currency`);
  }
  nullableString(item.cabinet_timezone, endpoint, `${field}.cabinet_timezone`);
  if (item.cabinet_timezone !== null) {
    ianaTimezone(item.cabinet_timezone, endpoint, `${field}.cabinet_timezone`);
  }
  nullableIsoDate(
    item.account_context_observed_at,
    endpoint,
    `${field}.account_context_observed_at`,
  );
  stringArray(
    item.account_context_issues,
    endpoint,
    `${field}.account_context_issues`,
  );
}

function section(
  value: unknown,
  endpoint: string,
  field: string,
  validateData: (data: unknown, endpoint: string, field: string) => void,
  isEmptyData?: (data: Record<string, unknown>) => boolean,
): void {
  const candidate = record(value, endpoint, field);
  enumValue(candidate.state, DATA_STATES, endpoint, `${field}.state`);
  nullableIsoDate(candidate.as_of, endpoint, `${field}.as_of`);
  nullableInteger(
    candidate.freshness_seconds,
    endpoint,
    `${field}.freshness_seconds`,
  );
  stringArray(candidate.sources, endpoint, `${field}.sources`);
  const issues = array(candidate.issues, endpoint, `${field}.issues`);
  issues.forEach((item, index) =>
    issue(item, endpoint, `${field}.issues[${index}]`),
  );
  if (
    candidate.state === "ready" &&
    (candidate.data === null ||
      candidate.as_of === null ||
      candidate.freshness_seconds === null ||
      issues.length > 0)
  ) {
    fail(endpoint, `${field}.ready_evidence`);
  }
  if (candidate.data !== null) {
    validateData(candidate.data, endpoint, `${field}.data`);
    if (
      candidate.state === "empty" &&
      isEmptyData &&
      !isEmptyData(record(candidate.data, endpoint, `${field}.data`))
    ) {
      fail(endpoint, `${field}.empty_data`);
    }
    if (
      candidate.state === "ready" &&
      isEmptyData &&
      isEmptyData(record(candidate.data, endpoint, `${field}.data`))
    ) {
      fail(endpoint, `${field}.ready_data`);
    }
  }
}

function emptyItems(data: Record<string, unknown>): boolean {
  return Array.isArray(data.items) && data.items.length === 0;
}

function emptyEconomy(data: Record<string, unknown>): boolean {
  if (!Array.isArray(data.series) || data.series.length !== 0) return false;
  if (
    typeof data.totals !== "object" ||
    data.totals === null ||
    Array.isArray(data.totals)
  ) {
    return false;
  }
  return Object.values(data.totals).every((value) => value === null);
}

function emptyFunnel(data: Record<string, unknown>): boolean {
  return Array.isArray(data.stages) && data.stages.length === 0;
}

function emptyPortfolio(data: Record<string, unknown>): boolean {
  return (
    Array.isArray(data.currency_groups) && data.currency_groups.length === 0
  );
}

function attentionItem(value: unknown, endpoint: string, field: string): void {
  const item = record(value, endpoint, field);
  string(item.id, endpoint, `${field}.id`);
  enumValue(item.kind, ATTENTION_KINDS, endpoint, `${field}.kind`);
  enumValue(item.severity, SEVERITIES, endpoint, `${field}.severity`);
  string(item.title, endpoint, `${field}.title`);
  string(item.summary, endpoint, `${field}.summary`);
  nullableString(item.reason, endpoint, `${field}.reason`);
  isoDate(item.occurred_at, endpoint, `${field}.occurred_at`);
  const target = record(item.target, endpoint, `${field}.target`);
  enumValue(target.kind, TARGET_KINDS, endpoint, `${field}.target.kind`);
  nullableString(target.id, endpoint, `${field}.target.id`);
  nullableString(target.label, endpoint, `${field}.target.label`);
  if (item.action !== null) {
    const action = record(item.action, endpoint, `${field}.action`);
    string(action.label, endpoint, `${field}.action.label`);
    const href = string(action.href, endpoint, `${field}.action.href`);
    if (safeOperatorAttentionHref(href) === null) {
      fail(endpoint, `${field}.action.href`);
    }
  }
}

function attentionData(value: unknown, endpoint: string, field: string): void {
  const data = record(value, endpoint, field);
  array(data.items, endpoint, `${field}.items`).forEach((value, index) =>
    attentionItem(value, endpoint, `${field}.items[${index}]`),
  );
}

function economyData(value: unknown, endpoint: string, field: string): void {
  const data = record(value, endpoint, field);
  moneyTotals(data.totals, endpoint, `${field}.totals`);
  array(data.series, endpoint, `${field}.series`).forEach((value, index) => {
    const pointField = `${field}.series[${index}]`;
    const point = record(value, endpoint, pointField);
    isoDate(point.at, endpoint, `${pointField}.at`);
    nullableDecimal(point.actual, endpoint, `${pointField}.actual`);
    nullableDecimal(point.base, endpoint, `${pointField}.base`);
    nullableDecimal(point.stop, endpoint, `${pointField}.stop`);
  });
}

function moneyTotals(value: unknown, endpoint: string, field: string): void {
  const totals = record(value, endpoint, field);
  for (const key of ["spend", "base", "stop", "base_delta"] as const) {
    nullableDecimal(totals[key], endpoint, `${field}.${key}`);
  }
}

function hasKnownMoney(
  value: unknown,
  endpoint: string,
  field: string,
): boolean {
  const totals = record(value, endpoint, field);
  return ["spend", "base", "stop", "base_delta"].some(
    (key) => totals[key] !== null,
  );
}

function navigationAction(
  value: unknown,
  endpoint: string,
  field: string,
): void {
  const action = record(value, endpoint, field);
  string(action.label, endpoint, `${field}.label`);
  const href = string(action.href, endpoint, `${field}.href`);
  if (safeOperatorAttentionHref(href) === null) fail(endpoint, `${field}.href`);
}

function portfolioData(value: unknown, endpoint: string, field: string): void {
  const data = record(value, endpoint, field);
  const groups = array(
    data.currency_groups,
    endpoint,
    `${field}.currency_groups`,
  );
  const groupIds = new Set<string>();
  const cabinetIds = new Set<string>();

  groups.forEach((value, groupIndex) => {
    const groupField = `${field}.currency_groups[${groupIndex}]`;
    const group = record(value, endpoint, groupField);
    const groupId = string(group.id, endpoint, `${groupField}.id`);
    if (!groupId || groupIds.has(groupId)) fail(endpoint, `${groupField}.id`);
    groupIds.add(groupId);
    nullableString(group.currency, endpoint, `${groupField}.currency`);
    if (group.currency !== null) {
      supportedCurrency(group.currency, endpoint, `${groupField}.currency`);
      if (groupId !== group.currency) fail(endpoint, `${groupField}.id`);
    } else if (groupId !== "unknown") {
      fail(endpoint, `${groupField}.id`);
    }
    const dollarContext = group.currency === "USD";
    enumValue(group.state, DATA_STATES, endpoint, `${groupField}.state`);
    enumValue(group.severity, SEVERITIES, endpoint, `${groupField}.severity`);
    nullableIsoDate(group.as_of, endpoint, `${groupField}.as_of`);
    nullableInteger(
      group.freshness_seconds,
      endpoint,
      `${groupField}.freshness_seconds`,
    );
    moneyTotals(group.totals, endpoint, `${groupField}.totals`);
    if (
      !dollarContext &&
      (group.state === "ready" ||
        group.state === "empty" ||
        hasKnownMoney(group.totals, endpoint, `${groupField}.totals`))
    ) {
      fail(endpoint, `${groupField}.dollar_context`);
    }
    const cabinets = array(group.cabinets, endpoint, `${groupField}.cabinets`);
    if (cabinets.length === 0) fail(endpoint, `${groupField}.cabinets`);
    cabinets.forEach((value, cabinetIndex) => {
      const cabinetField = `${groupField}.cabinets[${cabinetIndex}]`;
      const cabinet = record(value, endpoint, cabinetField);
      const cabinetId = string(cabinet.id, endpoint, `${cabinetField}.id`);
      if (!cabinetId || cabinetIds.has(cabinetId)) {
        fail(endpoint, `${cabinetField}.id`);
      }
      cabinetIds.add(cabinetId);
      string(cabinet.name, endpoint, `${cabinetField}.name`);
      nullableString(cabinet.timezone, endpoint, `${cabinetField}.timezone`);
      if (cabinet.timezone !== null) {
        ianaTimezone(cabinet.timezone, endpoint, `${cabinetField}.timezone`);
      }
      nullableString(cabinet.currency, endpoint, `${cabinetField}.currency`);
      if (cabinet.currency !== group.currency) {
        fail(endpoint, `${cabinetField}.currency`);
      }
      enumValue(cabinet.state, DATA_STATES, endpoint, `${cabinetField}.state`);
      enumValue(
        cabinet.severity,
        SEVERITIES,
        endpoint,
        `${cabinetField}.severity`,
      );
      nullableIsoDate(cabinet.as_of, endpoint, `${cabinetField}.as_of`);
      nullableInteger(
        cabinet.freshness_seconds,
        endpoint,
        `${cabinetField}.freshness_seconds`,
      );
      if (cabinet.cabinet_day !== null) {
        const day = record(
          cabinet.cabinet_day,
          endpoint,
          `${cabinetField}.cabinet_day`,
        );
        isoDate(
          day.starts_at,
          endpoint,
          `${cabinetField}.cabinet_day.starts_at`,
        );
        isoDate(day.ends_at, endpoint, `${cabinetField}.cabinet_day.ends_at`);
        if (!isIncreasingTimestampRange(day.starts_at, day.ends_at)) {
          fail(endpoint, `${cabinetField}.cabinet_day`);
        }
      }
      moneyTotals(cabinet.totals, endpoint, `${cabinetField}.totals`);
      if (
        !dollarContext &&
        (cabinet.state === "ready" ||
          cabinet.state === "empty" ||
          hasKnownMoney(cabinet.totals, endpoint, `${cabinetField}.totals`))
      ) {
        fail(endpoint, `${cabinetField}.dollar_context`);
      }
      string(cabinet.risk_label, endpoint, `${cabinetField}.risk_label`);
      nullableString(
        cabinet.risk_reason,
        endpoint,
        `${cabinetField}.risk_reason`,
      );
      array(cabinet.issues, endpoint, `${cabinetField}.issues`).forEach(
        (item, issueIndex) =>
          issue(item, endpoint, `${cabinetField}.issues[${issueIndex}]`),
      );
      navigationAction(cabinet.action, endpoint, `${cabinetField}.action`);
      if (
        cabinet.state === "ready" &&
        (cabinet.as_of === null || cabinet.freshness_seconds === null)
      ) {
        fail(endpoint, `${cabinetField}.ready_evidence`);
      }
    });
  });
}

function funnelData(value: unknown, endpoint: string, field: string): void {
  const data = record(value, endpoint, field);
  array(data.stages, endpoint, `${field}.stages`).forEach((value, index) => {
    const stageField = `${field}.stages[${index}]`;
    const stage = record(value, endpoint, stageField);
    enumValue(stage.key, FUNNEL_KEYS, endpoint, `${stageField}.key`);
    string(stage.label, endpoint, `${stageField}.label`);
    nullableInteger(stage.count, endpoint, `${stageField}.count`);
    nullableDecimal(stage.conversion, endpoint, `${stageField}.conversion`);
    nullableDecimal(stage.cost, endpoint, `${stageField}.cost`);
  });
}

function actionsData(value: unknown, endpoint: string, field: string): void {
  const data = record(value, endpoint, field);
  array(data.items, endpoint, `${field}.items`).forEach((item, index) =>
    actionItem(item, endpoint, `${field}.items[${index}]`),
  );
}

function systemData(value: unknown, endpoint: string, field: string): void {
  const data = record(value, endpoint, field);
  enumValue(data.severity, SEVERITIES, endpoint, `${field}.severity`);
  nullableBool(
    data.monitoring_enabled,
    endpoint,
    `${field}.monitoring_enabled`,
  );
  nullableIsoDate(data.last_scan_at, endpoint, `${field}.last_scan_at`);
  nullableIsoDate(data.next_scan_at, endpoint, `${field}.next_scan_at`);
  array(data.workers, endpoint, `${field}.workers`).forEach((value, index) => {
    const workerField = `${field}.workers[${index}]`;
    const worker = record(value, endpoint, workerField);
    string(worker.id, endpoint, `${workerField}.id`);
    string(worker.label, endpoint, `${workerField}.label`);
    enumValue(worker.severity, SEVERITIES, endpoint, `${workerField}.severity`);
    string(worker.status, endpoint, `${workerField}.status`);
    nullableIsoDate(
      worker.last_activity_at,
      endpoint,
      `${workerField}.last_activity_at`,
    );
  });
}

function snapshot(value: unknown, endpoint: string): void {
  const root = record(value, endpoint, "$");
  const meta = record(root.meta, endpoint, "$.meta");
  string(meta.revision, endpoint, "$.meta.revision");
  integer(meta.sequence, endpoint, "$.meta.sequence");
  isoDate(meta.generated_at, endpoint, "$.meta.generated_at");
  ianaTimezone(meta.timezone, endpoint, "$.meta.timezone");
  nullableString(meta.cabinet_timezone, endpoint, "$.meta.cabinet_timezone");
  bool(meta.cabinet_timezone_known, endpoint, "$.meta.cabinet_timezone_known");
  enumValue(
    meta.cabinet_timezone_state,
    CONTEXT_STATES,
    endpoint,
    "$.meta.cabinet_timezone_state",
  );
  stringArray(
    meta.missing_timezone_account_ids,
    endpoint,
    "$.meta.missing_timezone_account_ids",
  );
  nullableString(meta.currency, endpoint, "$.meta.currency");
  enumValue(
    meta.currency_state,
    CONTEXT_STATES,
    endpoint,
    "$.meta.currency_state",
  );
  stringArray(
    meta.missing_currency_account_ids,
    endpoint,
    "$.meta.missing_currency_account_ids",
  );
  nullableIsoDate(
    meta.currency_observed_at,
    endpoint,
    "$.meta.currency_observed_at",
  );
  if (meta.cabinet_timezone_state === "single") {
    ianaTimezone(meta.cabinet_timezone, endpoint, "$.meta.cabinet_timezone");
  } else if (meta.cabinet_timezone !== null) {
    fail(endpoint, "$.meta.cabinet_timezone");
  }
  if (
    meta.cabinet_timezone_known !==
    (meta.cabinet_timezone_state !== "unknown")
  ) {
    fail(endpoint, "$.meta.cabinet_timezone_known");
  }
  if (
    meta.cabinet_timezone_state !== "unknown" &&
    array(
      meta.missing_timezone_account_ids,
      endpoint,
      "$.meta.missing_timezone_account_ids",
    ).length > 0
  ) {
    fail(endpoint, "$.meta.missing_timezone_account_ids");
  }
  if (meta.currency_state === "single") {
    supportedCurrency(meta.currency, endpoint, "$.meta.currency");
    isoDate(meta.currency_observed_at, endpoint, "$.meta.currency_observed_at");
  } else if (meta.currency !== null) {
    fail(endpoint, "$.meta.currency");
  }
  if (
    meta.currency_state !== "unknown" &&
    array(
      meta.missing_currency_account_ids,
      endpoint,
      "$.meta.missing_currency_account_ids",
    ).length > 0
  ) {
    fail(endpoint, "$.meta.missing_currency_account_ids");
  }
  enumValue(meta.window, WINDOWS, endpoint, "$.meta.window");
  const account = record(meta.account, endpoint, "$.meta.account");
  nullableString(account.id, endpoint, "$.meta.account.id");
  nullableString(account.name, endpoint, "$.meta.account.name");
  const day = record(meta.cabinet_day, endpoint, "$.meta.cabinet_day");
  isoDate(day.starts_at, endpoint, "$.meta.cabinet_day.starts_at");
  isoDate(day.ends_at, endpoint, "$.meta.cabinet_day.ends_at");
  if (!isIncreasingTimestampRange(day.starts_at, day.ends_at)) {
    fail(endpoint, "$.meta.cabinet_day");
  }
  section(root.attention, endpoint, "$.attention", attentionData, emptyItems);
  section(
    root.portfolio,
    endpoint,
    "$.portfolio",
    portfolioData,
    emptyPortfolio,
  );
  section(root.economy, endpoint, "$.economy", economyData, emptyEconomy);
  section(root.funnel, endpoint, "$.funnel", funnelData, emptyFunnel);
  section(root.actions, endpoint, "$.actions", actionsData, emptyItems);
  section(root.system, endpoint, "$.system", systemData);
  if (meta.currency_state !== "single" || meta.currency !== "USD") {
    const economy = record(root.economy, endpoint, "$.economy");
    const funnel = record(root.funnel, endpoint, "$.funnel");
    if (economy.state === "ready" || funnel.state === "ready") {
      fail(endpoint, "$.meta.currency_state");
    }
    if (economy.data !== null) {
      const economyData = record(economy.data, endpoint, "$.economy.data");
      const totals = record(
        economyData.totals,
        endpoint,
        "$.economy.data.totals",
      );
      if (
        ["spend", "base", "stop", "base_delta"].some(
          (key) => totals[key] !== null,
        ) ||
        array(economyData.series, endpoint, "$.economy.data.series").some(
          (point) => {
            const item = record(point, endpoint, "$.economy.data.series[*]");
            return [item.actual, item.base, item.stop].some(
              (amount) => amount !== null,
            );
          },
        )
      ) {
        fail(endpoint, "$.economy.data");
      }
    }
    if (
      funnel.data !== null &&
      array(
        record(funnel.data, endpoint, "$.funnel.data").stages,
        endpoint,
        "$.funnel.data.stages",
      ).some(
        (stage) =>
          record(stage, endpoint, "$.funnel.data.stages[*]").cost !== null,
      )
    ) {
      fail(endpoint, "$.funnel.data");
    }
  }
}

function actionsResponse(value: unknown, endpoint: string): void {
  const root = record(value, endpoint, "$");
  contextEvidence(root.scope, endpoint, "$.scope");
  enumValue(root.state, DATA_STATES, endpoint, "$.state");
  nullableIsoDate(root.as_of, endpoint, "$.as_of");
  nullableInteger(root.freshness_seconds, endpoint, "$.freshness_seconds");
  stringArray(root.sources, endpoint, "$.sources");
  const issues = array(root.issues, endpoint, "$.issues");
  issues.forEach((item, index) => issue(item, endpoint, `$.issues[${index}]`));
  const items = array(root.items, endpoint, "$.items");
  items.forEach((item, index) =>
    actionItem(item, endpoint, `$.items[${index}]`),
  );
  nullableInteger(root.next_cursor, endpoint, "$.next_cursor");
  if (
    (root.state === "empty" && items.length !== 0) ||
    (root.state === "ready" && items.length === 0)
  ) {
    fail(endpoint, "$.state_items");
  }
  if (
    root.state === "ready" &&
    (root.as_of === null ||
      root.freshness_seconds === null ||
      issues.length > 0)
  ) {
    fail(endpoint, "$.ready_evidence");
  }
  if (root.next_cursor !== null) {
    const lastItem = record(items.at(-1), endpoint, "$.items[last]");
    const lastId = Number(lastItem.id);
    if (!Number.isSafeInteger(lastId) || root.next_cursor !== lastId) {
      fail(endpoint, "$.next_cursor");
    }
  }
}

function adRow(value: unknown, endpoint: string, field: string): void {
  const row = record(value, endpoint, field);
  for (const key of [
    "id",
    "fb_ad_id",
    "name",
    "campaign_id",
    "campaign_name",
    "adset_id",
    "adset_name",
  ] as const) {
    string(row[key], endpoint, `${field}.${key}`);
  }
  nullableString(row.account_id, endpoint, `${field}.account_id`);
  nullableString(row.delivery_status, endpoint, `${field}.delivery_status`);
  enumValue(row.data_state, DATA_STATES, endpoint, `${field}.data_state`);
  if (row.data_state === "empty") fail(endpoint, `${field}.data_state`);
  enumValue(row.severity, SEVERITIES, endpoint, `${field}.severity`);
  nullableIsoDate(row.as_of, endpoint, `${field}.as_of`);
  const metrics = record(row.metrics, endpoint, `${field}.metrics`);
  nullableDecimal(metrics.spend, endpoint, `${field}.metrics.spend`);
  for (const key of [
    "impressions",
    "clicks",
    "registrations",
    "ftd",
    "confirmed_deposits",
  ] as const) {
    nullableInteger(metrics[key], endpoint, `${field}.metrics.${key}`);
  }
  nullableDecimal(metrics.cpc, endpoint, `${field}.metrics.cpc`);
  nullableDecimal(
    metrics.cost_per_registration,
    endpoint,
    `${field}.metrics.cost_per_registration`,
  );
  if (row.data_state === "ready" && row.as_of === null) {
    fail(endpoint, `${field}.ready_evidence`);
  }
  if (row.active_action !== null)
    actionItem(row.active_action, endpoint, `${field}.active_action`);
}

function adsResponse(value: unknown, endpoint: string): void {
  const root = record(value, endpoint, "$");
  const scope = contextEvidence(root.scope, endpoint, "$.scope");
  enumValue(root.state, DATA_STATES, endpoint, "$.state");
  nullableIsoDate(root.as_of, endpoint, "$.as_of");
  nullableInteger(root.freshness_seconds, endpoint, "$.freshness_seconds");
  stringArray(root.sources, endpoint, "$.sources");
  const issues = array(root.issues, endpoint, "$.issues");
  issues.forEach((item, index) => issue(item, endpoint, `$.issues[${index}]`));
  const rows = array(root.rows, endpoint, "$.rows");
  rows.forEach((row, index) => adRow(row, endpoint, `$.rows[${index}]`));
  integer(root.page, endpoint, "$.page", 1);
  integer(root.page_size, endpoint, "$.page_size", 1);
  integer(root.total, endpoint, "$.total");
  integer(root.pages, endpoint, "$.pages");
  const page = Number(root.page);
  const pageSize = Number(root.page_size);
  const total = Number(root.total);
  const pages = Number(root.pages);
  const expectedPages = total === 0 ? 0 : Math.ceil(total / pageSize);
  const expectedRows =
    total === 0 ? 0 : Math.min(pageSize, total - (page - 1) * pageSize);
  if (
    pages !== expectedPages ||
    rows.length > pageSize ||
    (total > 0 &&
      (page > pages || expectedRows <= 0 || rows.length !== expectedRows))
  ) {
    fail(endpoint, "$.pagination");
  }
  if (
    (root.state === "empty" &&
      (rows.length !== 0 || total !== 0 || pages !== 0)) ||
    (root.state === "ready" &&
      (rows.length === 0 ||
        total === 0 ||
        root.as_of === null ||
        root.freshness_seconds === null ||
        issues.length > 0 ||
        rows.some(
          (row) => record(row, endpoint, "$.rows[*]").data_state !== "ready",
        )))
  ) {
    fail(endpoint, "$.state_rows");
  }
  if (scope.currency_state !== "single" || scope.currency !== "USD") {
    if (root.state === "ready") fail(endpoint, "$.state");
    for (const rowValue of rows) {
      const row = record(rowValue, endpoint, "$.rows[*]");
      const metrics = record(row.metrics, endpoint, "$.rows[*].metrics");
      if (
        ["spend", "cpc", "cost_per_registration"].some(
          (field) => metrics[field] !== null,
        )
      ) {
        fail(endpoint, "$.rows[*].metrics");
      }
    }
  }
}

function commandResponse(value: unknown, endpoint: string): void {
  const root = record(value, endpoint, "$");
  integer(root.task_id, endpoint, "$.task_id", 1);
  string(root.public_id, endpoint, "$.public_id");
  enumValue(root.state, ACTION_STATES, endpoint, "$.state");
  bool(root.created, endpoint, "$.created");
  string(root.correlation_id, endpoint, "$.correlation_id");
}

function incidentItem(
  value: unknown,
  endpoint: string,
  field: string,
): Record<string, unknown> {
  const item = record(value, endpoint, field);
  string(item.id, endpoint, `${field}.id`);
  enumValue(item.severity, SEVERITIES, endpoint, `${field}.severity`);
  enumValue(item.status, INCIDENT_STATUSES, endpoint, `${field}.status`);
  string(item.title, endpoint, `${field}.title`);
  nullableString(item.summary, endpoint, `${field}.summary`);
  nullableString(item.reason, endpoint, `${field}.reason`);
  isoDate(item.occurred_at, endpoint, `${field}.occurred_at`);
  nullableString(item.account_id, endpoint, `${field}.account_id`);
  const target = record(item.target, endpoint, `${field}.target`);
  enumValue(target.kind, TARGET_KINDS, endpoint, `${field}.target.kind`);
  nullableString(target.id, endpoint, `${field}.target.id`);
  nullableString(target.label, endpoint, `${field}.target.label`);
  navigationAction(item.action, endpoint, `${field}.action`);
  bool(item.requires_usd_evidence, endpoint, `${field}.requires_usd_evidence`);
  return item;
}

function validateIncidentMoneyEvidence(
  state: unknown,
  scope: Record<string, unknown>,
  incidents: Record<string, unknown>[],
  endpoint: string,
): void {
  const usdConfirmed =
    scope.currency_state === "single" && scope.currency === "USD";
  const guarded = incidents.filter(
    (incident) => incident.requires_usd_evidence === true,
  );
  if (usdConfirmed || guarded.length === 0) return;
  if (state === "ready") fail(endpoint, "$.state_currency_evidence");
  for (const incident of guarded) {
    if (
      incident.title !== "Денежный сигнал требует проверки" ||
      incident.summary !==
        "Валюта кабинета не подтверждена. Денежные детали скрыты." ||
      incident.reason !== null
    ) {
      fail(endpoint, "$.incident_money_copy");
    }
  }
}

function incidentsResponse(value: unknown, endpoint: string): void {
  const root = record(value, endpoint, "$");
  enumValue(root.state, DATA_STATES, endpoint, "$.state");
  isoDate(root.as_of, endpoint, "$.as_of");
  integer(root.freshness_seconds, endpoint, "$.freshness_seconds");
  stringArray(root.sources, endpoint, "$.sources");
  const issues = array(root.issues, endpoint, "$.issues");
  issues.forEach((item, index) => issue(item, endpoint, `$.issues[${index}]`));
  const scope = contextEvidence(root.scope, endpoint, "$.scope");
  const items = array(root.items, endpoint, "$.items").map((item, index) =>
    incidentItem(item, endpoint, `$.items[${index}]`),
  );
  integer(root.page, endpoint, "$.page", 1);
  integer(root.page_size, endpoint, "$.page_size", 1);
  integer(root.total, endpoint, "$.total");
  integer(root.pages, endpoint, "$.pages");
  if (Number(root.page) > 10_000) fail(endpoint, "$.page");
  if (
    Number(root.page_size) < 10 ||
    Number(root.page_size) > 100 ||
    items.length > Number(root.page_size)
  ) {
    fail(endpoint, "$.page_size");
  }
  const expectedPages =
    Number(root.total) === 0
      ? 0
      : Math.ceil(Number(root.total) / Number(root.page_size));
  if (Number(root.pages) !== expectedPages) fail(endpoint, "$.pages");
  if (
    root.state === "empty" &&
    (Number(root.total) !== 0 || items.length > 0)
  ) {
    fail(endpoint, "$.empty_items");
  }
  if (Number(root.total) === 0 && root.state !== "empty") {
    fail(endpoint, "$.state_total");
  }
  if (root.state === "ready" && issues.length > 0) {
    fail(endpoint, "$.ready_evidence");
  }
  validateIncidentMoneyEvidence(root.state, scope, items, endpoint);
}

function incidentDetailResponse(value: unknown, endpoint: string): void {
  const root = record(value, endpoint, "$");
  enumValue(root.state, DATA_STATES, endpoint, "$.state");
  isoDate(root.as_of, endpoint, "$.as_of");
  integer(root.freshness_seconds, endpoint, "$.freshness_seconds");
  stringArray(root.sources, endpoint, "$.sources");
  const issues = array(root.issues, endpoint, "$.issues");
  issues.forEach((item, index) => issue(item, endpoint, `$.issues[${index}]`));
  ianaTimezone(root.timezone, endpoint, "$.timezone");
  bool(root.timezone_known, endpoint, "$.timezone_known");
  const scope = contextEvidence(root.scope, endpoint, "$.scope");
  const incident = incidentItem(root.incident, endpoint, "$.incident");
  if (root.state === "empty") fail(endpoint, "$.state_incident");
  if (
    root.state === "ready" &&
    (issues.length > 0 || root.timezone_known !== true)
  ) {
    fail(endpoint, "$.ready_evidence");
  }
  validateIncidentMoneyEvidence(root.state, scope, [incident], endpoint);
}

function incidentAckResponse(value: unknown, endpoint: string): void {
  const root = record(value, endpoint, "$");
  string(root.incident_id, endpoint, "$.incident_id");
  if (root.status !== ACKNOWLEDGED_STATUS) fail(endpoint, "$.status");
  isoDate(root.acknowledged_at, endpoint, "$.acknowledged_at");
  string(root.correlation_id, endpoint, "$.correlation_id");
}

function eventsResponse(value: unknown, endpoint: string): void {
  array(value, endpoint, "$").forEach((value, index) => {
    const field = `$[${index}]`;
    const item = record(value, endpoint, field);
    enumValue(
      item.event_type,
      new Set(["alert", "task"]),
      endpoint,
      `${field}.event_type`,
    );
    isoDate(item.ts, endpoint, `${field}.ts`);
    for (const key of [
      "fb_ad_id",
      "ad_name",
      "campaign_id",
      "campaign_name",
      "stage",
      "task_type",
      "task_status",
    ] as const) {
      nullableString(item[key], endpoint, `${field}.${key}`);
    }
    if (item.rule_codes !== null)
      stringArray(item.rule_codes, endpoint, `${field}.rule_codes`);
  });
}

/** Validate a decoded successful operator response and return it unchanged. */
export function validateOperatorPayload(
  endpoint: string,
  value: unknown,
): unknown {
  if (
    endpoint === "/api/operator/snapshot" ||
    /^\/api\/operator\/cabinets\/[^/]+\/snapshot$/.test(endpoint)
  ) {
    snapshot(value, endpoint);
  } else if (endpoint === "/api/operator/actions")
    actionsResponse(value, endpoint);
  else if (endpoint === "/api/operator/ads") adsResponse(value, endpoint);
  else if (endpoint === "/api/operator/incidents")
    incidentsResponse(value, endpoint);
  else if (endpoint === "/api/operator/events") eventsResponse(value, endpoint);
  else if (/^\/api\/operator\/ads\/[^/]+\/(?:pause|activate)$/.test(endpoint)) {
    commandResponse(value, endpoint);
  } else if (/^\/api\/operator\/incidents\/[^/]+\/ack$/.test(endpoint)) {
    incidentAckResponse(value, endpoint);
  } else if (/^\/api\/operator\/incidents\/[^/]+$/.test(endpoint)) {
    incidentDetailResponse(value, endpoint);
  } else if (endpoint.startsWith("/api/operator/")) {
    fail(endpoint, "$.unvalidated_endpoint");
  }
  return value;
}
