export const inputCls =
  'w-full rounded bg-elevated border border-border px-3 py-2 text-sm text-primary focus:border-accent focus:ring-1 focus:ring-accent focus:outline-none disabled:opacity-50';

export const RULE_DEFS = [
  {
    key: 'cpc_percent',
    title: 'Правило 1: CPC > X% CPA',
    hint: 'Стоп при стоимости клика выше установленного % от целевого CPA',
    fields: [{ name: 'cpc_percent_stop', label: 'Процент стопа (%)', type: 'number' }],
  },
  {
    key: 'cpl_percent',
    title: 'Правило 2: CPL > X% CPA',
    hint: 'Стоп при стоимости лида выше установленного % от целевого CPA',
    fields: [{ name: 'cpl_percent_stop', label: 'Процент стопа (%)', type: 'number' }],
  },
  {
    key: 'cpr_percent',
    title: 'Правило 3: CPR > X% CPA',
    hint: 'Стоп при стоимости регистрации выше установленного % от целевого CPA',
    fields: [{ name: 'cpr_percent_stop', label: 'Процент стопа (%)', type: 'number' }],
  },
  {
    key: 'regs_no_dep',
    title: 'Правило 4: N регистраций без депозитов',
    hint: 'Стоп при заданном количестве регистраций без депозита',
    fields: [{ name: 'regs_no_dep_stop_count', label: 'Количество регистраций', type: 'number' }],
  },
  {
    key: 'spend_no_dep',
    title: 'Правило 5: Расход без депозитов',
    hint: 'Стоп при расходе в диапазоне % от CPA без депозитов',
    fields: [
      { name: 'spend_no_dep_from_percent', label: 'Расход от (% CPA)', type: 'number' },
      { name: 'spend_no_dep_to_percent', label: 'Расход до (% CPA)', type: 'number' },
    ],
  },
  {
    key: 'spend_with_dep',
    title: 'Правило 6: Расход с депозитом',
    hint: 'Стоп при расходе в диапазоне % от CPA с депозитом',
    fields: [
      { name: 'spend_with_dep_from_percent', label: 'Расход от (% CPA)', type: 'number' },
      { name: 'spend_with_dep_to_percent', label: 'Расход до (% CPA)', type: 'number' },
    ],
  },
];

export const DIAGNOSTIC_FIELDS = [
  { name: 'frequency_elevated_threshold', label: 'Частота: повышено от', type: 'number' },
  { name: 'frequency_critical_threshold', label: 'Частота: критично от', type: 'number' },
];

export const DEFAULT_RULES = {
  cpc_percent_enabled: true,
  cpc_percent_stop: '2',
  cpl_percent_enabled: true,
  cpl_percent_stop: '10',
  cpr_percent_enabled: true,
  cpr_percent_stop: '20',
  regs_no_dep_enabled: true,
  regs_no_dep_stop_count: '5',
  spend_no_dep_enabled: true,
  spend_no_dep_from_percent: '50',
  spend_no_dep_to_percent: '70',
  spend_with_dep_enabled: true,
  spend_with_dep_from_percent: '70',
  spend_with_dep_to_percent: '90',
  frequency_elevated_threshold: '2',
  frequency_critical_threshold: '3',
};
