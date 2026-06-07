/**
 * Маппинг доменных значений → badge-варианты (визуальные классы).
 *
 * Варианты совпадают с именами в макете:
 *   normal | warning | stop | claimed | disabled | pending | running | done | failed | retrying | cancelled
 *
 * Используется Badge-компонентом в обоих фронтах.
 */

import type { AlertState, AlertStage, TaskStatus } from "../constants/states";

/** Варианты бейджа для alert_state */
export type AlertStateBadgeVariant =
  | "normal"
  | "warning"
  | "stop"
  | "claimed"
  | "disabled";

/** Варианты бейджа для task_status */
export type TaskStatusBadgeVariant =
  | "pending"
  | "running"
  | "done"
  | "failed"
  | "retrying"
  | "cancelled";

/** Варианты бейджа для alert_stage */
export type AlertStageBadgeVariant = "warning" | "stop";

/**
 * alert_state → badge-вариант.
 * stop_sent → "stop" (красный), warning_sent → "warning" (оранжевый), и т.д.
 */
export function alertStateToBadgeVariant(state: AlertState): AlertStateBadgeVariant {
  switch (state) {
    case "warning_sent":
      return "warning";
    case "stop_sent":
      return "stop";
    case "claimed":
      return "claimed";
    case "disabled":
      return "disabled";
    case "normal":
    default:
      return "normal";
  }
}

/**
 * task_status → badge-вариант.
 * SUCCEEDED → "done", остальные по аналогии lowercase.
 */
export function taskStatusToBadgeVariant(status: TaskStatus): TaskStatusBadgeVariant {
  switch (status) {
    case "PENDING":
      return "pending";
    case "RUNNING":
      return "running";
    case "SUCCEEDED":
      return "done";
    case "FAILED":
      return "failed";
    case "RETRYING":
      return "retrying";
    case "CANCELLED":
      return "cancelled";
    default:
      return "pending";
  }
}

/**
 * alert_stage → badge-вариант.
 */
export function stageToBadgeVariant(stage: AlertStage): AlertStageBadgeVariant {
  return stage === "stop" ? "stop" : "warning";
}
