// Единый источник меток FSM-состояний ad_alert_state для всех страниц mini-app.
// Раньше STATE_LABELS дублировался в 3 страницах, причём в DashboardPage был неполным
// (без NORMAL/DISABLED) — состояния показывались как raw-значения (M11).
export const STATE_LABELS = {
  NORMAL: "Норма",
  WARNING_SENT: "Предупреждение",
  STOP_SENT: "Стоп",
  CLAIMED: "Ожидает OFF",
  DISABLED: "Отключено",
  ARCHIVED: "Архив",
};
