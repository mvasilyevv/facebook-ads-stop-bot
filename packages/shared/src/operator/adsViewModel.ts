import type { OperatorActionState, OperatorScopeEvidence } from "./contracts";

export type OperatorDeliveryKind = "active" | "inactive" | "unknown";

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
export function classifyOperatorDelivery(
  value: string | null,
): OperatorDeliveryKind {
  if (!value) return "unknown";
  const status = value.trim().toUpperCase();
  if (["OFF", "PAUSED", "INACTIVE", "DISABLED"].includes(status))
    return "inactive";
  if (["ACTIVE", "ON", "ENABLED", "DELIVERING"].includes(status))
    return "active";
  return "unknown";
}

/** Preserve the semantic difference between a confirmed zero and unknown. */
export function formatOperatorCount(value: number | null): string {
  return value === null ? "—" : value.toLocaleString("ru-RU");
}

/** FB Agent budgets are USD-only; every other currency fails closed. */
export function confirmedOperatorCurrency(
  scope:
    | Pick<OperatorScopeEvidence, "currency" | "currency_state">
    | null
    | undefined,
): string | null {
  return scope?.currency_state === "single" && scope.currency === "USD"
    ? "USD"
    : null;
}
