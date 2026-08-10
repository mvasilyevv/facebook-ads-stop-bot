/**
 * Коды стоп-правил и их человекочитаемые лейблы.
 *
 * Источник правды — core/rules/labels.py (RULE_LABELS + RULE_LABELS_SHORT).
 * Operator API отдаёт сырые коды правил в ленте событий и attention feed.
 * Человекочитаемый лейбл — на фронте, через ruleCodeLabel().
 */

/** Полные названия правил — для tooltip и детальных экранов. */
export const RULE_CODE_LABELS: Record<string, string> = {
  cpc_stop: "Дорогой клик",
  cpl_stop: "Дорогой лид",
  cpr_stop: "Дорогая рега",
  regs_no_dep_stop: "Реги без депозитов",
  spend_no_dep_range: "Расход без депа",
  spend_with_dep_range: "Расход с депозитом",
  early_outbound_ctr_signal: "Мало переходов на PWA",
  early_lpv_ratio_signal: "Мало открытий PWA после клика",
  early_cost_per_lpv_signal: "Дорогое открытие PWA",
  frequency_anomaly: "Выгорание аудитории",
};

/** Короткие лейблы — для бейджей и таблиц (≤20 символов). */
export const RULE_CODE_LABELS_SHORT: Record<string, string> = {
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

/** Все известные коды правил. */
export const RULE_CODES = Object.keys(RULE_CODE_LABELS) as RuleCode[];
export type RuleCode = keyof typeof RULE_CODE_LABELS;

/**
 * Человекочитаемый лейбл кода правила.
 * @param code — raw-код из API (например, "cpl_stop").
 * @param short — использовать короткий лейбл для бейджей.
 * @returns Лейбл или сам код как fallback.
 */
export function ruleCodeLabel(code: string, short = false): string {
  const map = short ? RULE_CODE_LABELS_SHORT : RULE_CODE_LABELS;
  return map[code] ?? code;
}

/**
 * Полное название + код для tooltip: «Дорогой лид (cpl_stop)».
 */
export function ruleCodeTitle(code: string): string {
  const full = RULE_CODE_LABELS[code];
  return full ? `${full} (${code})` : code;
}
