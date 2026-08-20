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

/**
 * Что означает состояние команды. Это подпись состояния, а не причина исхода:
 * причина приходит из события в `action.reason` — см. `operatorActionReason`.
 */
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

/** Сверка обязательна независимо от того, названа причина или нет. */
const RECONCILE_WARNING =
  "Результат требует сверки — не повторяйте действие вслепую.";

/**
 * Зеркало серверного `sanitize_operator_reason` (`core/tasks/action_reason.py`).
 * Причина приходит из БД, а не из кода экрана, поэтому последний рубеж перед
 * оператором проверяет её здесь: внутренности Python, адрес, секрет с
 * разделителем, Graph-токен, идентификатор запроса и UUID не показываются
 * вовсе. Обрезанный traceback в ленте хуже честного «причина не записана».
 *
 * Разошедшееся с сервером правило — дефект: сервер отбрасывает такой текст ещё
 * при чтении, здесь он не должен появиться.
 */
const UNSAFE_OPERATOR_REASON =
  /Traceback|File "|line \d+, in |https?:\/\/|\b(?:access_token|api[_-]?key|x-token|token|password|secret|fbtrace[_-]?id)\s*[:=]|\bBearer\s|\bEAA[A-Za-z0-9_-]{16,}|\b[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}\b/i;

function recordedOperatorReason(reason: unknown): string {
  if (typeof reason !== "string") return "";
  const trimmed = reason.trim();
  if (!trimmed || UNSAFE_OPERATOR_REASON.test(trimmed)) return "";
  return trimmed;
}

const UNRECORDED_REASON = {
  failed: "Причина отказа не записана. Проверьте состояние перед повтором.",
  unknown: `Причина не записана. ${RECONCILE_WARNING}`,
} as const;

/**
 * Причина исхода действия: та, что записал бэкенд при завершении задачи.
 *
 * Из состояния причина не выводится. Раньше выводилась — и пять заливов,
 * упавших по пяти разным причинам (отключённый кабинет, потерянный контекст
 * страницы, недоступный браузер, исчерпанный дедлайн), читались в очереди
 * одной строкой.
 *
 * `null` от бэкенда означает «причина не записана», а не «всё в порядке»:
 * у отказа и неизвестного итога это говорится прямым текстом. Предупреждение о
 * сверке остаётся при любом `unknown` — оно про деньги, а не про причину.
 */
export function operatorActionReason(action: {
  state: unknown;
  reason?: string | null;
}): string {
  const recorded = recordedOperatorReason(action.reason);
  if (action.state === "unknown") {
    return recorded
      ? `${recorded} ${RECONCILE_WARNING}`
      : UNRECORDED_REASON.unknown;
  }
  if (recorded) return recorded;
  if (action.state === "failed") return UNRECORDED_REASON.failed;
  return operatorActionStateReason(action.state);
}

export type OperatorCommandTone = "success" | "info" | "warning" | "error";

/**
 * Тон уведомления о команде выводится только из подтверждённого lifecycle.
 * HTTP 202 означает queued, а не выполнено, поэтому «успех» (зелёный) доступен
 * исключительно для confirmed; queued и running — нейтральный info; отказ —
 * error.
 *
 * Отмена — не отказ: система сама снимает задачу, когда выполнять её не нужно
 * или небезопасно (сканирование выключено оператором, мониторить нечего,
 * owner scope кабинетов не задан). Красный на таком исходе означал бы поломку
 * там, где её нет, поэтому отмена — warning: команда не выполнена, но чинить
 * нечего. Неизвестный итог остаётся warning по той же шкале внимания.
 */
export function operatorCommandTone(state: unknown): OperatorCommandTone {
  if (state === "confirmed") return "success";
  if (state === "queued" || state === "running") return "info";
  if (state === "failed") return "error";
  return "warning";
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
