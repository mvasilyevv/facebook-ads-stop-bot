import type { OperatorActionState, OperatorScopeEvidence, OperatorSeverity } from "./contracts";

export type OperatorDeliveryKind =
  | "active"
  | "inactive"
  | "rejected"
  | "pending"
  | "parent_paused"
  | "terminal"
  | "unknown";

const DELIVERY_LABEL: Record<string, string> = {
  ACTIVE: "Активно",
  ON: "Активно",
  ENABLED: "Активно",
  DELIVERING: "Активно",
  WITH_ISSUES: "Активно, есть замечания",
  OFF: "Выключено",
  PAUSED: "Выключено",
  INACTIVE: "Выключено",
  DISABLED: "Выключено",
  ADSET_PAUSED: "Выключено на уровне адсета",
  CAMPAIGN_PAUSED: "Выключено на уровне кампании",
  CAMPAIGN_GROUP_PAUSED: "Выключено на уровне кампании",
  ARCHIVED: "В архиве",
  DELETED: "Удалено",
  PENDING_REVIEW: "На модерации",
  IN_REVIEW: "На модерации",
  PREAPPROVED: "Предварительно одобрено",
  IN_PROCESS: "Обрабатывается",
  PROCESSING: "Обрабатывается",
  DISAPPROVED: "Отклонено модерацией",
  PENDING_BILLING_INFO: "Ожидает платёжных данных",
  NOT_DELIVERING: "Не доставляется",
  ADSET_PAUSED_NOT_DELIVERING: "Не доставляется: адсет выключен",
};

function normalizedDelivery(value: string | null): string {
  return value?.trim().toUpperCase() ?? "";
}

const ACTIVE_ACTION_LABEL: Record<OperatorActionState, string> = {
  queued: "в очереди",
  running: "выполняется",
  confirmed: "Подтверждено · сверяем данные",
  failed: "ошибка",
  cancelled: "отменено",
  unknown: "результат не подтверждён",
};

/** Compact lifecycle text shared by Web and TMA ad command surfaces. */
export function operatorActiveActionLabel(state: OperatorActionState): string {
  return ACTIVE_ACTION_LABEL[state];
}

/** One status interpretation shared by web and TMA command renderers. */
export function classifyOperatorDelivery(value: string | null): OperatorDeliveryKind {
  if (!value) return "unknown";
  const status = normalizedDelivery(value);
  if (status === "DISAPPROVED") return "rejected";
  if (["ADSET_PAUSED", "CAMPAIGN_PAUSED", "CAMPAIGN_GROUP_PAUSED"].includes(status))
    return "parent_paused";
  if (["ARCHIVED", "DELETED"].includes(status)) return "terminal";
  if (
    [
      "PENDING_REVIEW",
      "IN_REVIEW",
      "PREAPPROVED",
      "IN_PROCESS",
      "PROCESSING",
      "PENDING_BILLING_INFO",
      "NOT_DELIVERING",
      "ADSET_PAUSED_NOT_DELIVERING",
    ].includes(status)
  )
    return "pending";
  if (["OFF", "PAUSED", "INACTIVE", "DISABLED"].includes(status)) return "inactive";
  if (["ACTIVE", "ON", "ENABLED", "DELIVERING", "WITH_ISSUES"].includes(status)) return "active";
  return "unknown";
}

export function operatorDeliveryLabel(value: string | null): string {
  if (!value) return "Статус не подтверждён";
  const status = normalizedDelivery(value);
  return DELIVERY_LABEL[status] ?? `Статус не распознан: ${value.trim()}`;
}

export function operatorDeliverySeverity(value: string | null): OperatorSeverity {
  const status = normalizedDelivery(value);
  if (status === "DISAPPROVED") return "critical";
  if (
    [
      "WITH_ISSUES",
      "PENDING_REVIEW",
      "IN_REVIEW",
      "PREAPPROVED",
      "IN_PROCESS",
      "PROCESSING",
      "PENDING_BILLING_INFO",
      "NOT_DELIVERING",
      "ADSET_PAUSED_NOT_DELIVERING",
    ].includes(status)
  )
    return "warning";
  return status ? "ok" : "unknown";
}

/** Preserve the semantic difference between a confirmed zero and unknown. */
export function formatOperatorCount(value: number | null): string {
  return value === null ? "—" : value.toLocaleString("ru-RU");
}

/** FB Agent budgets are USD-only; every other currency fails closed. */
export function confirmedOperatorCurrency(
  scope: Pick<OperatorScopeEvidence, "currency" | "currency_state"> | null | undefined,
): string | null {
  return scope?.currency_state === "single" && scope.currency === "USD" ? "USD" : null;
}
