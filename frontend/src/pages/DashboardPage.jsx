import { lazy, Suspense, useEffect, useMemo, useRef, useState } from 'react';
import { useQuery, useQueryClient } from '@tanstack/react-query';
import { fmt$ as _fmt$, fmtN as _fmtN, fmtRoas as _fmtRoas } from '../utils/formatters.js';
import {
  getDashboardStats,
  getDashboardIncidents,
  getDisableTasks,
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
  getSpendHistory,
  createDisableTask,
  createEnableTaskFromRecommendation,
  validateBrowserColumns,
} from '../api.js';
import { useRefreshOnResume } from '../hooks/useRefreshOnResume.js';
import { AlertTray } from '../components/AlertTray.jsx';
import { CampaignScorecard, FunnelChart } from '../components/CampaignScorecard.jsx';

import { TaskQueuePanel } from '../components/TaskQueuePanel.jsx';
import { BudgetOverrunChart } from '../components/BudgetOverrunChart.jsx';
import { CampaignBreakdownTable } from '../components/CampaignBreakdownTable.jsx';
import { RuleViolationRanking } from '../components/RuleViolationRanking.jsx';
import { OfferLeaderboard } from '../components/OfferLeaderboard.jsx';
import { CampaignComparativeBars } from '../components/CampaignComparativeBars.jsx';
import { SpendAlertsChart } from '../components/SpendAlertsChart.jsx';

const HealthMapPage = lazy(() => import('./HealthMapPage.jsx'));

/* === Вспомогательные компоненты === */

/** Баннер ошибки валидации колонок — показывается если колонки отсутствуют. */
function ColumnValidationBanner({ validationResult, onRecheck }) {
  if (!validationResult || validationResult.valid) return null;

  const missing = validationResult.missing_columns || [];
  if (missing.length === 0) return null;

  return (
    <div className="rounded-md bg-danger-muted border border-danger/30 px-4 py-3 mb-md">
      <div className="flex items-start gap-3">
        <span className="status-dot bg-danger animate-pulse-dot mt-0.5" />
        <div className="flex-1">
          <p className="text-sm font-semibold text-danger mb-1">
            Отсутствуют колонки в таблице Ads Manager
          </p>
          <p className="text-2xs text-danger/80 mb-2">
            Сервис не может корректно сканировать объявления. Добавьте следующие колонки в таблицу Ads Manager:
          </p>
          <div className="flex flex-wrap gap-1.5 mb-2">
            {missing.map((col) => (
              <span key={col} className="rounded bg-danger/20 px-2 py-0.5 text-2xs font-mono text-danger">
                {col}
              </span>
            ))}
          </div>
          <button
            className="text-2xs text-danger/70 hover:text-danger underline"
            onClick={onRecheck}
          >
            Проверить снова
          </button>
        </div>
      </div>
    </div>
  );
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

/** Hero-баннер алертов — полная ширина, первое что видит медиабаер */
function AlertBanner({ stats }) {
  const stopCount = stats?.ads_in_stop ?? 0;
  const warnCount = stats?.ads_in_warning ?? 0;

  if (stopCount === 0 && warnCount === 0) {
    return (
      <div className="panel flex items-center gap-3 px-4 py-3 mb-md border-success/30 bg-success-muted">
        <span className="status-dot bg-success animate-pulse-dot" />
        <span className="text-sm font-medium text-success">Все объявления в норме</span>
      </div>
    );
  }

  return (
    <div className="panel flex items-center gap-4 px-4 py-3 mb-md">
      {stopCount > 0 && (
        <div className="flex items-center gap-2">
          <span className="status-dot bg-danger animate-pulse-dot" />
          <span className="font-mono text-2xl text-danger">{stopCount}</span>
          <span className="text-2xs uppercase tracking-wider text-danger/70">СТОП</span>
        </div>
      )}
      {warnCount > 0 && (
        <div className="flex items-center gap-2">
          <span className="status-dot bg-warning animate-pulse-dot" />
          <span className="font-mono text-2xl text-warning">{warnCount}</span>
          <span className="text-2xs uppercase tracking-wider text-warning/70">WARNING</span>
        </div>
      )}
    </div>
  );
}

/** Парсинг адаптивного интервала и уровня угрозы из статуса observer */
function parseObserverStatusMessage(msg) {
  if (!msg) return { intervalSec: null, threatLevel: null };
  const intervalMatch = msg.match(/интервал:\s*(\d+)/);
  const threatMatch = msg.match(/Угроза:\s*(\w+)/);
  return {
    intervalSec: intervalMatch ? parseInt(intervalMatch[1], 10) : null,
    threatLevel: threatMatch ? threatMatch[1] : null,
  };
}

function formatScanDuration(seconds) {
  const value = Number(seconds);
  if (!Number.isFinite(value) || value <= 0) return '—';
  if (value < 60) return `${Math.round(value)}с`;
  const minutes = Math.floor(value / 60);
  const rest = Math.round(value % 60);
  return rest > 0 ? `${minutes}м ${rest}с` : `${minutes}м`;
}

function getVisionRuntimeMeta(vision) {
  const status = String(vision?.runtime_status || 'NOT_CONFIGURED').toUpperCase();
  if (status === 'READY') {
    return {
      label: vision?.cdp_port ? `CDP ${vision.cdp_port}` : 'CDP готов',
      color: 'bg-success-muted text-success border-success/30',
      message: vision?.runtime_status_message || 'CDP-порт готов.',
    };
  }
  if (status === 'NOT_RUNNING') {
    return {
      label: 'Vision не запущен',
      color: 'bg-warning/10 text-warning border-warning/30',
      message: vision?.runtime_status_message || 'Профиль стартует при первом обращении к браузеру.',
    };
  }
  if (status === 'MISSING_CDP' || status === 'CDP_NOT_READY') {
    return {
      label: 'Vision без CDP',
      color: 'bg-danger-muted text-danger border-danger/30',
      message: vision?.runtime_status_message || 'Профиль запущен, но CDP-порт недоступен.',
    };
  }
  if (status === 'API_UNAVAILABLE') {
    return {
      label: 'Vision API недоступен',
      color: 'bg-danger-muted text-danger border-danger/30',
      message: vision?.runtime_status_message || 'Не удалось подключиться к Vision API.',
    };
  }
  return {
    label: 'Vision не настроен',
    color: 'bg-elevated text-muted border-border',
    message: vision?.runtime_status_message || 'Vision X-Token или профиль ещё не настроены.',
  };
}

/** Маппинг уровня угрозы → цвет и текст бейджа */
const THREAT_BADGE = {
  IMMEDIATE: { label: 'Ре-скан', color: 'bg-red-500/20 text-red-400 animate-pulse' },
  CRITICAL:  { label: 'Критично', color: 'bg-red-500/20 text-red-400' },
  ELEVATED:  { label: 'Повышенно', color: 'bg-amber-500/20 text-amber-400' },
  ACTIVE:    { label: 'Активно', color: 'bg-sky-500/20 text-sky-400' },
  CALM:      { label: 'Спокойно', color: 'bg-emerald-500/20 text-emerald-400' },
  IDLE:      { label: 'Ожидание', color: 'bg-zinc-500/20 text-zinc-400' },
};

const ANALYTICS_TABS = [
  { id: 'cpr', label: 'CPR' },
  { id: 'budget', label: 'Бюджет' },
  { id: 'campaigns', label: 'Кампании' },
  { id: 'offers', label: 'Офферы' },
  { id: 'funnel', label: 'Воронка' },
];

/** Полоса статуса сканирования */
function ScanStatusBar({
  settings,
  onToggle,
  onScanNow,
  scanning,
  lastScanAt,
  observerStatus,
  observerStatusMessage,
  scanIntervalSec,
  scanJitterSec,
  scanThreatLevel,
  nextScanAt,
  vision,
}) {
  const [secsLeft, setSecsLeft] = useState(null);
  const parsedStatus = parseObserverStatusMessage(observerStatusMessage);
  const intervalSec = scanIntervalSec ?? parsedStatus.intervalSec;
  const threatLevel = scanThreatLevel ?? parsedStatus.threatLevel;
  const badge = threatLevel ? THREAT_BADGE[threatLevel] : null;
  const visionMeta = getVisionRuntimeMeta(vision);

  useEffect(() => {
    const explicitNextScanAt = nextScanAt ? new Date(nextScanAt).getTime() : null;
    const fallbackNextScanAt = lastScanAt && intervalSec
      ? new Date(lastScanAt).getTime() + intervalSec * 1000
      : null;
    const targetScanAt = Number.isFinite(explicitNextScanAt) ? explicitNextScanAt : fallbackNextScanAt;
    if (!targetScanAt) { setSecsLeft(null); return; }
    const tick = () => setSecsLeft(Math.max(0, Math.round((targetScanAt - Date.now()) / 1000)));
    tick();
    const id = setInterval(tick, 1000);
    return () => clearInterval(id);
  }, [lastScanAt, intervalSec, nextScanAt]);

  const isEnabled = settings?.is_scanning_enabled ?? false;
  const isWaitingNextScan = isEnabled
    && observerStatus === 'RUNNING'
    && (Boolean(nextScanAt) || /^Ожидаем следующий цикл/i.test(observerStatusMessage || ''));
  const isActivelyScanning = scanning || (observerStatus === 'RUNNING' && !isWaitingNextScan);

  let statusText = '';
  let statusDetail = '';
  let statusColor = 'text-muted';
  let showDot = false;
  if (!isEnabled) {
    statusText = 'Выключено';
  } else if (observerStatus === 'WAITING_BROWSER') {
    const rawMessage = (observerStatusMessage || '').trim();
    const isEnableQueue = /^Браузер занят задачами включения/i.test(rawMessage);
    const isDisableQueue = /^Браузер занят задачами отключения/i.test(rawMessage);
    statusText = isEnableQueue
      ? 'Браузер занят включением объявлений'
      : isDisableQueue
        ? 'Браузер занят отключением объявлений'
        : 'Браузер занят обработкой объявлений';
    const normalizedReason = rawMessage
      .replace(/^Браузер занят задачами (отключения|включения)\.?\s*/i, '')
      .trim();
    if (isEnableQueue) {
      statusDetail = normalizedReason
        ? `Идёт очередь включения. ${normalizedReason} Скан продолжится автоматически.`
        : 'Идёт очередь включения объявлений. Скан продолжится автоматически после её завершения.';
    } else if (isDisableQueue) {
      statusDetail = normalizedReason
        ? `Идёт очередь отключения. ${normalizedReason} Скан продолжится автоматически.`
        : 'Идёт очередь отключения объявлений. Скан продолжится автоматически после её завершения.';
    } else {
      statusDetail = normalizedReason
        ? `Идёт фоновая обработка. ${normalizedReason} Скан продолжится автоматически.`
        : 'Браузер временно занят фоновыми задачами. Скан продолжится автоматически после их завершения.';
    }
    statusColor = 'text-warning';
  } else if (observerStatus === 'DISABLING') {
    statusText = 'Отключаем объявления…';
    statusColor = 'text-warning';
    showDot = true;
  } else if (observerStatus === 'ERROR') {
    statusText = 'Сканер не подключён к браузеру';
    statusDetail = observerStatusMessage
      ? `${observerStatusMessage} Сканирование не выполняется, пока подключение не восстановится.`
      : 'Сканирование не выполняется, пока не восстановится подключение к браузеру.';
    statusColor = 'text-danger';
  } else if (observerStatus === 'PAUSED') {
    statusText = observerStatusMessage ?? 'Пауза';
    statusColor = 'text-warning';
  } else if (isWaitingNextScan) {
    statusText = 'Ожидание';
    statusDetail = nextScanAt ? `Следующий скан запланирован с учетом jitter.` : '';
    statusColor = 'text-secondary';
  } else if (isActivelyScanning) {
    statusText = 'Сканирую…';
    statusColor = 'text-success';
    showDot = true;
  } else if (isEnabled) {
    statusText = 'Ожидание';
    statusColor = 'text-secondary';
  }

  const showCountdown = isEnabled && isWaitingNextScan && secsLeft !== null && secsLeft > 0;
  const showLastScan = isEnabled && !isActivelyScanning && !showCountdown && lastScanAt;

  return (
    <div className="panel flex items-center gap-3 px-4 py-2.5 mb-md flex-wrap">
      {/* Тогл сканирования */}
      <button
        onClick={onToggle}
        className="toggle-track"
        data-active={isEnabled}
        role="switch"
        aria-checked={isEnabled}
        aria-label={isEnabled ? 'Выключить сканирование' : 'Включить сканирование'}
      >
        <span className="toggle-knob" data-active={isEnabled} />
      </button>

      <span className="text-2xs font-bold uppercase tracking-widest text-secondary">
        Скан
      </span>

      {statusText && (
        <span className="flex flex-col">
          <span className={`flex items-center gap-1.5 text-2xs font-medium ${statusColor}`}>
            {showDot && <span className="status-dot bg-success animate-pulse-dot" />}
            {statusText}
          </span>
          {statusDetail && (
            <span className="text-[11px] leading-tight text-muted">
              {statusDetail}
            </span>
          )}
        </span>
      )}

      {badge && (
        <span className={`rounded-full px-2 py-0.5 text-2xs font-semibold ${badge.color}`}>
          {badge.label}{intervalSec ? ` ${formatScanDuration(intervalSec)}` : ''}
          {scanJitterSec ? ` ±${formatScanDuration(scanJitterSec)}` : ''}
        </span>
      )}

      {visionMeta && (
        <span
          className={`rounded-full border px-2 py-0.5 text-2xs font-semibold ${visionMeta.color}`}
          title={visionMeta.message}
        >
          {visionMeta.label}
        </span>
      )}

      {showCountdown && (
        <span className="font-mono text-sm font-semibold text-secondary">
          {Math.floor(secsLeft / 60)}:{String(secsLeft % 60).padStart(2, '0')}
        </span>
      )}
      {showLastScan && (
        <span className="font-mono text-2xs text-muted">
          {new Date(lastScanAt).toLocaleTimeString('ru-RU')}
        </span>
      )}

      <button
        className="btn-ghost ml-auto flex items-center gap-1.5"
        onClick={onScanNow}
        disabled={scanning}
      >
        <span className={scanning ? 'animate-spin' : ''}>↻</span>
        {scanning ? 'Сканирую' : 'Обновить'}
      </button>
    </div>
  );
}

/** KPI-полоса */
function HeroKPIStrip({ performance, performanceYesterday }) {
  const s = performance?.summary;
  const y = performanceYesterday?.summary;
  const fmt$ = _fmt$;
  const fmtN = _fmtN;
  const fmtPct = (v) => (v != null ? `${Number(v).toFixed(1)}%` : '—');
  const fmtRoas = _fmtRoas;

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
    <div className="grid grid-cols-2 sm:grid-cols-3 lg:grid-cols-6 gap-3 mb-md">
      {kpis.map((kpi) => (
        <div key={kpi.label} className="kpi-card group hover:border-border-hover transition-all">
          <span className="kpi-label">{kpi.label}</span>
          <span className={`kpi-value ${kpi.color}`}>{kpi.value}</span>
          <Delta today={s?.[kpi.key]} yesterday={y?.[kpi.key]} />
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
  const [healthMapOpen, setHealthMapOpen] = useState(false);
  const [analyticsView, setAnalyticsView] = useState('cpr');
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
  // Держим последние ненулевые данные чтобы не мигать нулями во время скана
  const lastNonZeroPerformanceRef = useRef(null);
  const performance = useMemo(() => {
    const spend = Number(rawPerformance?.summary?.spend ?? 0);
    if (spend > 0) lastNonZeroPerformanceRef.current = rawPerformance;
    return lastNonZeroPerformanceRef.current ?? rawPerformance;
  }, [rawPerformance]);

  const { data: performanceYesterday } = useQuery({
    queryKey: ['performanceYesterday'],
    queryFn: () => getDashboardPerformance({ period: 'yesterday' }).catch(() => null),
    refetchInterval: 30_000,
  });

  const { data: chartData } = useQuery({
    queryKey: ['chartDataToday'],
    queryFn: () => getChartData({ period: 'today' }).catch(() => null),
    refetchInterval: 30_000,
  });

  const { data: spendHistory } = useQuery({
    queryKey: ['spendHistory24h'],
    // spendHistory — массив, поэтому fallback тоже массив
    queryFn: () => getSpendHistory({ hours: 24 }).catch(() => []),
    refetchInterval: 30_000,
  });

  const { data: enableRecs } = useQuery({
    queryKey: ['enableRecs'],
    queryFn: () => getEnableRecommendations({ limit: 10 }).catch(() => []),
    refetchInterval: 30_000,
  });

  /* Валидация колонок — проверяем раз в 60с */
  const { data: columnValidation, refetch: refetchColumns } = useQuery({
    queryKey: ['columnValidation'],
    queryFn: () => validateBrowserColumns().catch(() => ({ valid: true, missing_columns: [], found_columns: [], error_message: '' })),
    refetchInterval: 60_000,
    retry: false,
  });

  /* --- Realtime-данные: обновление каждые 5 секунд --- */
  const { data: stats } = useQuery({
    queryKey: ['dashboardStats'],
    queryFn: getDashboardStats,
    refetchInterval: 5_000,
  });

  const { data: rawIncidents } = useQuery({
    queryKey: ['dashboardIncidents'],
    queryFn: () => getDashboardIncidents({ limit: 50 }).catch(() => []),
    refetchInterval: 5_000,
  });

  const { data: disableTasks } = useQuery({
    queryKey: ['disableTasks'],
    queryFn: () => getDisableTasks({ limit: 50 }),
    refetchInterval: 5_000,
  });

  const { data: enableTasks } = useQuery({
    queryKey: ['enableTasks'],
    queryFn: () => getEnableTasks({ limit: 20 }).catch(() => []),
    refetchInterval: 5_000,
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
    const scanStartedAt = queryClient.getQueryData(['dashboardStats'])?.last_scan_at ?? null;
    try {
      await triggerScanNow();
      const deadline = Date.now() + 120_000;
      /* Баг 2: используем ref, чтобы cleanup всегда видел актуальный timerId */
      const poll = async () => {
        if (Date.now() > deadline) { setScanning(false); return; }
        try {
          const fresh = await getDashboardStats();
          if (fresh?.last_scan_at && fresh.last_scan_at !== scanStartedAt) {
            /* Обновляем кеш статистики свежими данными */
            queryClient.setQueryData(['dashboardStats'], fresh);
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

  const handleDisable = async (fbAdId) => {
    try {
      await createDisableTask(fbAdId);
      /* Инвалидируем кеш задач на отключение */
      queryClient.invalidateQueries({ queryKey: ['disableTasks'] });
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
      queryClient.invalidateQueries({ queryKey: ['disableTasks'] });
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
      queryClient.invalidateQueries({ queryKey: ['disableTasks'] });
      queryClient.invalidateQueries({ queryKey: ['dashboardIncidents'] });
      queryClient.invalidateQueries({ queryKey: ['dashboardStats'] });
    } catch (e) {
      setError(`Ошибка удаления из очереди: ${e.message}`);
    }
  };

  const activeIncidents = incidents.filter(
    (i) => i.current_state !== 'NORMAL' && !isDeliveryDisabled(i.delivery_status),
  );

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
        onRecheck={() => refetchColumns()}
      />

      {/* 1. Hero-баннер алертов — ПЕРВОЕ, что видит медиабаер */}
      <AlertBanner stats={stats} />

      {/* 2. Статус сканирования */}
      <ScanStatusBar
        settings={settings}
        onToggle={handleToggle}
        onScanNow={handleScanNow}
        scanning={scanning}
        lastScanAt={stats?.last_scan_at}
        observerStatus={stats?.observer_status}
        observerStatusMessage={stats?.observer_status_message}
        scanIntervalSec={stats?.current_scan_interval_seconds}
        scanJitterSec={stats?.current_scan_jitter_seconds}
        scanThreatLevel={stats?.current_scan_threat_level}
        nextScanAt={stats?.next_scan_at}
        vision={visionStatus}
      />

      {/* 2b. Авто-включение по рекомендациям */}
      <div className="panel flex items-center gap-3 px-4 py-2.5 mb-md">
        <button
          onClick={handleAutoEnableToggle}
          className="toggle-track"
          data-active={settings?.auto_enable_recommendations ?? false}
          role="switch"
          aria-checked={settings?.auto_enable_recommendations ?? false}
          aria-label={(settings?.auto_enable_recommendations ?? false) ? 'Выключить авто-включение' : 'Включить авто-включение'}
        >
          <span className="toggle-knob" data-active={settings?.auto_enable_recommendations ?? false} />
        </button>
        <span className="text-2xs font-bold uppercase tracking-widest text-secondary">
          Авто-включение
        </span>
        <span className="text-2xs text-muted">
          {(settings?.auto_enable_recommendations ?? false)
            ? 'Рекомендации принимаются автоматически'
            : 'Рекомендации требуют ручного подтверждения'}
        </span>
      </div>

      {/* 3. KPI-полоса */}
      <HeroKPIStrip performance={performance} performanceYesterday={performanceYesterday} />

      {/* ЗОНА 2: Оперативный контроль */}
      <hr className="border-border/40 my-1" />
      <p className="text-[10px] font-semibold uppercase tracking-widest text-muted/50 mb-2">Оперативный контроль</p>

      <div className="grid grid-cols-1 lg:grid-cols-[1fr_0.54fr] gap-md items-start">

        {/* === ЛЕВАЯ КОЛОНКА (~65%) === */}
        <div className="space-y-md min-w-0">

          {/* Лента инцидентов */}
          <div className="panel overflow-hidden">
            <AlertTray
              incidents={activeIncidents}
              disableTasks={disableTasks}
              onSelectIncident={() => {}}
              onDisable={handleDisable}
              settings={settings}
              lastScanAt={stats?.last_scan_at}
              onEnableScanning={handleToggle}
            />
          </div>

          {/* Чарт: расход + алерты */}
          <div className="panel p-4">
            <SpendAlertsChart
              spendData={performance?.timeline ?? []}
              alertsData={chartData?.alerts_by_hour ?? []}
            />
          </div>

        </div>

        {/* === ПРАВАЯ КОЛОНКА (~35%) === */}
        <div className="space-y-md min-w-0">

          {/* Очередь задач */}
          <div className="panel">
            <TaskQueuePanel
              disableTasks={disableTasks}
              enableTasks={enableTasks}
              enableRecs={enableRecs ?? []}
              onRetryDisable={handleRetry}
              onCancelDisable={handleCancelDisableTask}
              onCreateEnableTask={handleEnableTask}
            />
          </div>

          {/* Scorecard состояний */}
          <div className="panel p-4">
            <CampaignScorecard
              stats={stats}
              statsYesterday={null}
              performance={performance}
              spendHistory={spendHistory}
              onStateClick={(state) => onNavigate?.(`/ads?state=${state}`)}
            />
          </div>

          {/* Нарушения правил */}
          <div className="panel p-4">
            <RuleViolationRanking data={chartData?.rule_violations ?? []} />
          </div>

        </div>
      </div>

      {/* ЗОНА 3: Аналитика кампаний */}
      <hr className="border-border/40 my-1" />
      <div className="mb-2 flex flex-wrap items-center justify-between gap-2">
        <p className="text-[10px] font-semibold uppercase tracking-widest text-muted/70">Аналитика кампаний</p>
        <div className="flex gap-1 rounded-md bg-elevated p-1">
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
      </div>

      {analyticsView === 'cpr' && (
        <div className="panel p-4">
          <CampaignComparativeBars data={performance?.campaigns ?? []} />
        </div>
      )}

      {analyticsView === 'budget' && (
        <div className="panel p-4">
          <BudgetOverrunChart data={chartData?.campaign_budget_deltas ?? []} />
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
