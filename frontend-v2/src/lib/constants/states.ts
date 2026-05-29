/**
 * Константы FSM-state, alert stage, task status и их человекочитаемых лейблов.
 * Используется badge-компонентами и фильтрами.
 */

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

export const ALERT_STAGES = ["warning", "stop"] as const;
export type AlertStage = (typeof ALERT_STAGES)[number];

export const ALERT_STAGE_LABELS: Record<AlertStage, string> = {
  warning: "Предупреждение",
  stop: "Стоп",
};

/**
 * Task status — frontend uppercase representation.
 * Backend хранит lowercase ('draft'|'pending'|'running'|...).
 * Маппинг через apps/api/utils/status_mapper.py.
 */
export const TASK_STATUSES = [
  "PENDING",
  "RUNNING",
  "DONE",
  "FAILED",
  "RETRYING",
  "CANCELLED",
] as const;

export type TaskStatus = (typeof TASK_STATUSES)[number];

export const TASK_STATUS_LABELS: Record<TaskStatus, string> = {
  PENDING: "В очереди",
  RUNNING: "Выполняется",
  DONE: "Выполнено",
  FAILED: "Ошибка",
  RETRYING: "Повтор",
  CANCELLED: "Отменено",
};

export const TASK_TYPES = [
  "disable",
  "enable",
  "plan_run",
  "meta_api_mutation",
  "ad_library_scan",
] as const;

export type TaskType = (typeof TASK_TYPES)[number];

/** Список правил из core/rules/evaluator.py. */
export const RULE_CODES = [
  "CPL_HIGH",
  "CPA_HIGH",
  "CPM_HIGH",
  "CTR_LOW",
  "FREQ_HIGH",
  "FUNNEL_LOW",
  "SPEND_NO_EVENT",
  "FAST_STOP",
] as const;

export type RuleCode = (typeof RULE_CODES)[number];
