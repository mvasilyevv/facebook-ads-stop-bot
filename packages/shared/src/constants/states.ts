/**
 * ЕДИНЫЙ ИСТОЧНИК FSM-состояний, стадий алертов и статусов задач.
 *
 * Канон alert_state = LOWERCASE (хранится в БД и отдаётся web-API).
 * TMA-API (TmaAdDetailResponse.state) отдаёт UPPERCASE → поглощается normalizeAlertState.
 * Канон task_status = UPPERCASE (маппинг status_mapper.py: draft/pending → PENDING).
 *
 * Расхождение мини-апп vs канона:
 *   mini STATE_LABELS["CLAIMED"] = "Ожидает OFF"  ← БАГ, не повторяем.
 *   Канон: claimed → "В работе".
 *   mini STATE_LABELS["ARCHIVED"] — нет такого состояния в БД, удалено.
 */

// ─── Alert State ─────────────────────────────────────────────────────────────

export const ALERT_STATES = [
  "normal",
  "warning_sent",
  "stop_sent",
  "claimed",
  "disabled",
] as const;

export type AlertState = (typeof ALERT_STATES)[number];

export const ALERT_STATE_LABELS: Record<AlertState, string> = {
  normal: "Норма",
  warning_sent: "Предупреждение",
  stop_sent: "Стоп",
  claimed: "В работе",
  disabled: "Отключено",
};

/**
 * Нормализует raw-значение alert_state к каноническому lowercase.
 * Поглощает UPPERCASE из TMA-API ("NORMAL" → "normal"),
 * legacy-значения, опечатки. Неизвестное → "normal" + console.warn.
 */
export function normalizeAlertState(raw: string | null | undefined): AlertState {
  if (!raw) return "normal";
  const lower = raw.toLowerCase() as AlertState;
  if (ALERT_STATES.includes(lower)) return lower;
  console.warn(`[normalizeAlertState] Неизвестное состояние: ${raw}, fallback → "normal"`);
  return "normal";
}

/**
 * alert_state → CSS-переменная FSM-цвета из tokens.css.
 * ВАЖНО: токены называются --fsm-warning / --fsm-stop (по СТАДИИ, не state) —
 * прямая подстановка state в имя (`--fsm-warning_sent`) даёт несуществующий
 * токен и невидимую точку (реальный баг mini, найден при дедупе 2026-06-09).
 */
const ALERT_STATE_FSM_VAR: Record<AlertState, string> = {
  normal: "--fsm-normal",
  warning_sent: "--fsm-warning",
  stop_sent: "--fsm-stop",
  claimed: "--fsm-claimed",
  disabled: "--fsm-disabled",
};

/** CSS-значение цвета состояния: `var(--fsm-…)`. Любой вход нормализуется. */
export function alertStateCssVar(raw: string | null | undefined): string {
  return `var(${ALERT_STATE_FSM_VAR[normalizeAlertState(raw)]})`;
}

/**
 * Итоговый статус объявления для UI: учитывает И FSM (alert_state), И доставку в FB
 * (delivery_status из Ads Manager).
 *
 * Инцидентные FSM-состояния (warning_sent/stop_sent/claimed/disabled) — вердикт бота,
 * показываем как есть. НО «normal» у объявления, которое НЕ крутится в FB (delivery_status
 * != ACTIVE: OFF/PAUSED/…) — это ложная «Норма» (объявление выключено, монитор просто не бил
 * тревогу). Показываем «Выключено» (цвет disabled), чтобы не путать с активной нормой.
 * delivery_status неизвестен (null/пусто) → не переопределяем, оставляем FSM-лейбл.
 */
export function displayAdState(
  alertStateRaw: string | null | undefined,
  deliveryStatus: string | null | undefined,
): { label: string; state: AlertState } {
  const state = normalizeAlertState(alertStateRaw);
  if (state === "normal" && deliveryStatus && deliveryStatus.toUpperCase() !== "ACTIVE") {
    return { label: "Выключено", state: "disabled" };
  }
  return { label: ALERT_STATE_LABELS[state] ?? state, state };
}

// ─── Alert Stage ─────────────────────────────────────────────────────────────

export const ALERT_STAGES = ["warning", "stop"] as const;
export type AlertStage = (typeof ALERT_STAGES)[number];

export const ALERT_STAGE_LABELS: Record<AlertStage, string> = {
  warning: "Предупреждение",
  stop: "Стоп",
};

// ─── Task Status ─────────────────────────────────────────────────────────────

/**
 * Канон wire-формата: UPPERCASE, как отдаёт API (status_mapper.py).
 * Внутренний "draft" → "PENDING" на бэке, фронт его не видит.
 * "succeeded" → "SUCCEEDED" (отдаётся для завершённых задач).
 */
export const TASK_STATUSES = [
  "PENDING",
  "RUNNING",
  "SUCCEEDED",
  "FAILED",
  "RETRYING",
  "CANCELLED",
] as const;

export type TaskStatus = (typeof TASK_STATUSES)[number];

export const TASK_STATUS_LABELS: Record<TaskStatus, string> = {
  PENDING: "В очереди",
  RUNNING: "Выполняется",
  SUCCEEDED: "Выполнено",
  FAILED: "Ошибка",
  RETRYING: "Повтор",
  CANCELLED: "Отменено",
};

/**
 * Нормализует статус задачи к каноническому UPPERCASE.
 * Поглощает lowercase из прямых SQL-запросов или legacy-кода.
 * Неизвестное → "PENDING" + console.warn.
 */
export function normalizeTaskStatus(raw: string | null | undefined): TaskStatus {
  if (!raw) return "PENDING";
  const upper = raw.toUpperCase() as TaskStatus;
  // "draft" → PENDING (по контракту status_mapper.py)
  if (upper === ("DRAFT" as string)) return "PENDING";
  if (TASK_STATUSES.includes(upper)) return upper;
  console.warn(`[normalizeTaskStatus] Неизвестный статус: ${raw}, fallback → "PENDING"`);
  return "PENDING";
}

// ─── Task Type ───────────────────────────────────────────────────────────────

export const TASK_TYPES = [
  "disable",
  "enable",
  "plan_run",
  "meta_api_mutation",
  "ad_library_scan",
] as const;

export type TaskType = (typeof TASK_TYPES)[number];

export const TASK_TYPE_LABELS: Record<string, string> = {
  disable: "Отключение",
  enable: "Включение",
  plan_run: "Создание кампании",
  meta_api_mutation: "Действие через API",
  ad_library_scan: "Разведка Ad Library",
};

/** Человекочитаемый лейбл типа задачи, fallback — сырой тип. */
export function taskTypeLabel(taskType: string): string {
  return TASK_TYPE_LABELS[taskType] ?? taskType;
}
