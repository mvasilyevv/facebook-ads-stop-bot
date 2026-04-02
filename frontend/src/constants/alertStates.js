export const ALERT_STATE_LABELS = {
  NORMAL: "Норма",
  EARLY_SIGNAL_SENT: "Ранний сигнал",
  WARNING_SENT: "Предупреждение",
  STOP_SENT: "Требует отключения",
  CLAIMED: "Взято в работу",
  DISABLED: "Отключено",
  ARCHIVED: "Архив",
};

export const ALERT_STATE_COLORS = {
  NORMAL: "var(--color-emerald, #10b981)",
  EARLY_SIGNAL_SENT: "var(--color-orchid, #8b5cf6)",
  WARNING_SENT: "var(--color-gold, #f59e0b)",
  STOP_SENT: "var(--color-crimson, #ef4444)",
  CLAIMED: "var(--accent-teal, #4f6ef7)",
  DISABLED: "var(--color-slate, #94a3b8)",
  ARCHIVED: "var(--color-slate, #94a3b8)",
};

export const ALERT_STATE_TOOLTIPS = {
  NORMAL: "Норма: метрики в пределах допустимых значений",
  EARLY_SIGNAL_SENT: "Ранний сигнал: замечены признаки слабого трафика",
  WARNING_SENT: "Предупреждение: метрики приближаются к стоп-порогу",
  STOP_SENT: "Требует отключения: нарушено стоп-правило",
  CLAIMED: "Взято в работу: оператор занимается инцидентом",
  DISABLED: "Отключено: объявление выключено системой или вручную",
  ARCHIVED: "Архив: объявление больше не сканируется",
};

export const TASK_STATUS_LABELS = {
  PENDING: "Ожидает",
  RUNNING: "Выполняется",
  RETRYING: "Повтор",
  SUCCESS: "Выполнено",
  FAILED: "Ошибка",
};

export const RECOMMENDATION_LEVEL_LABELS = {
  OK: "Можно включить",
  MEDIUM: "Хороший результат",
  GOOD: "Отличный результат",
};
