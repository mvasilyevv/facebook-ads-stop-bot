import type {
  DataState,
  OperatorIncidentItem,
  OperatorIncidentsQuery,
  OperatorIncidentStatus,
  OperatorScopeEvidence,
  OperatorSeverity,
} from "./contracts";

export interface OperatorIncidentsRouteSearch {
  account_id?: string;
  severity?: OperatorSeverity;
  status?: OperatorIncidentStatus;
}

const INCIDENT_SEVERITIES = new Set<OperatorSeverity>([
  "ok",
  "warning",
  "critical",
  "unknown",
]);

const INCIDENT_STATUSES = new Set<OperatorIncidentStatus>([
  "open",
  "acknowledged",
  "executing",
  "resolved",
  "failed",
]);

export const OPERATOR_INCIDENT_STATUS_LABEL: Record<
  OperatorIncidentStatus,
  string
> = {
  open: "Открыт",
  acknowledged: "Принят",
  executing: "Действие выполняется",
  resolved: "Разрешён",
  failed: "Завершён с ошибкой",
};

export function parseOperatorIncidentsRouteSearch(
  raw: Record<string, unknown>,
): OperatorIncidentsRouteSearch {
  return {
    account_id: boundedText(raw.account_id, 64),
    severity: setMember(raw.severity, INCIDENT_SEVERITIES),
    status: setMember(raw.status, INCIDENT_STATUSES),
  };
}

/**
 * `page` не входит в результат: журнал листается курсорным «Показать ещё»
 * (issue #340), а не URL-номером страницы — накопленные порции держит
 * инфинит-запрос, а не адресная строка.
 */
export function operatorIncidentsQuery(
  search: OperatorIncidentsRouteSearch,
  pageSize: number,
): Omit<OperatorIncidentsQuery, "page"> {
  return {
    account_id: search.account_id,
    severity: search.severity ? [search.severity] : [],
    status: search.status ? [search.status] : [],
    page_size: pageSize,
  };
}

export function operatorIncidentUsdConfirmed(
  scope: Pick<OperatorScopeEvidence, "currency" | "currency_state">,
): boolean {
  return scope.currency_state === "single" && scope.currency === "USD";
}

/** A disconnected live channel can retain history, but cannot label it current. */
export function operatorIncidentDataState(
  state: DataState,
  realtimeConnected: boolean,
): DataState {
  if (realtimeConnected) return state;
  return state === "unavailable" ? "unavailable" : "stale";
}

export function operatorIncidentCountLabel(count: number): string {
  const absolute = Math.abs(Math.trunc(count));
  const mod100 = absolute % 100;
  const mod10 = absolute % 10;
  const noun =
    mod10 === 1 && mod100 !== 11
      ? "запись"
      : mod10 >= 2 && mod10 <= 4 && (mod100 < 12 || mod100 > 14)
        ? "записи"
        : "записей";
  return `${absolute.toLocaleString("ru-RU")} ${noun}`;
}

/**
 * Defence in depth: the server already suppresses monetary incident copy when
 * USD is not proven. Web and TMA repeat the same gate before rendering.
 */
export function operatorIncidentCopy(
  item: OperatorIncidentItem,
  scope: Pick<OperatorScopeEvidence, "currency" | "currency_state">,
): { title: string; summary: string | null; reason: string | null } {
  if (item.requires_usd_evidence && !operatorIncidentUsdConfirmed(scope)) {
    return {
      title: "Денежный сигнал требует проверки",
      summary: "Валюта кабинета не подтверждена. Денежные детали скрыты.",
      reason: null,
    };
  }
  return {
    title: item.title.trim() || "Инцидент требует проверки",
    summary: item.summary?.trim() || null,
    reason: item.reason?.trim() || null,
  };
}

/** Never fall back to an opaque resource identifier in visible copy. */
export function operatorIncidentTargetLabel(
  item: OperatorIncidentItem,
): string {
  return item.target.label?.trim() || "Объект не указан";
}

function boundedText(value: unknown, maxLength: number): string | undefined {
  if (typeof value !== "string") return undefined;
  const normalized = value.trim();
  return normalized ? normalized.slice(0, maxLength) : undefined;
}

function setMember<T extends string>(
  value: unknown,
  allowed: ReadonlySet<T>,
): T | undefined {
  return typeof value === "string" && allowed.has(value as T)
    ? (value as T)
    : undefined;
}
