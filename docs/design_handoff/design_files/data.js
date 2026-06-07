// data.js — FB Stop Bot mock data (live + calm scenarios). Attaches to window.DATA.
(function () {
  // 24h spend × hour (USD). Daytime ramp, evening tail.
  const SPEND_LIVE = [
    412, 388, 301, 246, 198, 174, 161, 203, 367, 612, 884, 1042,
    1187, 1098, 1243, 1361, 1422, 1318, 1190, 1004, 842, 701, 588, 503,
  ];
  const SPEND_CALM = SPEND_LIVE.map((v) => Math.round(v * 0.78));

  // KPI strip
  const KPI_LIVE = [
    { key: 'active',   eyebrow: 'ACTIVE',   label: 'Норма',         value: 247, trend: +6,  trendPct: '+2.5%', note: 'активны сейчас',   tone: 'normal' },
    { key: 'warning',  eyebrow: 'WARNING',  label: 'Предупреждение', value: 12,  trend: +3,  trendPct: '+33%',  note: 'за последний час', tone: 'warning' },
    { key: 'stop',     eyebrow: 'STOP',     label: 'Стоп',          value: 4,   trend: -1,  trendPct: '−20%',  note: 'требуют решения',  tone: 'stop' },
    { key: 'disabled', eyebrow: 'DISABLED', label: 'Отключено',     value: 89,  trend: +14, trendPct: '+19%',  note: 'за сегодня',       tone: 'disabled' },
  ];
  const KPI_CALM = [
    { key: 'active',   eyebrow: 'ACTIVE',   label: 'Норма',         value: 251, trend: +2, trendPct: '+0.8%', note: 'активны сейчас',  tone: 'normal' },
    { key: 'warning',  eyebrow: 'WARNING',  label: 'Предупреждение', value: 0,  trend: 0,  trendPct: '0%',    note: 'за последний час', tone: 'warning' },
    { key: 'stop',     eyebrow: 'STOP',     label: 'Стоп',          value: 0,   trend: 0,  trendPct: '0%',    note: 'требуют решения', tone: 'stop' },
    { key: 'disabled', eyebrow: 'DISABLED', label: 'Отключено',     value: 71,  trend: +3, trendPct: '+4%',   note: 'за сегодня',      tone: 'disabled' },
  ];

  // Active incidents
  const INCIDENTS_LIVE = [
    { id: 'i1', ad: 'CR2 | DRC | MV', offer: 'DRC', state: 'warning', age: '12м', rules: ['CPL_HIGH'],                spend: 234.5, cpl: 18.3 },
    { id: 'i2', ad: 'UA17 | SP | MV', offer: 'UA17', state: 'stop',    age: '4м',  rules: ['CPL_HIGH', 'FREQ_HIGH'],  spend: 891.2, cpl: 42.1 },
    { id: 'i3', ad: 'BR9 | DRC | AK', offer: 'DRC', state: 'warning', age: '23м', rules: ['SPEND_NO_EVENT'],         spend: 156.0, cpl: 0 },
    { id: 'i4', ad: 'PL4 | NUT | MV', offer: 'NUT', state: 'claimed', age: '31м', rules: ['ROAS_LOW'],               spend: 402.7, cpl: 27.9 },
    { id: 'i5', ad: 'DE2 | SP | TK',  offer: 'SP',  state: 'warning', age: '47м', rules: ['CTR_LOW'],                spend: 88.4,  cpl: 14.2 },
    { id: 'i6', ad: 'IT8 | DRC | AK', offer: 'DRC', state: 'stop',    age: '52м', rules: ['CPL_HIGH', 'BUDGET_OVER'],spend: 1204.0, cpl: 51.6 },
  ];

  // Recent events (live-tail)
  const EVENTS_LIVE = [
    { id: 'e1',  time: '14:32:08', stage: 'warning', ad: 'CR2 | DRC | MV | Tyver | 25.03', rules: ['CPL_HIGH'] },
    { id: 'e2',  time: '14:31:54', stage: 'warning', ad: 'BR9 | DRC | AK | Lima | 19.02',  rules: ['SPEND_NO_EVENT'] },
    { id: 'e3',  time: '14:28:11', stage: 'stop',    ad: 'UA17 | SP | MV | Kyiv | 30.01',  rules: ['CPL_HIGH', 'FREQ_HIGH'] },
    { id: 'e4',  time: '14:22:40', stage: 'warning', ad: 'DE2 | SP | TK | Berlin | 11.04', rules: ['CTR_LOW'] },
    { id: 'e5',  time: '14:19:03', stage: 'claimed', ad: 'PL4 | NUT | MV | Warsaw | 02.03',rules: ['ROAS_LOW'] },
    { id: 'e6',  time: '14:11:27', stage: 'stop',    ad: 'IT8 | DRC | AK | Roma | 27.02',  rules: ['CPL_HIGH', 'BUDGET_OVER'] },
    { id: 'e7',  time: '14:04:55', stage: 'warning', ad: 'FR3 | NUT | TK | Paris | 14.03', rules: ['FREQ_HIGH'] },
    { id: 'e8',  time: '13:58:19', stage: 'warning', ad: 'ES6 | DRC | MV | Madrid | 09.04',rules: ['CPL_HIGH'] },
    { id: 'e9',  time: '13:51:02', stage: 'claimed', ad: 'CR2 | DRC | MV | Tyver | 25.03', rules: ['CPL_HIGH'] },
    { id: 'e10', time: '13:44:38', stage: 'warning', ad: 'NL1 | SP | AK | Amsterdam | 21.02', rules: ['CTR_LOW'] },
  ];

  // Task queues
  const DISABLE_TASKS_LIVE = [
    { id: 't1', type: 'disable', ad: 'UA17 | SP | MV', status: 'pending',    attempts: 0, age: '4м' },
    { id: 't2', type: 'disable', ad: 'IT8 | DRC | AK', status: 'in_progress',attempts: 1, age: '12м' },
    { id: 't3', type: 'disable', ad: 'CR2 | DRC | MV', status: 'pending',    attempts: 0, age: '1м' },
    { id: 't4', type: 'disable', ad: 'GB5 | NUT | TK', status: 'failed',     attempts: 3, age: '38м' },
    { id: 't5', type: 'disable', ad: 'PT2 | SP | MV',  status: 'pending',    attempts: 0, age: '2м' },
  ];
  const ENABLE_TASKS_LIVE = [
    { id: 't6', type: 'enable', ad: 'BR9 | DRC | AK', status: 'in_progress', attempts: 1, age: '7м' },
    { id: 't7', type: 'enable', ad: 'FR3 | NUT | TK', status: 'pending',     attempts: 0, age: '3м' },
    { id: 't8', type: 'enable', ad: 'NL1 | SP | AK',  status: 'pending',     attempts: 0, age: '9м' },
  ];

  // Event drill-down detail (for drawer)
  const EVENT_DETAIL = {
    e1: {
      ad: 'CR2 | DRC | MV | Tyver | 25.03',
      ad_id: '120211438870128761',
      offer: 'DRC', state: 'warning', cabinet: 'PT · Cabinet #4',
      metrics: [
        { k: 'spend',  v: '$234.50', flag: false },
        { k: 'CPL',    v: '$18.30',  flag: true },
        { k: 'CPA',    v: '$31.20',  flag: false },
        { k: 'CTR',    v: '1.8%',    flag: false },
        { k: 'CPM',    v: '$9.40',   flag: false },
        { k: 'freq',   v: '2.4',     flag: false },
        { k: 'leads',  v: '12',      flag: false },
        { k: 'ROAS',   v: '1.9×',    flag: false },
      ],
      rule: { code: 'CPL_HIGH', detail: 'CPL $18.30 > threshold $15.00 на окне 3ч', window: '3h' },
      timeline: [
        { t: '14:32', label: 'warning отправлен', stage: 'warning' },
        { t: '14:18', label: 'CPL пересёк порог', stage: 'warning' },
        { t: '11:04', label: 'norma — в пределах', stage: 'normal' },
        { t: '09:30', label: 'старт открутки', stage: 'normal' },
      ],
    },
  };

  window.DATA = {
    spend: { live: SPEND_LIVE, calm: SPEND_CALM },
    kpi:   { live: KPI_LIVE, calm: KPI_CALM },
    incidents: { live: INCIDENTS_LIVE, calm: [] },
    events:    { live: EVENTS_LIVE, calm: [] },
    disableTasks: { live: DISABLE_TASKS_LIVE, calm: [] },
    enableTasks:  { live: ENABLE_TASKS_LIVE, calm: [] },
    eventDetail: EVENT_DETAIL,
  };
})();
