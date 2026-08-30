/**
 * Единая таблица вид→классы Badge для обоих фронтов (frontend, frontend-mini).
 *
 * Раньше у каждого фронта была своя копия этой таблицы, и они разошлись:
 * `pending` на web — нейтральный (bg-bg-3/text-bg-9), на mini — акцентный
 * (bg-accent-bg/text-accent-muted). `pending` значит «поставлено в очередь»,
 * то есть ожидание, а не что-то выделенное вниманием оператора — акцентный
 * вид читался как «это важно/выбрано», хотя это не так. Канон — нейтральный.
 *
 * Компонент Badge каждого фронта остаётся своим (touch-target, layout,
 * механизм className — cva на web, ручной cn на mini): здесь только цвет.
 */

export type BadgeVariant =
  // FSM alert_state
  | "normal"
  | "warning"
  | "stop"
  | "claimed"
  | "disabled"
  // Дополнительные
  | "success"
  | "info"
  | "neutral"
  // Task statuses
  | "pending"
  | "running"
  | "done"
  | "failed"
  | "retrying"
  | "cancelled";

export interface BadgeVariantClasses {
  /** Фон + цвет рамки + цвет текста. */
  surface: string;
  /** Цвет точки-индикатора статуса (withDot). */
  dot: string;
}

export const BADGE_VARIANT_CLASSES: Record<BadgeVariant, BadgeVariantClasses> = {
  normal: { surface: "bg-bg-2 border-bg-6 text-bg-10", dot: "bg-bg-9" },
  warning: { surface: "bg-warning-bg border-warning/30 text-warning", dot: "bg-warning" },
  stop: { surface: "bg-danger-bg border-danger/30 text-danger", dot: "bg-danger" },
  claimed: { surface: "bg-info-bg border-info/30 text-info", dot: "bg-info" },
  disabled: { surface: "bg-bg-2 border-bg-5 text-bg-9", dot: "bg-bg-8" },
  success: { surface: "bg-success-bg border-success/30 text-success", dot: "bg-success" },
  info: { surface: "bg-info-bg border-info/30 text-info", dot: "bg-info" },
  neutral: { surface: "bg-bg-3 border-bg-6 text-bg-10", dot: "bg-bg-8" },
  // pending — ожидание в очереди, не акцент.
  pending: { surface: "bg-bg-3 border-bg-6 text-bg-9", dot: "bg-bg-8" },
  running: { surface: "bg-info-bg border-info/30 text-info", dot: "bg-info" },
  done: { surface: "bg-success-bg border-success/30 text-success", dot: "bg-success" },
  failed: { surface: "bg-danger-bg border-danger/30 text-danger", dot: "bg-danger" },
  retrying: { surface: "bg-warning-bg border-warning/30 text-warning", dot: "bg-warning" },
  cancelled: { surface: "bg-bg-2 border-bg-5 text-bg-9", dot: "bg-bg-8" },
};

export const BADGE_VARIANTS = Object.keys(BADGE_VARIANT_CLASSES) as BadgeVariant[];
