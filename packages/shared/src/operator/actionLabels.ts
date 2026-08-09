import type { OperatorActionItem } from "./contracts";

const OPERATOR_ACTION_KIND_LABELS = {
  pause: "Отключение объявления",
  activate: "Включение объявления",
  scan: "Сканирование кабинетов",
  create: "Создание кампании",
  duplicate: "Дублирование кампании",
  other: "Системное действие",
} satisfies Record<OperatorActionItem["kind"], string>;

const OPERATOR_ACTION_STATE_REASONS = {
  queued: "Команда принята и ожидает выполнения.",
  running: "Команда выполняется; итог ещё не подтверждён.",
  confirmed: "Результат команды подтверждён.",
  failed: "Команда завершилась ошибкой. Проверьте состояние перед повтором.",
  cancelled: "Команда отменена.",
  unknown: "Результат команды требует сверки. Не повторяйте действие вслепую.",
} satisfies Record<OperatorActionItem["state"], string>;

/** Returns operator copy only; unknown backend values never reach the UI. */
export function operatorActionKindLabel(kind: unknown): string {
  if (
    typeof kind === "string" &&
    Object.prototype.hasOwnProperty.call(OPERATOR_ACTION_KIND_LABELS, kind)
  ) {
    return OPERATOR_ACTION_KIND_LABELS[
      kind as keyof typeof OPERATOR_ACTION_KIND_LABELS
    ];
  }
  return "Операторское действие";
}

/** Derive safe operator copy from the public lifecycle, never backend reason text. */
export function operatorActionStateReason(state: unknown): string {
  if (
    typeof state === "string" &&
    Object.prototype.hasOwnProperty.call(OPERATOR_ACTION_STATE_REASONS, state)
  ) {
    return OPERATOR_ACTION_STATE_REASONS[
      state as keyof typeof OPERATOR_ACTION_STATE_REASONS
    ];
  }
  return "Состояние команды требует сверки. Не повторяйте действие вслепую.";
}

export interface OperatorActionRecovery {
  label: string;
  destination: "target" | "sources";
}

/**
 * Failed and ambiguous commands always expose a concrete, safe next step.
 * Route construction stays in the platform shell; this helper only chooses
 * whether exact target evidence is available.
 */
export function operatorActionRecovery(
  state: unknown,
  targetId: unknown,
): OperatorActionRecovery | null {
  if (state !== "failed" && state !== "unknown") return null;
  if (typeof targetId === "string" && targetId.trim()) {
    return { label: "Проверить объявление", destination: "target" };
  }
  return { label: "Проверить источники", destination: "sources" };
}
