// Единый источник названий правил для фронтенда
// Синхронизирован с core/rules/labels.py

export const RULE_LABELS = {
  cpc_stop: 'Дорогой клик',
  cpl_stop: 'Дорогой лид',
  cpr_stop: 'Дорогая рега',
  regs_no_dep_stop: 'Реги без депозитов',
  spend_no_dep_range: 'Расход без депа',
  spend_with_dep_range: 'Расход с депозитом',
  early_outbound_ctr_signal: 'Мало переходов на PWA',
  early_lpv_ratio_signal: 'Мало открытий PWA после клика',
  early_cost_per_lpv_signal: 'Дорогое открытие PWA',
};

export const RULE_LABELS_SHORT = {
  cpc_stop: 'Дорогой клик',
  cpl_stop: 'Дорогой лид',
  cpr_stop: 'Дорогая рега',
  regs_no_dep_stop: 'Реги без депов',
  spend_no_dep_range: 'Расход без депа',
  spend_with_dep_range: 'Расход с депозитом',
  early_outbound_ctr_signal: 'Мало переходов',
  early_lpv_ratio_signal: 'Мало открытий PWA',
  early_cost_per_lpv_signal: 'Дорогое открытие',
};

export function ruleLabel(code) {
  return RULE_LABELS[code] || code;
}

export function ruleLabelShort(code) {
  return RULE_LABELS_SHORT[code] || code;
}
