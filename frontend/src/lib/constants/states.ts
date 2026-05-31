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

/** Тип задачи существительным — для бейджей и очередей (disable → «Отключение»). */
export const TASK_TYPE_LABELS: Record<string, string> = {
  disable: "Отключение",
  enable: "Включение",
  plan_run: "Создание кампании",
  meta_api_mutation: "Действие через API",
  ad_library_scan: "Разведка Ad Library",
};

export function taskTypeLabel(taskType: string): string {
  return TASK_TYPE_LABELS[taskType] ?? taskType;
}

/**
 * Коды стоп-правил — синхронизированы с core/rules/labels.py (RULE_LABELS).
 * API отдаёт сырой код в matched_rule_codes; человекочитаемый лейбл — на фронте.
 * Короткий лейбл — для бейджей; полное название — для tooltip.
 */
export const RULE_CODE_LABELS: Record<string, string> = {
  cpc_stop: "Дорогой клик",
  cpl_stop: "Дорогой лид",
  cpr_stop: "Дорогая рега",
  regs_no_dep_stop: "Реги без депов",
  spend_no_dep_range: "Расход без депа",
  spend_with_dep_range: "Расход с депозитом",
  early_outbound_ctr_signal: "Мало переходов",
  early_lpv_ratio_signal: "Мало открытий PWA",
  early_cost_per_lpv_signal: "Дорогое открытие",
  frequency_anomaly: "Выгорание",
};

const RULE_CODE_TITLES: Record<string, string> = {
  cpc_stop: "Дорогой клик",
  cpl_stop: "Дорогой лид",
  cpr_stop: "Дорогая рега",
  regs_no_dep_stop: "Регистрации без депозитов",
  spend_no_dep_range: "Расход без депозита",
  spend_with_dep_range: "Расход с депозитом",
  early_outbound_ctr_signal: "Мало переходов на PWA",
  early_lpv_ratio_signal: "Мало открытий PWA после клика",
  early_cost_per_lpv_signal: "Дорогое открытие PWA",
  frequency_anomaly: "Выгорание аудитории",
};

/** Короткий человекочитаемый лейбл кода правила (fallback — сам код). */
export function ruleCodeLabel(code: string): string {
  return RULE_CODE_LABELS[code] ?? code;
}

/** Полное название + код для tooltip: «Дорогой лид (cpl_stop)». */
export function ruleCodeTitle(code: string): string {
  const full = RULE_CODE_TITLES[code];
  return full ? `${full} (${code})` : code;
}
