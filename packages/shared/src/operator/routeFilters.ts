import type {
  OperatorActionState,
  OperatorAdsQuery,
  OperatorSeverity,
  OperatorSnapshot,
} from "./contracts";

export type OperatorAdsSort = NonNullable<OperatorAdsQuery["sort"]>;
export type OperatorAdsDirection = NonNullable<OperatorAdsQuery["direction"]>;

export interface OperatorAdsRouteSearch {
  q?: string;
  account_id?: string;
  severity?: OperatorSeverity;
  sort?: OperatorAdsSort;
  direction?: OperatorAdsDirection;
  page?: number;
}

export interface OperatorActionsRouteSearch {
  account_id?: string;
  state?: OperatorActionState;
}

export interface OperatorCabinetOption {
  value: string;
  label: string;
}

const ACTION_STATES = new Set<OperatorActionState>([
  "queued",
  "running",
  "confirmed",
  "failed",
  "cancelled",
  "unknown",
]);

const AD_SEVERITIES = new Set<OperatorSeverity>([
  "ok",
  "warning",
  "critical",
  "unknown",
]);

const AD_SORTS = new Set<OperatorAdsSort>([
  "name",
  "spend",
  "clicks",
  "registrations",
  "ftd",
  "updated",
]);

export function parseOperatorAdsRouteSearch(
  raw: Record<string, unknown>,
): OperatorAdsRouteSearch {
  return {
    q: boundedText(raw.q, 200),
    account_id: boundedText(raw.account_id, 64),
    severity: setMember(raw.severity, AD_SEVERITIES),
    sort: setMember(raw.sort, AD_SORTS),
    direction:
      raw.direction === "asc" || raw.direction === "desc"
        ? raw.direction
        : undefined,
    page: positiveInteger(raw.page),
  };
}

export function parseOperatorActionsRouteSearch(
  raw: Record<string, unknown>,
): OperatorActionsRouteSearch {
  return {
    account_id: boundedText(raw.account_id, 64),
    state: setMember(raw.state, ACTION_STATES),
  };
}

/**
 * Build filter options only from the typed global operator portfolio. Cabinet
 * IDs and labels stay server-owned; currency is deliberately not inferred.
 */
export function operatorCabinetOptions(
  snapshot: OperatorSnapshot | null | undefined,
): OperatorCabinetOption[] {
  const options =
    snapshot?.portfolio.data?.currency_groups.flatMap((group) =>
      group.cabinets.map((cabinet) => ({
        value: cabinet.id,
        label: cabinet.name,
      })),
    ) ?? [];
  const seen = new Set<string>();
  return options.filter((option) => {
    if (!option.value || seen.has(option.value)) return false;
    seen.add(option.value);
    return true;
  });
}

function boundedText(value: unknown, maxLength: number): string | undefined {
  if (typeof value !== "string") return undefined;
  const normalized = value.trim();
  return normalized ? normalized.slice(0, maxLength) : undefined;
}

function positiveInteger(value: unknown): number | undefined {
  const parsed = typeof value === "number" ? value : Number(value);
  return Number.isInteger(parsed) && parsed > 0 ? parsed : undefined;
}

function setMember<T extends string>(
  value: unknown,
  allowed: ReadonlySet<T>,
): T | undefined {
  return typeof value === "string" && allowed.has(value as T)
    ? (value as T)
    : undefined;
}
