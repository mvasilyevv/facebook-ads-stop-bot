import { lazy, Suspense, useEffect, useMemo, useRef, useState } from 'react';
import { useQuery, useQueryClient } from '@tanstack/react-query';
import { fmt$ as _fmt$, fmtN as _fmtN, fmtRoas as _fmtRoas } from '../utils/formatters.js';
import {
  getDashboardBatch,
  getObserverSettings,
  getVisionSettings,
  toggleScanning,
  toggleAutoEnable,
  triggerScanNow,
  retryDisableTask,
  cancelDisableTask,
  getDashboardPerformance,
  getEnableTasks,
  getEnableRecommendations,
  getChartData,
  createDisableTask,
  createEnableTaskFromRecommendation,
  validateBrowserColumns,
} from '../api.js';
import { DashboardOperations } from '../components/dashboard/DashboardOperations.jsx';
import { useRefreshOnResume } from '../hooks/useRefreshOnResume.js';
import { useWebSocket } from '../hooks/useWebSocket.js';
import { CampaignScorecard, FunnelChart } from '../components/CampaignScorecard.jsx';
import { BudgetOverrunChart } from '../components/BudgetOverrunChart.jsx';
import { CampaignBreakdownTable } from '../components/CampaignBreakdownTable.jsx';
import { RuleViolationRanking } from '../components/RuleViolationRanking.jsx';
import { OfferLeaderboard } from '../components/OfferLeaderboard.jsx';
import { CampaignComparativeBars } from '../components/CampaignComparativeBars.jsx';
import { SpendAlertsChart } from '../components/SpendAlertsChart.jsx';
import AIBriefingCard from '../components/ai/AIBriefingCard.jsx';
import ObserverStatusTile from '../components/observer/ObserverStatusTile.jsx';

const HealthMapPage = lazy(() => import('./HealthMapPage.jsx'));

/* === Вспомогательные компоненты === */

/** Баннер ошибки валидации колонок — показывается если колонки отсутствуют. */
function ColumnValidationBanner({ validationResult, onRecheck, checking = false }) {
  if (!validationResult || validationResult.valid) return null;

  const missing = validationResult.missing_columns || [];
  const errorMessage = validationResult.error_message || '';
  if (missing.length === 0 && !errorMessage) return null;

  return (
    <div className="rounded-md bg-danger-muted border border-danger/30 px-4 py-3 mb-md">
      <div className="flex items-start gap-3">
        <span className="status-dot bg-danger animate-pulse-dot mt-0.5" />
        <div className="flex-1">
          <p className="text-sm font-semibold text-danger mb-1">
            Отсутствуют колонки в таблице Ads Manager
          </p>
          <p className="text-2xs text-danger/80 mb-2">
            {missing.length > 0
              ? 'Сервис не может корректно сканировать объявления. Добавьте следующие колонки в таблицу Ads Manager:'
              : errorMessage}
          </p>
          {missing.length > 0 && (
            <div className="flex flex-wrap gap-1.5 mb-2">
              {missing.map((col) => (
                <span key={col} className="rounded bg-danger/20 px-2 py-0.5 text-2xs font-mono text-danger">
                  {col}
                </span>
              ))}
            </div>
          )}
          <button
            className="text-2xs text-danger/70 hover:text-danger underline disabled:opacity-60"
            onClick={onRecheck}
            disabled={checking}
          >
            {checking ? 'Проверяем...' : 'Проверить снова'}
          </button>
        </div>
      </div>
    </div>
  );
}

function emptyColumnValidationResult() {
  return { valid: true, missing_columns: [], found_columns: [], error_message: '' };
}

function failedColumnValidationResult(err) {
  return {
    valid: false,
    missing_columns: [],
    found_columns: [],
    error_message: err?.message || 'Не удалось проверить колонки Ads Manager',
  };
}

function isMissingBrowserSessionError(err) {
  return String(err?.message || '').includes('Активная browser-agent сессия не найдена');
}

function isDeliveryDisabled(status) {
  if (!status) return false;
  const s = status.toLowerCase();
  return s === 'off' || s.includes('off');
}

function normalizeIncidentList(payload) {
  if (!Array.isArray(payload)) return [];
  return payload.map((item) => ({
    fb_ad_id: item.fb_ad_id,
    ad_name: item.ad_name,
    campaign_name: item.campaign_name,
    current_state: item.current_state,
    current_stage: item.current_stage,
    reason_title: item.reason_title,
    matched_rule_codes: item.matched_rule_codes || [],
    last_activity_at: item.last_activity_at,
    has_active_disable_task: item.has_active_disable_task,
    delivery_status: item.delivery_status,
  }));
}

/** Дельта: ▲/▼ к вчера */
function Delta({ today, yesterday, lowerIsBetter = false }) {
  if (today == null || yesterday == null) return null;
  const a = Number(today);
  const b = Number(yesterday);
  if (b === 0) return null;
  const diff = a - b;
  const pct = Math.abs((diff / b) * 100).toFixed(0);
  if (pct === '0') return null;
  const up = diff > 0;
  const good = lowerIsBetter ? !up : up;
  const color = good ? 'text-success' : 'text-danger';
  return (
    <span className={`font-mono text-2xs font-semibold ${color}`}>
      {up ? '↑' : '↓'} {pct}%
    </span>
  );
}

const DASHBOARD_BATCH_QUERY = { limit: 50 };

/** Форматирует время «N сек назад» / «N мин назад» для индикатора обновления */
function formatUpdatedAgo(updatedAt) {
  if (!updatedAt) return null;
  const diffMs = Date.now() - updatedAt;
  const diffSec = Math.round(diffMs / 1000);
  if (diffSec < 5) return 'только что';
  if (diffSec < 60) return `${diffSec} сек назад`;
  const diffMin = Math.floor(diffSec / 60);
  if (diffMin < 60) return `${diffMin} мин назад`;
  return null;
}

/** Живой индикатор времени последнего обновления — тикает каждую секунду */
function UpdatedAgoLabel({ updatedAt }) {
  const [, setTick] = useState(0);
  useEffect(() => {
    if (!updatedAt) return undefined;
    const id = setInterval(() => setTick((n) => n + 1), 1000);
    return () => clearInterval(id);
  }, [updatedAt]);
  const label = formatUpdatedAgo(updatedAt);
  if (!label) return null;
  return (
    <span className="font-mono text-[11px] text-muted/60 leading-none">
      обновлено {label}
    </span>
  );
}



const ANALYTICS_TABS = [
  { id: 'cpr', label: 'CPR' },
  { id: 'budget', label: 'Бюджет' },
  { id: 'campaigns', label: 'Кампании' },
  { id: 'offers', label: 'Офферы' },
  { id: 'funnel', label: 'Воронка' },
];

/** KPI-полоса */
function HeroKPIStrip({ performance, performanceYesterday }) {
  const s = performance?.summary;
  const fmt$ = _fmt$;
  const fmtN = _fmtN;
  const fmtPct = (v) => (v != null ? `${Number(v).toFixed(1)}%` : '—');
  const fmtRoas = _fmtRoas;

  // Сравнение «как было вчера в это же время суток»: суммируем timeline
  // вчерашнего дня по бакетам с timestamp <= текущего момента дня.
  // Без этого ↓% сравнивает неполный сегодняшний день с полным вчерашним
  // и в начале дня всегда «падает».
  const yComparable = useMemo(() => {
    const yTimeline = performanceYesterday?.timeline ?? [];
    const yFullSummary = performanceYesterday?.summary ?? null;
    if (!yTimeline.length) return yFullSummary;
    const now = new Date();
    const minutesOfDay = now.getHours() * 60 + now.getMinutes();
    let spend = 0;
    let leads = 0;
    let registrations = 0;
    let deposits = 0;
    let lastPoint = null;
    for (const pt of yTimeline) {
      const ts = pt?.timestamp ? new Date(pt.timestamp) : null;
      if (!ts || Number.isNaN(ts.getTime())) continue;
      const pmin = ts.getHours() * 60 + ts.getMinutes();
      if (pmin > minutesOfDay) break;
      lastPoint = pt;
    }
    if (lastPoint) {
      // timeline у нас уже cumulative, значит последняя точка ≤ «сейчас»
      // и даёт расход/реги/депы вчера до того же часа.
      spend = Number(lastPoint.spend ?? 0);
      registrations = Number(lastPoint.registrations ?? 0);
      deposits = Number(lastPoint.deposits ?? 0);
    }
    // Лиды и производные ставки берём из общего summary как fallback —
    // в timeline их нет, но они меняются медленнее spend'а.
    leads = Number(yFullSummary?.leads ?? 0);
    const regToDep = registrations > 0 ? (deposits / registrations) * 100 : null;
    const roas = yFullSummary?.roas ?? null;
    return {
      spend,
      leads,
      registrations,
      deposits,
      reg_to_dep_rate: regToDep,
      roas,
    };
  }, [performanceYesterday]);

  const hasDeposits = Number(s?.deposits ?? 0) > 0;
  const hasSpend = Number(s?.spend ?? 0) > 0;
  const roasVal = Number(s?.roas ?? 0);

  const kpis = [
    { label: 'Расход', value: fmt$(s?.spend), key: 'spend', color: 'text-accent' },
    { label: 'Лиды', value: fmtN(s?.leads), key: 'leads', color: Number(s?.leads ?? 0) > 0 ? 'text-accent' : 'text-muted' },
    { label: 'Реги', value: fmtN(s?.registrations), key: 'registrations', color: 'text-primary' },
    { label: 'Депозиты', value: fmtN(s?.deposits), key: 'deposits', color: hasDeposits ? 'text-success' : hasSpend ? 'text-danger' : 'text-muted' },
    { label: 'Рег→Деп', value: fmtPct(s?.reg_to_dep_rate), key: 'reg_to_dep_rate', color: 'text-primary' },
    { label: 'ROAS', value: fmtRoas(s?.roas), key: 'roas', color: roasVal >= 3 ? 'text-success' : roasVal >= 1 ? 'text-warning' : roasVal > 0 ? 'text-danger' : 'text-muted' },
  ];

  return (
    <div className="mb-md grid auto-rows-fr grid-cols-2 items-stretch gap-3 sm:grid-cols-3 lg:grid-cols-6">
      {kpis.map((kpi, i) => (
        <div
          key={kpi.label}
          className="kpi-cell stagger-item"
          style={{ animationDelay: `${120 + i * 50}ms` }}
        >
          <span className="kpi-label">{kpi.label}</span>
          <div className="mt-auto flex items-baseline justify-between gap-2">
            <span className={`kpi-value ${kpi.color}`}>{kpi.value}</span>
            <Delta today={s?.[kpi.key]} yesterday={yComparable?.[kpi.key]} />
          </div>
        </div>
      ))}
    </div>
  );
}

/* === Основная страница === */

export default function DashboardPage({ onNavigate }) {
  const queryClient = useQueryClient();
  const [error, setError] = useState(null);
  const [toggling, setToggling] = useState(false);
  const [togglingAutoEnable, setTogglingAutoEnable] = useState(false);
  const [scanning, setScanning] = useState(false);
  const [columnRechecking, setColumnRechecking] = useState(false);
  const [healthMapOpen, setHealthMapOpen] = useState(false);
  const [analyticsOpen, setAnalyticsOpen] = useState(false);
  const [analyticsView, setAnalyticsView] = useState('cpr');
  const alertTrayRef = useRef(null);
  // Баг 2: useRef для таймера polling — cleanup всегда ловит актуальный timerId
  const scanTimerRef = useRef(null);

  /* --- Основные данные: обновление каждые 30 секунд --- */
  const { data: settings } = useQuery({
    queryKey: ['observerSettings'],
    queryFn: getObserverSettings,
    refetchInterval: 30_000,
  });

  const { data: visionStatus } = useQuery({
    queryKey: ['visionStatus'],
    queryFn: getVisionSettings,
    refetchInterval: 10_000,
  });

  const { data: rawPerformance } = useQuery({
    queryKey: ['performanceToday'],
    queryFn: () => getDashboardPerformance({ period: 'today' }),
    refetchInterval: 30_000,
  });
  const performance = rawPerformance;

  const [chartPeriod, setChartPeriod] = useState('today');
  const STALE_CHART_TTL_MS = 3 * 60 * 1000;
  const lastNonZeroChartPerformanceRef = useRef(null);
  const chartPerformanceCachedAtRef = useRef(0);
  const prevLastScanAtRef = useRef(null);

  const { data: rawChartPerformance } = useQuery({
    queryKey: ['performanceChart', chartPeriod],
    queryFn: () => getDashboardPerformance({ period: chartPeriod }),
    refetchInterval: 30_000,
  });

  const chartPerformance = useMemo(() => {
    // Для "today" sticky отключён: ноль на старте дня — валидное состояние,
    // нельзя подменять его прошлым ненулевым значением (висла вчерашняя сумма).
    if (chartPeriod === 'today') {
      return rawChartPerformance;
    }
    const spend = Number(rawChartPerformance?.summary?.spend ?? 0);
    if (spend > 0) {
      lastNonZeroChartPerformanceRef.current = rawChartPerformance;
      chartPerformanceCachedAtRef.current = Date.now();
      return rawChartPerformance;
    }
    return lastNonZeroChartPerformanceRef.current ?? rawChartPerformance;
  }, [rawChartPerformance, chartPeriod]);

  const isStaleChartPerformance = useMemo(() => {
    if (chartPeriod === 'today') return false;
    const spend = Number(rawChartPerformance?.summary?.spend ?? 0);
    if (spend > 0 || !lastNonZeroChartPerformanceRef.current) return false;
    const age = Date.now() - chartPerformanceCachedAtRef.current;
    return age > STALE_CHART_TTL_MS || Boolean(settings?.is_scanning_enabled);
  }, [rawChartPerformance, settings?.is_scanning_enabled, chartPeriod]);

  useEffect(() => {
    const scanAt = settings?.last_scan_at;
    if (scanAt && scanAt !== prevLastScanAtRef.current) {
      lastNonZeroChartPerformanceRef.current = null;
      chartPerformanceCachedAtRef.current = 0;
      prevLastScanAtRef.current = scanAt;
    }
  }, [settings?.last_scan_at]);

  const { data: performanceYesterday } = useQuery({
    queryKey: ['performanceYesterday'],
    queryFn: () => getDashboardPerformance({ period: 'yesterday' }).catch(() => null),
    refetchInterval: 30_000,
  });

  const { data: chartData, dataUpdatedAt: chartDataUpdatedAt } = useQuery({
    queryKey: ['chartData', chartPeriod],
    queryFn: () => getChartData({ period: chartPeriod }).catch(() => null),
    refetchInterval: 10_000,
  });

  const { data: enableRecs } = useQuery({
    queryKey: ['enableRecs'],
    queryFn: () => getEnableRecommendations({ limit: 10 }).catch(() => []),
    refetchInterval: 30_000,
  });

  /* Валидация колонок — проверяем раз в 60с */
  const { data: columnValidation } = useQuery({
    queryKey: ['columnValidation'],
    queryFn: () => validateBrowserColumns().catch((err) => (
      isMissingBrowserSessionError(err) ? emptyColumnValidationResult() : failedColumnValidationResult(err)
    )),
    refetchInterval: 60_000,
    retry: false,
  });

  /* --- WebSocket: realtime-события от observer --- */
  // Строим WS URL из текущего host: ws(s)://host/ws/dashboard
  const wsUrl = (() => {
    const proto = window.location.protocol === 'https:' ? 'wss:' : 'ws:';
    return `${proto}//${window.location.host}/ws/dashboard`;
  })();

  const wsDisconnectedSinceRef = useRef(null);

  const { connected: wsConnected } = useWebSocket(wsUrl, {
    enabled: true,
    autoReconnect: true,
    onMessage: (event) => {
      // scan_finished → инвалидируем основные данные дашборда
      if (event.type === 'scan_finished') {
        queryClient.invalidateQueries({ queryKey: ['dashboardBatch'] });
        queryClient.invalidateQueries({ queryKey: ['observerSettings'] });
        queryClient.invalidateQueries({ queryKey: ['chartData'] });
        queryClient.invalidateQueries({ queryKey: ['performanceToday'] });
      }
      // alert_created → инвалидируем алерты и инциденты
      if (event.type === 'alert_created') {
        queryClient.invalidateQueries({ queryKey: ['dashboardBatch'] });
      }
      // task_changed → инвалидируем задачи
      if (event.type === 'task_changed') {
        queryClient.invalidateQueries({ queryKey: ['dashboardBatch'] });
        queryClient.invalidateQueries({ queryKey: ['enableTasks'] });
      }
    },
  });

  // Fallback на polling: если WS не подключён — polling каждые 5с, иначе каждые 60с
  useEffect(() => {
    if (!wsConnected) {
      if (wsDisconnectedSinceRef.current === null) {
        wsDisconnectedSinceRef.current = Date.now();
      }
    } else {
      wsDisconnectedSinceRef.current = null;
    }
  }, [wsConnected]);

  // WS подключён → polling для критичных данных раз в 60с (вместо 5с)
  const batchPollInterval = wsConnected ? 60_000 : 5_000;
  const enableTasksPollInterval = wsConnected ? 60_000 : 5_000;

  /* --- Realtime: stats + incidents + disable-tasks одним batch-запросом --- */
  const { data: dashboardBatch } = useQuery({
    queryKey: ['dashboardBatch', DASHBOARD_BATCH_QUERY],
    queryFn: () => getDashboardBatch(DASHBOARD_BATCH_QUERY),
    refetchInterval: batchPollInterval,
  });

  const stats = dashboardBatch?.stats;
  const rawIncidents = dashboardBatch?.incidents;
  const disableTasks = dashboardBatch?.disable_tasks ?? [];

  const { data: enableTasks } = useQuery({
    queryKey: ['enableTasks'],
    queryFn: () => getEnableTasks({ limit: 20 }).catch(() => []),
    refetchInterval: enableTasksPollInterval,
  });

  /* Нормализуем инциденты из сырых данных */
  const incidents = useMemo(
    () => normalizeIncidentList(rawIncidents ?? []),
    [rawIncidents],
  );

  /* Баг 5: при возврате из фона инвалидируем только stale-запросы, не всё подряд */
  useRefreshOnResume(() => {
    queryClient.invalidateQueries({ predicate: (q) => q.state.isStale });
  });

  const handleToggle = async () => {
    if (toggling || !settings) return;
    setToggling(true);
    /* Баг 4: сохраняем предыдущее состояние для отката при ошибке */
    const previousData = queryClient.getQueryData(['observerSettings']);
    /* Оптимистично обновляем кеш настроек */
    queryClient.setQueryData(['observerSettings'], (cur) =>
      cur ? { ...cur, is_scanning_enabled: !cur.is_scanning_enabled } : cur,
    );
    try {
      await toggleScanning(!settings.is_scanning_enabled);
    } catch (e) {
      /* Баг 4: откатываем оптимистичное обновление при ошибке */
      queryClient.setQueryData(['observerSettings'], previousData);
      setError(`Ошибка переключения сканирования: ${e.message}`);
    } finally {
      setToggling(false);
    }
  };

  const handleResume = async () => {
    if (toggling || !settings) return;
    setToggling(true);
    const previousData = queryClient.getQueryData(['observerSettings']);
    queryClient.setQueryData(['observerSettings'], (cur) =>
      cur ? { ...cur, is_scanning_enabled: true, pause_until: null } : cur,
    );
    try {
      await toggleScanning(true);
    } catch (e) {
      queryClient.setQueryData(['observerSettings'], previousData);
      setError(`Ошибка возобновления сканирования: ${e.message}`);
    } finally {
      setToggling(false);
    }
  };

  const handleAutoEnableToggle = async () => {
    if (togglingAutoEnable || !settings) return;
    setTogglingAutoEnable(true);
    const previousData = queryClient.getQueryData(['observerSettings']);
    queryClient.setQueryData(['observerSettings'], (cur) =>
      cur ? { ...cur, auto_enable_recommendations: !cur.auto_enable_recommendations } : cur,
    );
    try {
      await toggleAutoEnable(!settings.auto_enable_recommendations);
    } catch (e) {
      queryClient.setQueryData(['observerSettings'], previousData);
      setError(`Ошибка переключения авто-включения: ${e.message}`);
    } finally {
      setTogglingAutoEnable(false);
    }
  };

  const handleScanNow = async () => {
    if (scanning) return;
    setScanning(true);
    /* Баг 9: читаем актуальное значение из кеша, не из замыкания */
    const batchKey = ['dashboardBatch', DASHBOARD_BATCH_QUERY];
    const scanStartedAt = queryClient.getQueryData(batchKey)?.stats?.last_scan_at ?? null;
    try {
      await triggerScanNow();
      const deadline = Date.now() + 120_000;
      /* Баг 2: используем ref, чтобы cleanup всегда видел актуальный timerId */
      const poll = async () => {
        if (Date.now() > deadline) { setScanning(false); return; }
        try {
          const fresh = await getDashboardBatch(DASHBOARD_BATCH_QUERY);
          if (fresh?.stats?.last_scan_at && fresh.stats.last_scan_at !== scanStartedAt) {
            queryClient.setQueryData(batchKey, fresh);
            setScanning(false);
            return;
          }
        } catch {
          /* ожидаем следующей итерации */
        }
        scanTimerRef.current = setTimeout(poll, 4000);
      };
      scanTimerRef.current = setTimeout(poll, 4000);
      return () => { if (scanTimerRef.current) clearTimeout(scanTimerRef.current); };
    } catch (e) {
      setScanning(false);
      setError(`Ошибка запуска скана: ${e.message}`);
    }
  };

  const handleRecheckColumns = async () => {
    if (columnRechecking) return;
    setColumnRechecking(true);
    try {
      const result = await validateBrowserColumns({ startIfMissing: true });
      queryClient.setQueryData(['columnValidation'], result);
    } catch (err) {
      queryClient.setQueryData(['columnValidation'], failedColumnValidationResult(err));
    } finally {
      setColumnRechecking(false);
    }
  };

  const handleDisable = async (fbAdId) => {
    try {
      await createDisableTask(fbAdId);
      /* Инвалидируем кеш задач на отключение */
      queryClient.invalidateQueries({ queryKey: ['dashboardBatch'] });
    } catch (e) {
      setError(`Ошибка отключения: ${e.message}`);
    }
  };

  const handleEnableTask = async (recId) => {
    try {
      await createEnableTaskFromRecommendation(recId);
      /* Инвалидируем рекомендации и задачи на включение */
      queryClient.invalidateQueries({ queryKey: ['enableRecs'] });
      queryClient.invalidateQueries({ queryKey: ['enableTasks'] });
    } catch (e) {
      setError(`Ошибка включения: ${e.message}`);
    }
  };

  const handleRetry = async (taskId) => {
    try {
      await retryDisableTask(taskId);
      /* Инвалидируем кеш задач на отключение */
      queryClient.invalidateQueries({ queryKey: ['dashboardBatch'] });
    } catch (e) {
      setError(`Ошибка повтора: ${e.message}`);
    }
  };

  const handleCancelDisableTask = async (taskId, adName) => {
    const label = adName ? ` для ${adName}` : '';
    if (!window.confirm(`Удалить задачу отключения${label} из очереди?`)) {
      return;
    }

    try {
      await cancelDisableTask(taskId);
      /* Инвалидируем кеш задач и инцидентов после ручного удаления */
      queryClient.invalidateQueries({ queryKey: ['dashboardBatch'] });
    } catch (e) {
      setError(`Ошибка удаления из очереди: ${e.message}`);
    }
  };

  const activeIncidents = incidents.filter(
    (i) => i.current_state !== 'NORMAL' && !isDeliveryDisabled(i.delivery_status),
  );

  const handleSelectIncident = (incident) => {
    if (!incident?.fb_ad_id) return;
    onNavigate?.(`/ads?fb_ad_id=${encodeURIComponent(incident.fb_ad_id)}`);
  };

  const handleBannerStopClick = () => {
    if ((stats?.ads_in_stop ?? 0) > 0) {
      onNavigate?.('/ads?state=STOP_SENT');
      return;
    }
    alertTrayRef.current?.scrollIntoView({ behavior: 'smooth', block: 'start' });
  };

  const handleBannerWarningClick = () => {
    if ((stats?.ads_in_warning ?? 0) > 0) {
      onNavigate?.('/ads?state=WARNING_SENT');
      return;
    }
    alertTrayRef.current?.scrollIntoView({ behavior: 'smooth', block: 'start' });
  };

  return (
    <div className="space-y-md">
      {/* Ошибка */}
      {error && (
        <div className="rounded-md bg-danger-muted border border-danger/30 px-4 py-3 text-sm text-danger">
          {error}
          <button onClick={() => setError(null)} className="ml-3 text-danger/60 hover:text-danger">✕</button>
        </div>
      )}

      {/* Валидация колонок */}
      <ColumnValidationBanner
        validationResult={columnValidation}
        onRecheck={handleRecheckColumns}
        checking={columnRechecking}
      />

      <DashboardOperations
        alertTrayRef={alertTrayRef}
        stats={stats}
        settings={settings}
        vision={visionStatus}
        scanning={scanning}
        onToggle={handleToggle}
        onResume={handleResume}
        onScanNow={handleScanNow}
        onAutoEnableToggle={handleAutoEnableToggle}
        onStopClick={handleBannerStopClick}
        onWarningClick={handleBannerWarningClick}
        activeIncidents={activeIncidents}
        disableTasks={disableTasks}
        onSelectIncident={handleSelectIncident}
        onDisable={handleDisable}
        onEnableScanning={handleToggle}
        enableTasks={enableTasks}
        enableRecs={enableRecs ?? []}
        onRetryDisable={handleRetry}
        onCancelDisable={handleCancelDisableTask}
        onCreateEnableTask={handleEnableTask}
      />

      <div className="mb-md">
        <ObserverStatusTile />
      </div>

      <HeroKPIStrip performance={performance} performanceYesterday={performanceYesterday} />

      <div className="mb-md">
        <AIBriefingCard />
      </div>

      <div className="card-grid grid-cols-1 lg:grid-cols-[1fr_0.54fr]">
        <div className="panel h-full min-w-0 p-4">
          <div className="mb-1 flex flex-wrap items-center justify-between gap-2">
            <div className="flex flex-wrap items-center gap-2">
              <div className="flex gap-1 rounded-md bg-elevated p-0.5">
                {[
                  { id: 'today', label: 'Сегодня' },
                  { id: '7d', label: '7 дней' },
                ].map((opt) => (
                  <button
                    key={opt.id}
                    type="button"
                    className={`rounded px-2 py-0.5 text-2xs font-medium transition-colors ${
                      chartPeriod === opt.id
                        ? 'bg-surface text-primary'
                        : 'text-secondary hover:text-primary'
                    }`}
                    onClick={() => setChartPeriod(opt.id)}
                  >
                    {opt.label}
                  </button>
                ))}
              </div>
              {isStaleChartPerformance && (
                <span className="rounded bg-warning/15 px-2 py-0.5 text-[10px] font-medium text-warning">
                  данные предыдущего цикла
                </span>
              )}
            </div>
            <UpdatedAgoLabel updatedAt={chartDataUpdatedAt} />
          </div>
          <div className="flex flex-1 flex-col justify-center">
            <SpendAlertsChart
              spendData={chartPerformance?.timeline ?? []}
              alertsData={chartData?.alerts_by_hour ?? []}
              period={chartPeriod}
            />
          </div>
        </div>

        <div className="grid h-full min-w-0 grid-cols-1 gap-md lg:grid-rows-[1fr_1fr]">
          <div className="panel p-4">
            <CampaignScorecard
              stats={stats}
              statsYesterday={null}
              onStateClick={(state) => onNavigate?.(`/ads?state=${state}`)}
            />
          </div>
          <div className="panel p-4">
            <RuleViolationRanking data={chartData?.rule_violations ?? []} />
          </div>
        </div>
      </div>

      {/* ЗОНА 3: Аналитика кампаний — свёрнута по умолчанию */}
      <hr className="border-border/40 my-1" />
      <div className="panel overflow-hidden">
        <button
          type="button"
          className="flex w-full items-center justify-between px-4 py-3 text-left"
          onClick={() => setAnalyticsOpen((v) => !v)}
        >
          <span className="text-[10px] font-semibold uppercase tracking-widest text-muted/70">Аналитика кампаний</span>
          <span className="text-muted text-sm">{analyticsOpen ? '▲' : '▼'}</span>
        </button>
        {analyticsOpen && (
          <div className="space-y-md px-4 pb-4">
            <div className="flex flex-wrap justify-end gap-1 rounded-md bg-elevated p-1">
          {ANALYTICS_TABS.map((tab) => (
            <button
              key={tab.id}
              type="button"
              className={`rounded px-2.5 py-1 text-2xs font-medium transition-colors ${
                analyticsView === tab.id
                  ? 'bg-surface text-primary'
                  : 'text-secondary hover:text-primary'
              }`}
              onClick={() => setAnalyticsView(tab.id)}
            >
              {tab.label}
            </button>
          ))}
            </div>

            {analyticsView === 'cpr' && (
              <div className="panel p-4">
                <CampaignComparativeBars data={performance?.campaigns ?? []} />
              </div>
            )}

      {analyticsView === 'budget' && (
        <div className="panel p-4">
          <BudgetOverrunChart
            data={chartData?.campaign_budget_deltas ?? []}
            period={chartPeriod}
          />
        </div>
      )}

      {analyticsView === 'campaigns' && (
        <div className="panel p-4">
          <CampaignBreakdownTable data={performance?.campaigns ?? []} />
        </div>
      )}

      {analyticsView === 'offers' && (
        <div className="panel p-4">
          <OfferLeaderboard data={performance?.campaigns ?? []} />
        </div>
      )}

      {analyticsView === 'funnel' && (
        <div className="panel p-4">
          <FunnelChart funnel={performance?.funnel ?? []} />
        </div>
      )}
          </div>
        )}
      </div>

      {/* Состояние системы — collapsible */}
      <div className="panel overflow-hidden">
        <button
          className="flex w-full items-center justify-between px-4 py-3 text-left"
          onClick={() => setHealthMapOpen(v => !v)}
        >
          <span className="text-2xs font-bold uppercase tracking-widest text-muted">Состояние системы</span>
          <span className="text-muted text-sm">{healthMapOpen ? '▲' : '▼'}</span>
        </button>
        {healthMapOpen && (
          <div className="px-4 pb-4">
            <Suspense fallback={<div className="h-32 animate-pulse bg-elevated rounded" />}>
              <HealthMapPage embedded />
            </Suspense>
          </div>
        )}
      </div>

    </div>
  );
}
