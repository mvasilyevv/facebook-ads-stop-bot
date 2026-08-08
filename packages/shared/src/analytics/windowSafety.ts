import type { AnalyticsPerformance } from "../api/types";
import type { DataState } from "../operator/contracts";

const UNKNOWN_TIMEZONE_ISSUE =
  "Часовой пояс кабинета неизвестен; границы суток оценочные";

type SafetyFields = {
  timezone?: unknown;
  timezone_known?: unknown;
  timezone_state?: unknown;
  issues?: unknown;
};

export interface AnalyticsWindowSafety {
  /** Exact single-cabinet zone, explicit unknown fallback, or null for mixed. */
  timezone: string | null;
  timezoneKnown: boolean;
  timezoneState: "single" | "mixed" | "unknown";
  state: Extract<DataState, "ready" | "partial">;
  issues: string[];
}

/** Only an explicit true plus a valid IANA zone marks cabinet-day totals exact. */
export function analyticsWindowSafety(
  window: Partial<AnalyticsPerformance["window"]> | undefined,
): AnalyticsWindowSafety {
  const fields = (window ?? {}) as AnalyticsPerformance["window"] &
    SafetyFields;
  const timezoneState =
    fields.timezone_state === "single" || fields.timezone_state === "mixed"
      ? fields.timezone_state
      : "unknown";
  const configured =
    typeof fields.timezone === "string" ? fields.timezone : null;
  const timezoneValid = configured !== null && isIanaTimeZone(configured);
  const timezoneKnown =
    fields.timezone_known === true &&
    ((timezoneState === "single" && timezoneValid) ||
      (timezoneState === "mixed" && fields.timezone === null));
  const issues = Array.isArray(fields.issues)
    ? fields.issues.filter(
        (value): value is string =>
          typeof value === "string" && value.length > 0,
      )
    : [];
  if (!timezoneKnown && !issues.includes(UNKNOWN_TIMEZONE_ISSUE)) {
    issues.unshift(UNKNOWN_TIMEZONE_ISSUE);
  }
  return {
    timezone:
      timezoneState === "mixed"
        ? null
        : timezoneValid && configured !== null
          ? configured
          : "UTC",
    timezoneKnown,
    timezoneState,
    state: timezoneKnown && issues.length === 0 ? "ready" : "partial",
    issues,
  };
}

function isIanaTimeZone(value: string): boolean {
  try {
    new Intl.DateTimeFormat("en-US", { timeZone: value }).format(0);
    return true;
  } catch {
    return false;
  }
}

export { UNKNOWN_TIMEZONE_ISSUE };
