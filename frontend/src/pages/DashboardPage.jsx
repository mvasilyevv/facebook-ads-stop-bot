import { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import {
  BarChart,
  Bar,
  Cell,
  XAxis,
  YAxis,
  Tooltip,
  ResponsiveContainer,
  ComposedChart,
  Line,
  CartesianGrid,
} from 'recharts';
import {
  getDashboardStats,
  getDashboardIncidents,
  getDisableTasks,
  getEnableRecommendations,
  getEnableTasks,
  getObserverSettings,
  toggleScanning,
  triggerScanNow,
  getChartData,
  restartObserver,
  restartDisableWorker,
  retryDisableTask,
  getDashboardPerformance,
  createEnableTaskFromRecommendation,
} from '../api.js';
import { useAsyncPolling } from '../hooks/useAsyncPolling.js';
import { useRefreshOnResume } from '../hooks/useRefreshOnResume.js';

const ACTIVE_DISABLE_STATUSES = new Set(['PENDING', 'RUNNING', 'RETRYING']);
const STALE_DISABLE_TASK_MS = 5 * 60 * 1000;
const PERFORMANCE_PERIODS = [
  { value: 'today', label: 'Сегодня' },
  { value: '7d', label: '7 дней' },
  { value: '30d', label: '1 месяц' },
];
const SORTABLE_CAMPAIGN_COLUMNS = new Set(['spend', 'deposits', 'spend_per_dep', 'reg_to_dep_rate']);
const STATUS_DISTRIBUTION_META = [
  { apiLabel: 'Норма', label: 'Норма', color: '#00e896' },
  { apiLabel: 'Ранний сигнал', label: 'Ранний сигнал', color: '#4d88ff' },
  { apiLabel: 'Предупреждение', label: 'Предупреждение', color: '#ff9a20' },
  { apiLabel: 'Стоп', label: 'Стоп', color: '#ff2b50' },
  { apiLabel: 'Ожидает OFF', label: 'Ожидает OFF', color: '#a06bff' },
  { apiLabel: 'Отключён', label: 'Отключено', color: '#7a82a0' },
];
const ENABLE_RECOMMENDATION_LEVEL_META = {
  OK: {
    label: 'Нет блокирующих сигналов',
    tone: 'signal',
    icon: 'ℹ️',
    hint: 'Это не гарантия безопасного запуска',
  },
  EARLY_SIGNAL: {
    label: 'Ранний сигнал восстановления',
    tone: 'signal',
    icon: '🔎',
    hint: 'Есть ранний сигнал, проверьте вручную',
  },
  WARNING: {
    label: 'Требует проверки',
    tone: 'warning',
    icon: '⚠️',
    hint: 'Перед запуском нужна проверка',
  },
};
const ENABLE_RECOMMENDATION_LEVEL_ORDER = {
  WARNING: 0,
  EARLY_SIGNAL: 1,
  OK: 2,
};
const ENABLE_TASK_ACTIVE_STATUSES = new Set(['PENDING', 'RUNNING', 'RETRYING']);
const ENABLE_TASK_STATUS_ORDER = {
  PENDING: 0,
  RUNNING: 1,
  RETRYING: 2,
  FAILED: 3,
  SUCCEEDED: 4,
};
const ENABLE_TASK_STATUS_LABELS = {
  PENDING: 'В очереди',
  RUNNING: 'В работе',
  RETRYING: 'На повторе',
  FAILED: 'Ошибка',
  SUCCEEDED: 'Включено',
};
const ENABLE_TASK_STATUS_ICONS = {
  PENDING: '⏳',
  RUNNING: '🔄',
  RETRYING: '🔁',
  FAILED: '❌',
  SUCCEEDED: '✅',
};

function formatTime(isoStr) {
  if (!isoStr) return '—';
  const d = new Date(isoStr);
  return d.toLocaleTimeString('ru-RU', { hour: '2-digit', minute: '2-digit', second: '2-digit' });
}

function timeAgo(isoStr) {
  if (!isoStr) return '—';
  const diff = Math.floor((Date.now() - new Date(isoStr)) / 1000);
  if (diff < 60) return `${diff}с назад`;

  const minutes = Math.floor(diff / 60);
  if (minutes < 60) return `${minutes}м назад`;

  const hours = Math.floor(diff / 3600);
  if (hours < 24) {
    const restMinutes = Math.floor((diff % 3600) / 60);
    return restMinutes > 0 ? `${hours}ч ${restMinutes}м назад` : `${hours}ч назад`;
  }

  const days = Math.floor(diff / 86400);
  const restHours = Math.floor((diff % 86400) / 3600);
  return restHours > 0 ? `${days}д ${restHours}ч назад` : `${days}д назад`;
}

function formatNextRetry(isoStr) {
  if (!isoStr) return '';
  const diff = Math.ceil((new Date(isoStr) - Date.now()) / 1000);
  if (diff <= 0) return 'сейчас';
  if (diff < 60) return `через ${diff}с`;
  return `через ${Math.floor(diff / 60)}м`;
}

function extractListPayload(payload) {
  if (Array.isArray(payload)) return payload;
  if (Array.isArray(payload?.items)) return payload.items;
  if (Array.isArray(payload?.results)) return payload.results;
  if (Array.isArray(payload?.data)) return payload.data;
  return [];
}

function normalizeIncidentSummary(item) {
  if (!item) return null;
  return {
    ...item,
    incident_key: item.incident_key || item.telegram_group_key || item.open_state_token || null,
    current_state: String(item.current_state || item.alert_state || '').toUpperCase() || null,
    current_stage: String(item.current_stage || item.latest_alert_stage || '').toUpperCase() || null,
    latest_alert_stage: String(item.latest_alert_stage || item.current_stage || '').toUpperCase() || null,
    latest_disable_task_status: String(item.latest_disable_task_status || '').toUpperCase() || null,
  };
}

function normalizeIncidentList(payload) {
  return extractListPayload(payload)
    .map(normalizeIncidentSummary)
    .filter(Boolean)
    .sort((left, right) => new Date(getIncidentActivityAt(right) || 0) - new Date(getIncidentActivityAt(left) || 0));
}

function formatMoney(value, digits = 2) {
  if (value == null) return '—';
  return `$${Number(value).toFixed(digits)}`;
}

function formatCount(value) {
  if (value == null) return '—';
  return Number(value).toLocaleString('ru-RU');
}

function formatPercent(value, digits = 1) {
  if (value == null) return '—';
  return `${Number(value).toFixed(digits)}%`;
}

function formatDeltaPercent(value, digits = 1) {
  if (value == null) return '—';
  const amount = Number(value);
  const prefix = amount > 0 ? '+' : '';
  return `${prefix}${amount.toFixed(digits)}%`;
}

function formatDeltaMoney(value, digits = 2) {
  if (value == null) return '—';
  const amount = Number(value);
  const prefix = amount > 0 ? '+' : amount < 0 ? '-' : '';
  return `${prefix}$${Math.abs(amount).toFixed(digits)}`;
}

function ruleLabel(code) {
  const map = {
    cpc_stop: 'Дорогой клик',
    cpl_stop: 'Дорогой лид',
    cpr_stop: 'Дорогая рега',
    regs_no_dep_stop: 'Реги без депозитов',
    spend_no_dep_range: 'Расход без депа',
    spend_with_dep_range: 'Расход с депозитом',
    early_outbound_ctr_signal: 'Слабый CTR исходящих кликов',
    early_lpv_ratio_signal: 'Слабая доходимость до лендинга',
    early_cost_per_lpv_signal: 'Дорогой просмотр лендинга',
  };
  return map[code] || code;
}

function formatRuleSummary(codes) {
  const labels = (codes || []).map(ruleLabel).filter(Boolean);
  if (labels.length === 0) return 'Причина не указана';
  if (labels.length <= 2) return labels.join(' · ');
  return `${labels.slice(0, 2).join(' · ')} +${labels.length - 2}`;
}

function getEnableRecommendationReasonTitle(row) {
  if (row?.reason_title) return row.reason_title;
  if (String(row?.recommendation_level || '').toUpperCase() === 'OK') {
    return 'Нет блокирующих сигналов';
  }
  return formatRuleSummary(row?.matched_rule_codes);
}

function getEnableRecommendationReasonText(row) {
  if (row?.reason_text) return row.reason_text;
  if (String(row?.recommendation_level || '').toUpperCase() === 'OK') {
    return 'По текущим правилам блокирующих сигналов нет.';
  }
  return '';
}

function getEnableRecommendationStateMeta(state) {
  const normalized = String(state || '').toUpperCase();
  if (normalized === 'STALE') {
    return { label: 'Устарело', tone: 'muted', icon: '🕒' };
  }
  if (normalized === 'TASK_CREATED') {
    return { label: 'Задача уже создана', tone: 'warning', icon: '🧩' };
  }
  if (normalized === 'OPEN') {
    return null;
  }
  return null;
}

function performancePeriodLabel(period) {
  return PERFORMANCE_PERIODS.find((item) => item.value === period)?.label || period;
}

function chartGranularityLabel(period) {
  return period === 'today' ? 'По часам' : 'По дням';
}

function isDisableTaskStale(task) {
  const activityAt = task?.updated_at || task?.created_at;
  if (!activityAt || task.status !== 'RUNNING') return false;
  return Date.now() - new Date(activityAt).getTime() >= STALE_DISABLE_TASK_MS;
}

function getDisableTaskActivityAt(task) {
  return task?.updated_at || task?.completed_at || task?.created_at || null;
}

function getIncidentActivityAt(incident) {
  return (
    incident?.last_activity_at ||
    incident?.latest_alert_at ||
    incident?.latest_disable_task_updated_at ||
    incident?.started_at ||
    incident?.created_at ||
    null
  );
}

function getIncidentStageLabel(stage) {
  const normalized = String(stage || '').toUpperCase();
  if (normalized === 'STOP') return 'Стоп-алерт';
  if (normalized === 'WARNING') return 'Предупреждение';
  if (normalized === 'EARLY_SIGNAL') return 'Ранний сигнал';
  return 'Активный инцидент';
}

function getIncidentStateLabel(incident) {
  const state = String(incident?.current_state || '').toUpperCase();
  if (state === 'CLAIMED') return 'Ожидает OFF';
  if (state === 'DISABLED') return 'Отключено';
  if (state === 'STOP_SENT') return 'Стоп-алерт';
  if (state === 'WARNING_SENT') return 'Предупреждение';
  if (state === 'EARLY_SIGNAL_SENT') return 'Ранний сигнал';
  return getIncidentStageLabel(incident?.current_stage || incident?.latest_alert_stage);
}

function getIncidentVariant(incident) {
  if (!incident) return 'default';
  if (incident.needs_manual_attention) return 'stop';
  const state = String(incident.current_state || '').toUpperCase();
  const stage = String(incident.current_stage || incident.latest_alert_stage || '').toUpperCase();
  if (state === 'CLAIMED' || state === 'STOP_SENT' || stage === 'STOP') return 'stop';
  if (state === 'WARNING_SENT' || stage === 'WARNING') return 'warning';
  if (state === 'EARLY_SIGNAL_SENT' || stage === 'EARLY_SIGNAL') return 'signal';
  if (state === 'DISABLED') return 'default';
  return 'default';
}

function getIncidentAdsLink(incident) {
  const state = String(incident?.current_state || '').toUpperCase();
  if (state === 'CLAIMED') return '/ads?view=all&state=CLAIMED';
  if (state === 'DISABLED') return '/ads?view=all&state=DISABLED';
  if (state === 'STOP_SENT') return '/ads?view=all&state=STOP_SENT';
  if (state === 'WARNING_SENT') return '/ads?view=all&state=WARNING_SENT';
  if (state === 'EARLY_SIGNAL_SENT') return '/ads?view=all&state=EARLY_SIGNAL_SENT';
  return '/ads?view=all';
}

function getRequestedBy(task) {
  return task?.requested_by || task?.requested_by_username || null;
}

function getTaskHealth(tasks) {
  const source = tasks || [];
  const active = source.filter((task) => ACTIVE_DISABLE_STATUSES.has(task.status));
  const running = active.filter((task) => task.status === 'RUNNING');
  const retrying = source.filter((task) => task.status === 'RETRYING');
  const failed = source.filter((task) => task.status === 'FAILED');

  return {
    activeCount: active.length,
    retryingCount: retrying.length,
    failedCount: failed.length,
    staleCount: running.filter(isDisableTaskStale).length,
  };
}

function getEnableRecommendationMeta(level) {
  const normalized = String(level || 'OK').toUpperCase();
  return ENABLE_RECOMMENDATION_LEVEL_META[normalized] || ENABLE_RECOMMENDATION_LEVEL_META.OK;
}

function getEnableRecommendationSecondaryLabel(level) {
  const normalized = String(level || 'OK').toUpperCase();
  if (normalized === 'EARLY_SIGNAL') return 'Нужна проверка';
  if (normalized === 'WARNING') return 'Есть риск';
  return null;
}

function parseJsonObject(value) {
  if (!value) return {};
  if (typeof value === 'object') return value;
  if (typeof value !== 'string') return {};
  try {
    const parsed = JSON.parse(value);
    return parsed && typeof parsed === 'object' ? parsed : {};
  } catch {
    return {};
  }
}

function normalizeEnableRecommendation(item) {
  const fallbackId = `enable-reco-${item?.fb_ad_id || 'unknown'}-${item?.updated_at || item?.created_at || item?.live_batch_started_at || item?.reason_title || 'row'}`;
  return {
    ...item,
    id: item?.id ?? item?.event_id ?? item?.recommendation_event_id ?? fallbackId,
    recommendation_level: String(item?.recommendation_level || item?.stage || 'OK').toUpperCase(),
    matched_rule_codes: Array.isArray(item?.matched_rule_codes)
      ? item.matched_rule_codes
      : Array.isArray(item?.matched_rules)
      ? item.matched_rules
      : [],
    metrics_json: parseJsonObject(item?.metrics_json),
  };
}

function normalizeEnableTask(item) {
  const fallbackId = `enable-task-${item?.fb_ad_id || 'unknown'}-${item?.created_at || item?.status || 'row'}`;
  return {
    ...item,
    id: item?.id ?? item?.task_id ?? fallbackId,
    status: String(item?.status || item?.task_status || 'PENDING').toUpperCase(),
  };
}

function normalizeEnableRecommendations(payload) {
  return extractListPayload(payload).map(normalizeEnableRecommendation);
}

function normalizeEnableTasks(payload) {
  return extractListPayload(payload).map(normalizeEnableTask);
}

function sortEnableRecommendations(rows) {
  return [...(rows || [])].sort((left, right) => {
    const leftOrder = ENABLE_RECOMMENDATION_LEVEL_ORDER[left.recommendation_level] ?? 99;
    const rightOrder = ENABLE_RECOMMENDATION_LEVEL_ORDER[right.recommendation_level] ?? 99;
    if (leftOrder !== rightOrder) return leftOrder - rightOrder;
    const leftTime = new Date(left.updated_at || left.created_at || left.live_batch_started_at || 0).getTime();
    const rightTime = new Date(right.updated_at || right.created_at || right.live_batch_started_at || 0).getTime();
    if (leftTime !== rightTime) return rightTime - leftTime;
    return String(left.ad_name || '').localeCompare(String(right.ad_name || ''), 'ru');
  });
}

function sortEnableTasks(rows) {
  return [...(rows || [])].sort((left, right) => {
    const leftOrder = ENABLE_TASK_STATUS_ORDER[left.status] ?? 99;
    const rightOrder = ENABLE_TASK_STATUS_ORDER[right.status] ?? 99;
    if (leftOrder !== rightOrder) return leftOrder - rightOrder;
    const leftTime = new Date(left.created_at || 0).getTime();
    const rightTime = new Date(right.created_at || 0).getTime();
    if (leftTime !== rightTime) return rightTime - leftTime;
    return String(left.ad_name || '').localeCompare(String(right.ad_name || ''), 'ru');
  });
}

function getEnableRecommendationMetrics(row) {
  const metrics = row?.metrics_json || {};
  const items = [];
  if (metrics.spend != null) items.push(`Расход ${formatMoney(metrics.spend)}`);
  if (metrics.cpc != null) items.push(`CPC ${formatMoney(metrics.cpc, 4)}`);
  if (metrics.outbound_ctr != null) items.push(`CTR исх. ${Number(metrics.outbound_ctr).toFixed(2)}%`);
  if (metrics.landing_page_views != null) items.push(`LPV ${formatCount(metrics.landing_page_views)}`);
  if (metrics.leads != null) items.push(`Лиды ${formatCount(metrics.leads)}`);
  if (metrics.registrations != null) items.push(`Реги ${formatCount(metrics.registrations)}`);
  if (metrics.deposits != null) items.push(`Депы ${formatCount(metrics.deposits)}`);
  return items;
}

function getEnableTaskStatusLabel(status) {
  return ENABLE_TASK_STATUS_LABELS[status] || status || 'Неизвестно';
}

function getEnableTaskErrorPreview(message) {
  const normalized = String(message || '').replace(/\s+/g, ' ').trim();
  if (!normalized) return '';
  if (normalized.length <= 96) return normalized;
  return `${normalized.slice(0, 93).trimEnd()}…`;
}

function sortCampaigns(rows, sortState) {
  const source = [...(rows || [])];
  const direction = sortState.direction === 'asc' ? 1 : -1;
  return source.sort((a, b) => {
    const left = a?.[sortState.key];
    const right = b?.[sortState.key];
    const leftNull = left == null;
    const rightNull = right == null;

    if (leftNull && rightNull) {
      return a.campaign.localeCompare(b.campaign, 'ru');
    }
    if (leftNull) return 1;
    if (rightNull) return -1;
    if (left === right) {
      return a.campaign.localeCompare(b.campaign, 'ru');
    }
    return left > right ? direction : -direction;
  });
}

function ChartTooltip({ active, payload, label, formatter }) {
  if (!active || !payload?.length) return null;
  return (
    <div className="chart-tooltip">
      <div className="chart-tooltip__label">{label}</div>
      {payload.map((item) => (
        <div key={item.dataKey} className="chart-tooltip__row" style={{ color: item.color }}>
          <span>{item.name}</span>
          <strong>{formatter ? formatter(item.dataKey, item.value) : item.value}</strong>
        </div>
      ))}
    </div>
  );
}

function SectionHeader({ title, hint, actions, badge, badgeTone = 'neutral' }) {
  return (
    <div className="dashboard-section__header">
      <div className="dashboard-section__title-block">
        {badge ? <span className={`dashboard-section__badge dashboard-section__badge--${badgeTone}`}>{badge}</span> : null}
        <h3 className="dashboard-section__title">{title}</h3>
        {hint && <p className="dashboard-section__subtitle" title={hint}>{hint}</p>}
      </div>
      {actions ? <div className="dashboard-section__actions">{actions}</div> : null}
    </div>
  );
}

function PeriodSwitch({ value, onChange }) {
  return (
    <div className="period-switch" role="tablist" aria-label="Период исторической аналитики">
      {PERFORMANCE_PERIODS.map((option) => (
        <button
          key={option.value}
          type="button"
          className={`period-switch__button ${value === option.value ? 'period-switch__button--active' : ''}`}
          onClick={() => onChange(option.value)}
        >
          {option.label}
        </button>
      ))}
    </div>
  );
}

function AnalyticsScopeSection({ period, onPeriodChange }) {
  return (
    <div className="dashboard-section dashboard-section--scope">
      <SectionHeader
        badge="Период"
        badgeTone="period"
        title="Историческая аналитика"
        hint="Переключатель влияет только на сводку, воронку, динамику и кампании."
        actions={<PeriodSwitch value={period} onChange={onPeriodChange} />}
      />
    </div>
  );
}

function ScanStatusBar({
  settings,
  onToggle,
  onScanNow,
  scanning,
  lastScanAt,
  onRestart,
  observerStatus,
  observerStatusMessage,
  observerHeartbeatAt,
  observerLastError,
}) {
  const [elapsed, setElapsed] = useState(null);
  const timerRef = useRef(null);

  useEffect(() => {
    if (!lastScanAt) {
      setElapsed(null);
      return;
    }
    const tick = () => setElapsed(Math.floor((Date.now() - new Date(lastScanAt)) / 1000));
    tick();
    timerRef.current = setInterval(tick, 1000);
    return () => clearInterval(timerRef.current);
  }, [lastScanAt]);

  const isActive = settings?.is_scanning_enabled;
  const avgInterval = (settings?.interval_seconds || 90) + Math.floor((settings?.jitter_seconds || 10) / 2);
  const remaining = elapsed !== null ? Math.max(0, avgInterval - elapsed) : null;
  const heartbeatAgeSeconds = observerHeartbeatAt
    ? Math.floor((Date.now() - new Date(observerHeartbeatAt)) / 1000)
    : null;
  const hasFreshHeartbeat = heartbeatAgeSeconds !== null && heartbeatAgeSeconds <= Math.max(avgInterval * 2, 45);
  const runtimeReason = observerStatusMessage || observerLastError || '';
  const isRuntimeError = isActive && observerStatus === 'ERROR' && hasFreshHeartbeat;
  const isWaitingConfig = isActive && observerStatus === 'WAITING_CONFIG' && hasFreshHeartbeat;
  const isConnecting = isActive && observerStatus === 'CONNECTING' && hasFreshHeartbeat;
  const isBrowserBusy = isActive && observerStatus === 'WAITING_BROWSER' && hasFreshHeartbeat;
  const isStuck = isActive && elapsed !== null && elapsed > avgInterval * 3 && !hasFreshHeartbeat;

  let statusLabel = 'Сканирование активно';
  let statusHint = null;

  if (!isActive) {
    statusLabel = 'Сканирование остановлено';
  } else if (isRuntimeError) {
    statusLabel = 'Воркер запущен, но не может работать';
    statusHint = runtimeReason || 'Причина пока не сохранена';
  } else if (isWaitingConfig) {
    statusLabel = 'Воркер ждёт настройки Vision';
    statusHint = runtimeReason || 'Нужно указать X-Token и профиль';
  } else if (isConnecting) {
    statusLabel = 'Воркер подключается к браузеру';
    statusHint = runtimeReason || 'Подготавливаем подключение к Vision';
  } else if (isBrowserBusy) {
    statusLabel = 'Браузер занят задачами отключения';
    statusHint = runtimeReason || 'Observer ждёт освобождения браузера';
  } else if (isStuck) {
    statusLabel = 'Воркер не отвечает';
    statusHint = runtimeReason || (
      lastScanAt
        ? `Последний скан ${timeAgo(lastScanAt)} — перезапустите воркер`
        : 'Нет свежего сигнала от воркера'
    );
  }

  const hasProblemState = isRuntimeError || isWaitingConfig || isBrowserBusy || isStuck;

  return (
    <div className={`scan-status-bar ${isActive && !hasProblemState ? 'scan-status-bar--active' : hasProblemState ? 'scan-status-bar--stuck' : 'scan-status-bar--paused'}`}>
      <div className="scan-status-bar__left">
        <span className={`scan-dot ${isActive && !hasProblemState ? 'scan-dot--active' : hasProblemState ? 'scan-dot--stuck' : ''}`} />
        <span className="scan-status-bar__label">{statusLabel}</span>
        {isActive && !hasProblemState && remaining !== null && (
          <span className="scan-status-bar__timer">
            Следующий скан: <strong>{remaining > 0 ? `${remaining}с` : 'сейчас...'}</strong>
          </span>
        )}
        {isActive && hasFreshHeartbeat && (isRuntimeError || isWaitingConfig || isConnecting || isBrowserBusy) && (
          <span className="scan-status-bar__timer">
            Сигнал воркера: <strong>{timeAgo(observerHeartbeatAt)}</strong>
          </span>
        )}
        {isActive && statusHint && (
          <span className="scan-status-bar__stuck-hint">
            {statusHint}
          </span>
        )}
        {lastScanAt && <span className="scan-status-bar__last">Последний: {formatTime(lastScanAt)}</span>}
      </div>
      <div className="scan-status-bar__actions">
        {isActive && !hasProblemState && (
          <button
            className={`scan-now-btn ${scanning ? 'scan-now-btn--active' : ''}`}
            onClick={onScanNow}
            disabled={scanning}
            title="Запустить сканирование немедленно"
          >
            {scanning ? 'Запрошено...' : 'Сейчас'}
          </button>
        )}
        {(isRuntimeError || isStuck) && (
          <button className="restart-btn" onClick={onRestart} title="Перезапустить observer worker">
            Перезапустить воркер
          </button>
        )}
        <button
          className={`scan-toggle-btn ${isActive ? 'scan-toggle-btn--stop' : 'scan-toggle-btn--start'}`}
          onClick={onToggle}
        >
          {isActive ? 'Остановить' : 'Запустить'}
        </button>
      </div>
    </div>
  );
}

function StatCard({ value, label, icon, variant, hint, onClick }) {
  const className = `stat-card stat-card--${variant || 'default'} ${onClick ? 'stat-card--clickable' : ''}`;
  const content = (
    <>
      <span className="stat-card__icon">{icon}</span>
      <span className="stat-card__value">{value ?? '—'}</span>
      <span className="stat-card__label" title={label}>{label}</span>
      {hint ? <span className="stat-card__hint" title={hint}>{hint}</span> : null}
    </>
  );

  if (onClick) {
    return (
      <button className={className} onClick={onClick} type="button">
        {content}
      </button>
    );
  }
  return <div className={className}>{content}</div>;
}

function CompactSummaryStrip({ items, className = '' }) {
  return (
    <div className={`compact-summary-strip ${className}`.trim()}>
      {(items || []).map((item) => {
        const Tag = item.onClick ? 'button' : 'div';
        return (
          <Tag
            key={item.key || item.label}
            className={`compact-summary-strip__item compact-summary-strip__item--${item.tone || 'default'} ${item.onClick ? 'compact-summary-strip__item--clickable' : ''}`}
            onClick={item.onClick}
            type={item.onClick ? 'button' : undefined}
          >
            <span className="compact-summary-strip__eyebrow">
              {item.icon ? <span>{item.icon}</span> : null}
              <span>{item.label}</span>
            </span>
            <strong className="compact-summary-strip__value">{item.value ?? '—'}</strong>
            {item.hint ? (
              <span className="compact-summary-strip__hint" title={item.hint}>
                {item.hint}
              </span>
            ) : null}
          </Tag>
        );
      })}
    </div>
  );
}

function CampaignBudgetDeltaPanel({ rows }) {
  const normalizedRows = (rows || []).map((row) => ({
    ...row,
    budget_delta_percent: Number(row?.budget_delta_percent ?? row?.overrun_percent ?? 0),
    budget_delta_amount: Number(row?.budget_delta_amount ?? row?.overrun_amount ?? 0),
    budget_status: String(row?.budget_status || '').toUpperCase() || null,
    actual_spend: Number(row?.actual_spend || 0),
    ideal_spend: Number(row?.ideal_spend || 0),
    overrun_amount: Number(row?.overrun_amount || 0),
    total_ads: Number(row?.total_ads || 0),
    max_ad_overrun_percent: Number(row?.max_ad_overrun_percent || 0),
    max_ad_overrun_amount: Number(row?.max_ad_overrun_amount || 0),
    affected_ads: Number(row?.affected_ads || 0),
    over_budget_ads: Number(row?.over_budget_ads ?? row?.affected_ads ?? 0),
    under_budget_ads: Number(row?.under_budget_ads || 0),
    on_target_ads: Number(row?.on_target_ads || 0),
  }));
  const hasRows = normalizedRows.length > 0;
  const maxDelta = normalizedRows.reduce(
    (max, row) => Math.max(max, Math.abs(row.budget_delta_percent)),
    0,
  ) || 1;

  return (
    <div className="ops-overrun-panel">
      <div className="ops-overrun-panel__header">
        <div>
          <div className="ops-overrun-panel__eyebrow">Кампании сейчас</div>
          <div className="ops-overrun-panel__title">Отклонение от базовой экономики</div>
        </div>
        <span className="ops-overrun-panel__badge">{formatCount(normalizedRows.length)}</span>
      </div>

      {!hasRows ? (
        <div className="ops-overrun-panel__empty">
          Сейчас нет кампаний, где удалось посчитать факт против базовой экономики.
        </div>
      ) : (
        <div className="ops-overrun-list">
          {normalizedRows.map((row) => {
            const budgetStatus = row.budget_status
              || (row.budget_delta_amount > 0 ? 'OVER' : row.budget_delta_amount < 0 ? 'UNDER' : 'ON_TARGET');
            const rowToneClass = budgetStatus === 'OVER'
              ? 'ops-overrun-row--over'
              : budgetStatus === 'UNDER'
              ? 'ops-overrun-row--under'
              : 'ops-overrun-row--balanced';
            const valueToneClass = budgetStatus === 'OVER'
              ? 'ops-overrun-row__value--over'
              : budgetStatus === 'UNDER'
              ? 'ops-overrun-row__value--under'
              : 'ops-overrun-row__value--balanced';
            const barToneClass = budgetStatus === 'OVER'
              ? 'dashboard-bar-list__fill--stop'
              : budgetStatus === 'UNDER'
              ? 'dashboard-bar-list__fill--normal'
              : 'dashboard-bar-list__fill--balanced';
            const metaParts = [];
            const adsMix = [];
            if (row.over_budget_ads > 0) adsMix.push(`${formatCount(row.over_budget_ads)} выше базы`);
            if (row.under_budget_ads > 0) adsMix.push(`${formatCount(row.under_budget_ads)} ниже базы`);
            if (row.on_target_ads > 0) adsMix.push(`${formatCount(row.on_target_ads)} в базе`);
            if (adsMix.length > 0) {
              metaParts.push(adsMix.join(' · '));
            } else if (row.total_ads > 0) {
              metaParts.push(`${formatCount(row.total_ads)} объявл.`);
            }
            if (budgetStatus === 'OVER' && row.budget_delta_amount > 0) {
              metaParts.push(`перерасход ${formatMoney(row.budget_delta_amount)}`);
            } else if (budgetStatus === 'UNDER' && row.budget_delta_amount < 0) {
              metaParts.push(`экономия ${formatMoney(Math.abs(row.budget_delta_amount))}`);
            } else {
              metaParts.push('ровно в базе');
            }
            if (row.ideal_spend > 0) metaParts.push(`база ${formatMoney(row.ideal_spend)}`);
            metaParts.push(`факт ${formatMoney(row.actual_spend)}`);
            if (row.max_ad_overrun_amount > 0) metaParts.push(`пик ${formatDeltaMoney(row.max_ad_overrun_amount)}`);
            if (row.top_ad_name) metaParts.push(row.top_ad_name);
            if (row.dominant_metric) metaParts.push(row.dominant_metric);

            return (
              <div
                key={`${row.campaign_full || row.campaign}-${row.budget_delta_percent}`}
                className={`ops-overrun-row ${rowToneClass}`}
              >
                <div className="ops-overrun-row__top">
                  <div className="ops-overrun-row__campaign" title={row.campaign_full || row.campaign}>
                    {row.campaign_full || row.campaign || 'Без кампании'}
                  </div>
                  <div className={`ops-overrun-row__value ${valueToneClass}`}>
                    {formatDeltaPercent(row.budget_delta_percent)}
                  </div>
                </div>
                <div className="ops-overrun-row__meta">{metaParts.join(' · ')}</div>
                <div className="dashboard-bar-list__track">
                  <span
                    className={`dashboard-bar-list__fill ${barToneClass}`}
                    style={{ width: `${Math.max(12, (Math.abs(row.budget_delta_percent) / maxDelta) * 100)}%` }}
                  />
                </div>
              </div>
            );
          })}
        </div>
      )}
    </div>
  );
}

function DisableTasksSection({ tasks, onRetry, onRestartStale, restartingDisableTaskId }) {
  const active = (tasks || [])
    .filter((task) => ACTIVE_DISABLE_STATUSES.has(task.status) || task.status === 'FAILED')
    .sort((a, b) => {
      const order = { RUNNING: 0, RETRYING: 1, PENDING: 2, FAILED: 3 };
      const stateDiff = (order[a.status] ?? 99) - (order[b.status] ?? 99);
      if (stateDiff !== 0) return stateDiff;
      return new Date(getDisableTaskActivityAt(b) || 0) - new Date(getDisableTaskActivityAt(a) || 0);
    });

  if (active.length === 0) return null;

  return (
    <div className="dashboard-section">
      <SectionHeader
        badge="Сейчас"
        badgeTone="live"
        title="Очередь отключений"
        hint="В работе, на повторе и с ошибками."
        actions={<span className="badge badge--warning">{active.length}</span>}
      />
      <div className="disable-tasks-list">
        {active.map((task) => (
          <div
            key={task.id}
            className={`disable-task-row disable-task-row--${task.status.toLowerCase()} ${isDisableTaskStale(task) ? 'disable-task-row--stale' : ''}`}
          >
            <div className="disable-task-row__name">{task.ad_name}</div>
            <div className="disable-task-row__info">
              {task.status === 'RUNNING' && (
                <span className={`task-status ${isDisableTaskStale(task) ? 'task-status--stale' : 'task-status--running'}`}>
                  {isDisableTaskStale(task) ? '⚠️ Зависло в браузере' : '🔄 Выключаем в браузере'}
                  <span className="task-status__retry"> · {timeAgo(getDisableTaskActivityAt(task))}</span>
                </span>
              )}
              {task.status === 'PENDING' && (
                <span className="task-status task-status--pending">
                  ⏳ В очереди (попытка {task.attempt_count + 1})
                </span>
              )}
              {task.status === 'RETRYING' && (
                <span className="task-status task-status--retrying">
                  🔁 Повтор {task.attempt_count}/10
                  {task.next_retry_at && (
                    <span className="task-status__retry"> · {formatNextRetry(task.next_retry_at)}</span>
                  )}
                </span>
              )}
              {task.status === 'FAILED' && (
                <span className="task-status task-status--failed">
                  ❌ {task.last_error || 'неизвестно'}
                </span>
              )}
            </div>
            <div className="disable-task-row__actions">
              {task.status === 'RUNNING' && isDisableTaskStale(task) && (
                <button
                  className="task-retry-btn task-retry-btn--danger"
                  onClick={() => onRestartStale(task.id)}
                  title="Перезапустить воркер отключения и переподключить браузер"
                  disabled={Boolean(restartingDisableTaskId)}
                  type="button"
                >
                  {restartingDisableTaskId === task.id ? 'Рестарт...' : 'Рестарт'}
                </button>
              )}
              {(task.status === 'RETRYING' || task.status === 'FAILED') && (
                <button
                  className="task-retry-btn"
                  onClick={() => onRetry(task.id)}
                  title="Повторить немедленно"
                  type="button"
                >
                  Сейчас
                </button>
              )}
            </div>
          </div>
        ))}
      </div>
    </div>
  );
}

function EnableRecommendationsSection({ recommendations, taskByRecommendationId, taskByAdId, onCreateTask, creatingRecommendationId }) {
  const rows = recommendations || [];
  const counts = rows.reduce(
    (acc, item) => {
      if (item.recommendation_level === 'WARNING') acc.warning += 1;
      else if (item.recommendation_level === 'EARLY_SIGNAL') acc.earlySignal += 1;
      else acc.ok += 1;
      return acc;
    },
    { ok: 0, earlySignal: 0, warning: 0 },
  );
  const queueCount = Object.values(taskByRecommendationId || {}).filter((task) => ENABLE_TASK_ACTIVE_STATUSES.has(task.status)).length;
  const summaryItems = [
    {
      key: 'ok',
      value: formatCount(counts.ok),
      label: 'OK',
      icon: 'OK',
      tone: counts.ok > 0 ? 'signal' : 'default',
      hint: 'Без блокирующих сигналов',
    },
    {
      key: 'early',
      value: formatCount(counts.earlySignal),
      label: 'Ранний сигнал',
      icon: 'EAR',
      tone: counts.earlySignal > 0 ? 'signal' : 'default',
      hint: 'Требует ручной проверки',
    },
    {
      key: 'warning',
      value: formatCount(counts.warning),
      label: 'Проверить',
      icon: 'WRN',
      tone: counts.warning > 0 ? 'warning' : 'default',
      hint: 'Есть риск перед запуском',
    },
    {
      key: 'queue',
      value: formatCount(queueCount),
      label: 'В очереди',
      icon: 'QEN',
      tone: queueCount > 0 ? 'warning' : 'default',
      hint: 'Созданные задачи',
    },
  ];

  return (
    <div className="dashboard-section dashboard-section--dense">
      <SectionHeader
        badge="Сейчас"
        badgeTone="live"
        title="Рекомендации на включение"
        hint="Кандидаты на проверку и возможное восстановление из живого среза."
        actions={<span className="badge badge--warning">{rows.length}</span>}
      />
      <CompactSummaryStrip items={summaryItems} />

      {rows.length === 0 ? (
        <div className="dashboard-chart-empty">Пока нет рекомендаций на включение</div>
      ) : (
        <div className="enable-recommendations-list enable-recommendations-list--compact">
          {rows.map((row) => {
            const meta = getEnableRecommendationMeta(row.recommendation_level);
            const secondaryLabel = getEnableRecommendationSecondaryLabel(row.recommendation_level);
            const stateMeta = getEnableRecommendationStateMeta(row.state);
            const task = taskByRecommendationId?.[row.id] || taskByAdId?.[row.fb_ad_id] || null;
            const taskActive = task && ENABLE_TASK_ACTIVE_STATUSES.has(task.status);
            const isStale = String(row.state || '').toUpperCase() === 'STALE';
            const taskLabel = task ? getEnableTaskStatusLabel(task.status) : 'Нет задачи';
            const metrics = getEnableRecommendationMetrics(row);
            const reasonTitle = getEnableRecommendationReasonTitle(row);
            const reasonText = getEnableRecommendationReasonText(row);
            const createdAt = row.updated_at || row.created_at || row.live_batch_started_at;
            const actionLabel = creatingRecommendationId === row.id
              ? 'Создаём...'
              : isStale
              ? 'Устарело'
              : taskActive
              ? 'В очереди'
              : task
              ? 'Повторить'
              : 'Создать';

            return (
              <div
                key={row.id}
                className={`enable-recommendation-row enable-recommendation-row--compact enable-recommendation-row--${meta.tone}`}
              >
                <div className="enable-recommendation-row__main">
                  <div className="enable-recommendation-row__top">
                    <div className="enable-recommendation-row__title" title={row.ad_name}>
                      {row.ad_name || 'Без названия'}
                    </div>
                    <div className="enable-recommendation-row__meta">
                      {row.fb_ad_id ? `ID: ${row.fb_ad_id}` : 'ID не указан'}
                      {createdAt ? ` · ${formatTime(createdAt)}` : ''}
                    </div>
                  </div>

                  <div className="enable-recommendation-row__reason-inline">
                    <span>Причина:</span>
                    <strong title={reasonTitle}>{reasonTitle}</strong>
                    {reasonText && (
                      <em title={reasonText}>{reasonText}</em>
                    )}
                  </div>

                  {(row.campaign_name || row.adset_name) && (
                    <div className="enable-recommendation-row__metrics">
                      {row.campaign_name && (
                        <span className="rule-tag rule-tag--muted" title={row.campaign_name}>
                          Кампания: {row.campaign_name}
                        </span>
                      )}
                      {row.adset_name && (
                        <span className="rule-tag rule-tag--muted" title={row.adset_name}>
                          Адсет: {row.adset_name}
                        </span>
                      )}
                    </div>
                  )}

                  <div className="enable-recommendation-row__badges">
                    <span className={`rule-tag ${meta.tone === 'warning' ? 'rule-tag--warn' : 'rule-tag--signal'}`}>
                      {meta.icon} {meta.label}
                    </span>
                    {secondaryLabel && (
                      <span className="rule-tag rule-tag--muted">{secondaryLabel}</span>
                    )}
                    {stateMeta && (
                      <span className={`rule-tag ${stateMeta.tone === 'warning' ? 'rule-tag--warn' : 'rule-tag--muted'}`}>
                        {stateMeta.icon} {stateMeta.label}
                      </span>
                    )}
                    {row.delivery_status && (
                      <span className="rule-tag rule-tag--muted">
                        Статус Meta: {row.delivery_status}
                      </span>
                    )}
                    {task && (
                      <span className={`task-status task-status--${task.status === 'SUCCEEDED' ? 'done' : task.status === 'FAILED' ? 'failed' : task.status === 'RETRYING' ? 'retrying' : 'pending'}`}>
                        {ENABLE_TASK_STATUS_ICONS[task.status] || '🧩'} {taskLabel}
                      </span>
                    )}
                  </div>

                  {metrics.length > 0 && (
                    <div className="enable-recommendation-row__metrics">
                      {metrics.map((metric) => (
                        <span key={metric} className="rule-tag rule-tag--muted">
                          {metric}
                        </span>
                      ))}
                    </div>
                  )}
                </div>

                <div className="enable-recommendation-row__actions">
                  <button
                    type="button"
                    className="task-retry-btn"
                    onClick={() => onCreateTask(row.id)}
                    disabled={creatingRecommendationId === row.id || taskActive || isStale}
                    title={
                      isStale
                        ? 'Рекомендация устарела'
                        : taskActive
                        ? 'Задача уже в очереди'
                        : 'Создать задачу на включение'
                    }
                  >
                    {actionLabel}
                  </button>
                </div>
              </div>
            );
          })}
        </div>
      )}
    </div>
  );
}

function EnableTasksSection({ tasks, onRetry, retryingRecommendationId }) {
  const rows = tasks || [];
  const summary = rows.reduce(
    (acc, item) => {
      if (item.status === 'FAILED') acc.failed += 1;
      else if (item.status === 'RETRYING') acc.retrying += 1;
      else if (item.status === 'SUCCEEDED') acc.succeeded += 1;
      else if (ENABLE_TASK_ACTIVE_STATUSES.has(item.status)) acc.active += 1;
      return acc;
    },
    { active: 0, retrying: 0, failed: 0, succeeded: 0 },
  );
  const summaryItems = [
    {
      key: 'active',
      value: formatCount(summary.active),
      label: 'Активные',
      icon: 'RUN',
      tone: summary.active > 0 ? 'warning' : 'default',
      hint: 'Очередь и выполнение',
    },
    {
      key: 'retrying',
      value: formatCount(summary.retrying),
      label: 'Повтор',
      icon: 'RPT',
      tone: summary.retrying > 0 ? 'warning' : 'default',
      hint: 'Требуют нового запуска',
    },
    {
      key: 'failed',
      value: formatCount(summary.failed),
      label: 'Ошибка',
      icon: 'ERR',
      tone: summary.failed > 0 ? 'stop' : 'default',
      hint: 'Нужно вмешательство',
    },
    {
      key: 'done',
      value: formatCount(summary.succeeded),
      label: 'Включено',
      icon: 'OK',
      tone: summary.succeeded > 0 ? 'signal' : 'default',
      hint: 'Задача завершена',
    },
  ];

  return (
    <div className="dashboard-section dashboard-section--dense">
      <SectionHeader
        badge="Сейчас"
        badgeTone="live"
        title="Задачи на включение"
        hint="Очередь ручных включений из рекомендаций."
        actions={<span className="badge badge--warning">{rows.length}</span>}
      />
      <CompactSummaryStrip items={summaryItems} />

      {rows.length === 0 ? (
        <div className="dashboard-chart-empty">Пока нет задач на включение</div>
      ) : (
        <div className="disable-tasks-list enable-tasks-list">
          {rows.map((task) => {
            const normalizedStatus = String(task.status || 'PENDING').toUpperCase();
            const statusClass =
              normalizedStatus === 'SUCCEEDED'
                ? 'done'
                : normalizedStatus === 'FAILED'
                ? 'failed'
                : normalizedStatus === 'RETRYING'
                ? 'retrying'
                : normalizedStatus === 'RUNNING'
                ? 'running'
                : 'pending';
            const requestedBy = getRequestedBy(task);
            const errorPreview =
              normalizedStatus === 'FAILED' || normalizedStatus === 'RETRYING'
                ? getEnableTaskErrorPreview(task.last_error)
                : '';
            const retryLabel =
              task.next_retry_at && normalizedStatus === 'RETRYING'
                ? `Следующая попытка ${formatNextRetry(task.next_retry_at)}`
                : '';
            const attemptLabel =
              task.attempt_count != null && normalizedStatus !== 'SUCCEEDED'
                ? `Попытка ${task.attempt_count}`
                : '';

            return (
              <div
                key={task.id}
                className={`disable-task-row enable-task-row enable-task-row--compact enable-task-row--${normalizedStatus.toLowerCase() || 'pending'}`}
              >
                <div className="enable-task-row__main">
                  <div className="enable-task-row__top">
                    <div className="disable-task-row__name" title={task.ad_name}>
                      {task.ad_name || 'Без названия'}
                    </div>
                    <span className={`task-status task-status--${statusClass}`}>
                      {ENABLE_TASK_STATUS_ICONS[normalizedStatus] || '🧩'} {getEnableTaskStatusLabel(normalizedStatus)}
                    </span>
                  </div>

                  <div className="enable-task-row__meta">
                    <span
                      className="enable-task-row__meta-item enable-task-row__meta-item--mono"
                      title={task.fb_ad_id || ''}
                    >
                      {task.fb_ad_id ? `ID: ${task.fb_ad_id}` : 'ID не указан'}
                    </span>
                    {requestedBy && (
                      <span className="enable-task-row__meta-item" title={`Запросил @${requestedBy}`}>
                        @{requestedBy}
                      </span>
                    )}
                    {attemptLabel && (
                      <span className="enable-task-row__meta-item" title={attemptLabel}>
                        {attemptLabel}
                      </span>
                    )}
                    {retryLabel && (
                      <span className="enable-task-row__meta-item enable-task-row__meta-item--retry" title={retryLabel}>
                        {retryLabel}
                      </span>
                    )}
                  </div>

                  {errorPreview && (
                    <div className="enable-task-row__error" title={task.last_error}>
                      {errorPreview}
                    </div>
                  )}
                </div>
                <div className="disable-task-row__actions">
                  {normalizedStatus === 'FAILED' && task.recommendation_event_id && (
                    <button
                      className="task-retry-btn"
                      onClick={() => onRetry(task.recommendation_event_id)}
                      disabled={retryingRecommendationId === task.recommendation_event_id}
                      title="Вернуть задачу в очередь после повторной проверки рекомендации"
                      type="button"
                    >
                      {retryingRecommendationId === task.recommendation_event_id ? 'Повторяем...' : 'Сейчас'}
                    </button>
                  )}
                </div>
              </div>
            );
          })}
        </div>
      )}
    </div>
  );
}

function ObserverHealthSection({ stats, observerHeartbeatAt }) {
  const lastScanAt = stats?.last_scan_at;
  const lastCycleAdCount = stats?.last_cycle_ad_count;
  const disabledTodayCount = stats?.disabled_today_count;

  return (
    <div className="dashboard-section dashboard-section--compact">
      <SectionHeader
        badge="Здоровье"
        badgeTone="live"
        title="Статус Observer"
        hint="Метрики последнего цикла сканирования."
      />
      <div className="stat-cards-grid stat-cards-grid--compact">
        <StatCard
          value={lastScanAt ? formatTime(lastScanAt) : '—'}
          label="Последний скан"
          icon="🔍"
          hint={lastScanAt ? timeAgo(lastScanAt) : 'Сканирование не запускалось'}
        />
        <StatCard
          value={formatCount(lastCycleAdCount ?? 0)}
          label="Объявлений просканировано"
          icon="📊"
          hint={lastCycleAdCount != null ? `В последнем цикле` : 'Данные недоступны'}
        />
        <StatCard
          value={formatCount(disabledTodayCount ?? 0)}
          label="Отключено сегодня"
          icon="✅"
          hint={disabledTodayCount != null ? `Успешно отключено` : 'Данные недоступны'}
        />
      </div>
    </div>
  );
}

function LatestActiveIncidentCard({ latestIncident, onNavigate }) {
  const latestVariant = getIncidentVariant(latestIncident);
  const rules = latestIncident?.matched_rule_codes || [];
  const metrics = latestIncident?.metrics_json || {};
  const reasonTitle = latestIncident?.reason_title || formatRuleSummary(rules);
  const reasonText = latestIncident?.reason_text || null;
  const activityAt = getIncidentActivityAt(latestIncident);
  const stateLabel = getIncidentStateLabel(latestIncident);
  const adsLink = getIncidentAdsLink(latestIncident);
  const retryCount = latestIncident?.incident_retry_count;

  return (
    <div className="dashboard-section dashboard-section--compact">
      <SectionHeader
        badge="Сейчас"
        badgeTone="live"
        title="Последний активный инцидент"
        hint="Текущий кейс по отключению с причиной и быстрым переходом."
      />
      <div className={`latest-event latest-event--${latestVariant}`}>
        {!latestIncident ? (
          <div className="latest-event__empty">Активных инцидентов пока нет</div>
        ) : (
          <>
            <div className="latest-event__top">
              <div>
                <div className="latest-event__status">
                  {stateLabel} · {timeAgo(activityAt)}
                </div>
                <div className="latest-event__name" title={latestIncident.ad_name}>{latestIncident.ad_name}</div>
              </div>
              <div className="latest-event__time">{formatTime(activityAt)}</div>
            </div>
            <div className="latest-event__meta" title={`ID: ${latestIncident.fb_ad_id}`}>ID: {latestIncident.fb_ad_id}</div>
            <div className="latest-event__meta">
              Активность: {formatTime(activityAt)}
              {retryCount != null && ` · автоповторы ${retryCount}/3`}
            </div>
            {latestIncident.latest_disable_task_status && (
              <div className="latest-event__meta">
                Последняя задача: {latestIncident.latest_disable_task_status}
                {latestIncident.latest_disable_task_attempt != null && ` · попытка ${latestIncident.latest_disable_task_attempt}`}
              </div>
            )}
            {latestIncident.waiting_for_off && (
              <div className="latest-event__meta">Ждём подтверждения OFF после клика.</div>
            )}
            {latestIncident.needs_manual_attention && (
              <div className="latest-event__meta">Нужен ручной разбор после серии автопопыток.</div>
            )}
            <div className="latest-event__reason">
              <span>Причина</span>
              <strong title={reasonTitle}>{reasonTitle}</strong>
            </div>
            {reasonText && <div className="latest-event__reason-text" title={reasonText}>{reasonText}</div>}
            <div className="latest-event__metrics">
              <div className="latest-event__metric">
                <span>Расход</span>
                <strong>{formatMoney(metrics.spend)}</strong>
              </div>
              <div className="latest-event__metric">
                <span>Реги</span>
                <strong>{formatCount(metrics.registrations)}</strong>
              </div>
              <div className="latest-event__metric">
                <span>Депозиты</span>
                <strong>{formatCount(metrics.deposits)}</strong>
              </div>
            </div>
            <div className="latest-event__actions">
              <button
                type="button"
                className="latest-event__action"
                onClick={() => onNavigate(adsLink)}
              >
                В обработку
              </button>
              <button
                type="button"
                className="latest-event__action latest-event__action--ghost"
                onClick={() => onNavigate(adsLink)}
              >
                К объявлениям
              </button>
            </div>
          </>
        )}
      </div>
    </div>
  );
}

function PerformanceSummarySection({ summary, period }) {
  return (
    <div className="dashboard-section">
      <SectionHeader
        badge="Период"
        badgeTone="period"
        title="Сводка эффективности"
        hint={`Период ${performancePeriodLabel(period)}. Только расход, cost-метрики и воронка.`}
      />
      <div className="stat-cards-grid stat-cards-grid--performance">
        <StatCard
          value={formatMoney(summary?.spend)}
          label="Расход"
          icon="SPD"
          hint={`${formatCount(summary?.clicks)} кликов`}
        />
        <StatCard
          value={formatMoney(summary?.cpc, 4)}
          label="CPC"
          icon="CPC"
          hint={summary?.clicks ? `${formatCount(summary.clicks)} кликов` : 'Без кликов'}
        />
        <StatCard
          value={formatMoney(summary?.cpl, 4)}
          label="CPL"
          icon="CPL"
          hint={summary?.leads ? `${formatCount(summary.leads)} лидов` : 'Лидов нет'}
        />
        <StatCard
          value={formatMoney(summary?.cpr, 4)}
          label="CPR"
          icon="CPR"
          hint={summary?.registrations ? `${formatCount(summary.registrations)} регов` : 'Регов нет'}
        />
        <StatCard
          value={formatMoney(summary?.spend_per_dep, 4)}
          label="Расход / деп"
          icon="DEP"
          hint={summary?.deposits ? `${formatCount(summary.deposits)} депозитов` : 'Депозитов нет'}
        />
        <StatCard
          value={formatPercent(summary?.reg_to_dep_rate)}
          label="Reg → Dep"
          icon="R2D"
          hint={summary?.registrations ? `${formatCount(summary.registrations)} регов в базе` : 'Нет базы для расчёта'}
        />
      </div>
    </div>
  );
}

function TotalFunnelSection({ funnel, period }) {
  const steps = funnel || [];
  const maxCount = steps.reduce((max, step) => Math.max(max, step.count || 0), 0) || 1;

  return (
    <div className="dashboard-section">
      <SectionHeader
        badge="Период"
        badgeTone="period"
        title="Общая воронка"
        hint={`Клики, лиды, реги и депозиты за ${performancePeriodLabel(period).toLowerCase()}.`}
      />
      <div className="funnel-overview">
        {steps.length === 0 ? (
          <div className="dashboard-chart-empty">Нет данных для воронки</div>
        ) : (
          steps.map((step, index) => (
            <div key={step.key} className="funnel-stage">
              <div className="funnel-stage__head">
                <span className="funnel-stage__label">{step.label}</span>
                <strong className="funnel-stage__value">{formatCount(step.count)}</strong>
              </div>
              <div className="funnel-stage__bar">
                <span
                  className="funnel-stage__fill"
                  style={{ width: `${Math.max(12, (Number(step.count || 0) / maxCount) * 100)}%` }}
                />
              </div>
              <div className="funnel-stage__foot">
                {index === 0 ? 'Базовый шаг' : `${formatPercent(step.conversion_rate)} от прошлого шага`}
              </div>
            </div>
          ))
        )}
      </div>
    </div>
  );
}

function PerformanceTimelineChart({ data, period }) {
  const hasData = (data || []).some((point) => Number(point.spend) > 0 || point.registrations > 0 || point.deposits > 0);

  function TimelineTooltip({ active, payload, label }) {
    return (
      <ChartTooltip
        active={active}
        payload={payload}
        label={label}
        formatter={(key, value) => {
          if (key === 'spend') return formatMoney(value);
          return formatCount(value);
        }}
      />
    );
  }

  return (
    <div className="dashboard-section">
      <SectionHeader
        badge="Период"
        badgeTone="period"
        title="Динамика расхода и конверсий"
        hint={`Расход и конверсии по времени за ${performancePeriodLabel(period).toLowerCase()}.`}
      />
      <div className="dashboard-chart-card dashboard-chart-card--embedded">
        {!hasData ? (
          <div className="dashboard-chart-empty">Нет данных для таймлайна</div>
        ) : (
          <ResponsiveContainer width="100%" height={260}>
            <ComposedChart data={data} margin={{ top: 8, right: 24, left: -18, bottom: 8 }}>
              <CartesianGrid stroke="rgba(122,130,160,0.14)" vertical={false} />
              <XAxis
                dataKey="label"
                tick={{ fill: '#545c80', fontSize: 11 }}
                tickLine={false}
                axisLine={false}
                minTickGap={16}
              />
              <YAxis
                yAxisId="spend"
                tick={{ fill: '#3a4065', fontSize: 11 }}
                tickLine={false}
                axisLine={false}
                tickFormatter={(value) => `$${value}`}
              />
              <YAxis
                yAxisId="count"
                orientation="right"
                tick={{ fill: '#3a4065', fontSize: 11 }}
                tickLine={false}
                axisLine={false}
                allowDecimals={false}
              />
              <Tooltip content={<TimelineTooltip />} cursor={{ fill: 'rgba(77,136,255,0.06)' }} />
              <Bar
                yAxisId="spend"
                dataKey="spend"
                name="Расход"
                fill="#4d88ff"
                radius={[4, 4, 0, 0]}
                maxBarSize={28}
                fillOpacity={0.7}
              />
              <Line
                yAxisId="count"
                type="monotone"
                dataKey="registrations"
                name="Реги"
                stroke="#ff9a20"
                strokeWidth={2}
                dot={{ fill: '#ff9a20', r: 3, strokeWidth: 0 }}
              />
              <Line
                yAxisId="count"
                type="monotone"
                dataKey="deposits"
                name="Депозиты"
                stroke="#00e896"
                strokeWidth={2}
                dot={{ fill: '#00e896', r: 4, strokeWidth: 0 }}
              />
            </ComposedChart>
          </ResponsiveContainer>
        )}
      </div>
    </div>
  );
}

function CampaignFunnelTable({ rows, sortState, onSort }) {
  const sortMark = (key) => {
    if (sortState.key !== key) return '↕';
    return sortState.direction === 'asc' ? '↑' : '↓';
  };

  const renderHeaderCell = (key, label) => {
    if (!SORTABLE_CAMPAIGN_COLUMNS.has(key)) {
      return <span>{label}</span>;
    }
    return (
      <button type="button" className="campaign-table__sort" onClick={() => onSort(key)}>
        {label} <span>{sortMark(key)}</span>
      </button>
    );
  };

  return (
    <div className="dashboard-section">
      <SectionHeader
        badge="Период"
        badgeTone="period"
        title="Кампании по воронке"
        hint="Расход, cost-метрики и конверсии по кампаниям."
      />
      {!rows?.length ? (
        <div className="dashboard-chart-empty">Кампаний для сравнения пока нет</div>
      ) : (
        <div className="campaign-table-wrap">
          <table className="campaign-table">
            <thead>
              <tr>
                <th>{renderHeaderCell('campaign', 'Кампания')}</th>
                <th>{renderHeaderCell('spend', 'Расход')}</th>
                <th>Клики</th>
                <th>Лиды</th>
                <th>Реги</th>
                <th>{renderHeaderCell('deposits', 'Депы')}</th>
                <th>CPC</th>
                <th>CPL</th>
                <th>CPR</th>
                <th>{renderHeaderCell('spend_per_dep', 'Расход / деп')}</th>
                <th>Клик → лид</th>
                <th>Лид → рег</th>
                <th>{renderHeaderCell('reg_to_dep_rate', 'Рег → деп')}</th>
              </tr>
            </thead>
            <tbody>
              {rows.map((row) => (
                <tr key={row.campaign}>
                  <td className="campaign-table__campaign">{row.campaign}</td>
                  <td>{formatMoney(row.spend)}</td>
                  <td>{formatCount(row.clicks)}</td>
                  <td>{formatCount(row.leads)}</td>
                  <td>{formatCount(row.registrations)}</td>
                  <td>{formatCount(row.deposits)}</td>
                  <td>{formatMoney(row.cpc, 4)}</td>
                  <td>{formatMoney(row.cpl, 4)}</td>
                  <td>{formatMoney(row.cpr, 4)}</td>
                  <td>{formatMoney(row.spend_per_dep, 4)}</td>
                  <td>{formatPercent(row.click_to_lead_rate)}</td>
                  <td>{formatPercent(row.lead_to_reg_rate)}</td>
                  <td>{formatPercent(row.reg_to_dep_rate)}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
    </div>
  );
}

const CHART_COLORS = {
  signal: '#4d88ff',
  warning: '#ff9a20',
  stop: '#ff2b50',
  normal: '#00e896',
  blue: '#4d88ff',
};

function StatusDistributionChart({ data }) {
  const countsByState = new Map((data || []).map((item) => [item.state, Number(item.count || 0)]));
  const normalizedData = STATUS_DISTRIBUTION_META.map((item) => ({
    ...item,
    count: countsByState.get(item.apiLabel) || 0,
  }));
  const hasData = normalizedData.some((item) => item.count > 0);

  function StatusDistributionTooltip({ active, payload }) {
    if (!active || !payload?.length) return null;
    const item = payload[0]?.payload;
    return (
      <div className="chart-tooltip">
        <div className="chart-tooltip__label">{item?.label}</div>
        <div className="chart-tooltip__row" style={{ color: item?.color || '#4d88ff' }}>
          <span>Объявлений</span>
          <strong>{formatCount(item?.count)}</strong>
        </div>
      </div>
    );
  }

  return (
    <div className="dashboard-chart-card dashboard-chart-card--wide">
      <div className="dashboard-chart-card__header">
        <span className="dashboard-chart-card__title">Статусы объявлений сейчас</span>
        <span className="dashboard-chart-card__hint">Живой срез · текущая скан-сессия</span>
      </div>
      {!hasData ? (
        <div className="dashboard-chart-empty">Нет объявлений в текущем срезе</div>
      ) : (
        <ResponsiveContainer width="100%" height={180}>
          <BarChart data={normalizedData} layout="vertical" margin={{ top: 4, right: 16, left: 8, bottom: 0 }}>
            <YAxis
              type="category"
              dataKey="label"
              tick={{ fill: '#545c80', fontSize: 11 }}
              tickLine={false}
              axisLine={false}
              width={104}
            />
            <XAxis
              type="number"
              tick={{ fill: '#3a4065', fontSize: 11 }}
              tickLine={false}
              axisLine={false}
              allowDecimals={false}
            />
            <Tooltip content={<StatusDistributionTooltip />} cursor={{ fill: 'rgba(77,136,255,0.06)' }} />
            <Bar dataKey="count" name="Объявлений" radius={[0, 3, 3, 0]} maxBarSize={16}>
              {normalizedData.map((item) => (
                <Cell key={item.apiLabel} fill={item.color} fillOpacity={0.9} />
              ))}
            </Bar>
          </BarChart>
        </ResponsiveContainer>
      )}
    </div>
  );
}

function ActiveRiskReasonsChart({ data }) {
  const normalizedData = (data || [])
    .map((item) => ({
      ...item,
      count: Number(item?.count || 0),
    }))
    .filter((item) => item.count > 0);
  const hasData = normalizedData.length > 0;
  const maxCount = normalizedData.reduce((max, item) => Math.max(max, item.count), 0) || 1;

  return (
    <div className="dashboard-chart-card dashboard-chart-card--wide">
      <div className="dashboard-chart-card__header">
        <span className="dashboard-chart-card__title">Причины активных рисков сейчас</span>
        <span className="dashboard-chart-card__hint">Живой срез · проблемные объявления</span>
      </div>
      {!hasData ? (
        <div className="dashboard-chart-empty">Активных рисков сейчас нет</div>
      ) : (
        <div className="dashboard-bar-list">
          {normalizedData.map((item) => (
            <div key={`${item.rule}-${item.count}`} className="dashboard-bar-list__row">
              <div className="dashboard-bar-list__meta">
                <span className="dashboard-bar-list__label" title={item.rule}>
                  {item.rule_short || item.rule}
                </span>
                <span className="dashboard-bar-list__value">
                  {formatCount(item.count)}
                </span>
              </div>
              <div className="dashboard-bar-list__track">
                <span
                  className="dashboard-bar-list__fill dashboard-bar-list__fill--warning"
                  style={{ width: `${Math.max(12, (item.count / maxCount) * 100)}%` }}
                />
              </div>
            </div>
          ))}
        </div>
      )}
    </div>
  );
}

function TopCampaignsBySpendChart({ data, period }) {
  const normalizedData = (data || []).map((item) => ({
    ...item,
    spend: Number(item?.spend || 0),
  }));
  const hasData = normalizedData.length > 0;
  const chartHeight = Math.max(180, normalizedData.length * 30);

  function TopCampaignsTooltip({ active, payload }) {
    if (!active || !payload?.length) return null;
    const item = payload[0]?.payload;
    return (
      <div className="chart-tooltip">
        <div className="chart-tooltip__label" style={{ maxWidth: 220, whiteSpace: 'normal', marginBottom: 4 }}>
          {item?.campaign_full}
        </div>
        <div className="chart-tooltip__row" style={{ color: CHART_COLORS.blue }}>
          <span>Расход</span><strong>{formatMoney(item?.spend)}</strong>
        </div>
        <div className="chart-tooltip__row" style={{ color: '#a0a8c8' }}>
          <span>Лиды</span><strong>{formatCount(item?.leads)}</strong>
        </div>
        <div className="chart-tooltip__row" style={{ color: CHART_COLORS.warning }}>
          <span>Реги</span><strong>{formatCount(item?.registrations)}</strong>
        </div>
        <div className="chart-tooltip__row" style={{ color: CHART_COLORS.normal }}>
          <span>Депозиты</span><strong>{formatCount(item?.deposits)}</strong>
        </div>
      </div>
    );
  }

  return (
    <div className="dashboard-chart-card dashboard-chart-card--wide">
      <div className="dashboard-chart-card__header">
        <span className="dashboard-chart-card__title">Топ кампаний по расходу</span>
        <span className="dashboard-chart-card__hint">
          {performancePeriodLabel(period)} · расход и результат
        </span>
      </div>
      {!hasData ? (
        <div className="dashboard-chart-empty">Нет кампаний с расходом за выбранный период</div>
      ) : (
        <ResponsiveContainer width="100%" height={chartHeight}>
          <BarChart data={normalizedData} layout="vertical" margin={{ top: 4, right: 48, left: 8, bottom: 0 }}>
            <XAxis
              type="number"
              tick={{ fill: '#3a4065', fontSize: 11 }}
              tickLine={false}
              axisLine={false}
              tickFormatter={(value) => `$${value}`}
            />
            <YAxis
              type="category"
              dataKey="campaign"
              tick={{ fill: '#545c80', fontSize: 10 }}
              tickLine={false}
              axisLine={false}
              width={140}
              interval={0}
              tickMargin={8}
            />
            <Tooltip content={<TopCampaignsTooltip />} cursor={{ fill: 'rgba(77,136,255,0.06)' }} />
            <Bar
              dataKey="spend"
              name="Расход"
              radius={[0, 3, 3, 0]}
              maxBarSize={16}
              minPointSize={4}
              fill={CHART_COLORS.blue}
              fillOpacity={0.85}
            />
          </BarChart>
        </ResponsiveContainer>
      )}
    </div>
  );
}

function TopAdsBySpendChart({ data }) {
  const normalizedData = (data || []).map((item) => ({
    ...item,
    spend: Number(item?.spend || 0),
    clicks: Number(item?.clicks || 0),
  }));
  const hasData = normalizedData.length > 0;
  const chartHeight = Math.max(180, normalizedData.length * 30);

  function TopAdsTooltip({ active, payload }) {
    if (!active || !payload?.length) return null;
    const item = payload[0]?.payload;
    return (
      <div className="chart-tooltip">
        <div className="chart-tooltip__label" style={{ maxWidth: 220, whiteSpace: 'normal', marginBottom: 4 }}>
          {item?.state_icon} {item?.name_full}
        </div>
        {item?.adset_name ? (
          <div className="chart-tooltip__subtle" style={{ maxWidth: 220, whiteSpace: 'normal', marginBottom: 6 }}>
            Адсет: {item.adset_name}
          </div>
        ) : null}
        <div className="chart-tooltip__row" style={{ color: CHART_COLORS.blue }}>
          <span>Расход</span><strong>{formatMoney(item?.spend)}</strong>
        </div>
        <div className="chart-tooltip__row" style={{ color: CHART_COLORS.warning }}>
          <span>Клик</span><strong>{formatCount(item?.clicks)}</strong>
        </div>
        <div className="chart-tooltip__row" style={{ color: '#a0a8c8' }}>
          <span>Лиды</span><strong>{formatCount(item?.leads)}</strong>
        </div>
        <div className="chart-tooltip__row" style={{ color: CHART_COLORS.normal }}>
          <span>Депозиты</span><strong>{formatCount(item?.deposits)}</strong>
        </div>
      </div>
    );
  }

  return (
    <div className="dashboard-chart-card">
      <div className="dashboard-chart-card__header">
        <span className="dashboard-chart-card__title">Топ объявлений по расходу</span>
        <span className="dashboard-chart-card__hint">Живой срез · с расходом</span>
      </div>
      {!hasData ? (
        <div className="dashboard-chart-empty">Нет объявлений с расходом в текущем срезе</div>
      ) : (
        <ResponsiveContainer width="100%" height={chartHeight}>
          <BarChart data={normalizedData} layout="vertical" margin={{ top: 4, right: 48, left: 8, bottom: 0 }}>
            <XAxis
              type="number"
              tick={{ fill: '#3a4065', fontSize: 11 }}
              tickLine={false}
              axisLine={false}
              tickFormatter={(value) => `$${value}`}
            />
            <YAxis
              type="category"
              dataKey="label"
              tick={{ fill: '#545c80', fontSize: 10 }}
              tickLine={false}
              axisLine={false}
              width={170}
              interval={0}
              tickMargin={8}
            />
            <Tooltip content={<TopAdsTooltip />} cursor={{ fill: 'rgba(77,136,255,0.06)' }} />
            <Bar
              dataKey="spend"
              name="Расход"
              radius={[0, 3, 3, 0]}
              maxBarSize={16}
              minPointSize={4}
              fill={CHART_COLORS.blue}
              fillOpacity={0.85}
            />
          </BarChart>
        </ResponsiveContainer>
      )}
    </div>
  );
}

export default function DashboardPage({ onNavigate }) {
  const navigate = onNavigate || (() => {});
  const [stats, setStats] = useState(null);
  const [incidents, setIncidents] = useState([]);
  const [tasks, setTasks] = useState([]);
  const [enableRecommendations, setEnableRecommendations] = useState([]);
  const [enableTasks, setEnableTasks] = useState([]);
  const [settings, setSettings] = useState(null);
  const [operationalCharts, setOperationalCharts] = useState(null);
  const [performance, setPerformance] = useState(null);
  const [period, setPeriod] = useState('today');
  const [campaignSort, setCampaignSort] = useState({ key: 'spend', direction: 'desc' });
  const [toggling, setToggling] = useState(false);
  const [scanning, setScanning] = useState(false);
  const [creatingRecommendationId, setCreatingRecommendationId] = useState(null);
  const [restartingDisableTaskId, setRestartingDisableTaskId] = useState(null);
  const [error, setError] = useState(null);

  const loadData = useCallback(async () => {
    try {
      const incidentsPromise = getDashboardIncidents({ limit: 20 }).catch(() => []);
      const [
        statsResponse,
        tasksResponse,
        settingsResponse,
        chartResponse,
        performanceResponse,
        incidentsResponse,
      ] = await Promise.all([
        getDashboardStats(),
        getDisableTasks({ limit: 20 }),
        getObserverSettings(),
        getChartData(),
        getDashboardPerformance({ period }),
        incidentsPromise,
      ]);
      setStats(statsResponse);
      setIncidents(normalizeIncidentList(incidentsResponse));
      setTasks(tasksResponse);
      setSettings(settingsResponse);
      setOperationalCharts(chartResponse);
      setPerformance(performanceResponse);
      const [enableRecommendationsResponse, enableTasksResponse] = await Promise.allSettled([
        getEnableRecommendations({ limit: 20 }),
        getEnableTasks({ limit: 20 }),
      ]);
      setEnableRecommendations(
        enableRecommendationsResponse.status === 'fulfilled'
          ? normalizeEnableRecommendations(enableRecommendationsResponse.value)
          : [],
      );
      setEnableTasks(
        enableTasksResponse.status === 'fulfilled'
          ? normalizeEnableTasks(enableTasksResponse.value)
          : [],
      );
      setError(null);
    } catch (e) {
      setError(e.message);
    }
  }, [period]);

  const loadRealtimeStatus = useCallback(async () => {
    try {
      const incidentsPromise = getDashboardIncidents({ limit: 20 }).catch(() => []);
      const [
        tasksResponse,
        statsResponse,
        settingsResponse,
        incidentsResponse,
      ] = await Promise.all([
        getDisableTasks({ limit: 20 }),
        getDashboardStats(),
        getObserverSettings(),
        incidentsPromise,
      ]);
      setTasks(tasksResponse);
      setStats(statsResponse);
      setSettings(settingsResponse);
      setIncidents(normalizeIncidentList(incidentsResponse));
      const [enableRecommendationsResponse, enableTasksResponse] = await Promise.allSettled([
        getEnableRecommendations({ limit: 20 }),
        getEnableTasks({ limit: 20 }),
      ]);
      if (enableRecommendationsResponse.status === 'fulfilled') {
        setEnableRecommendations(normalizeEnableRecommendations(enableRecommendationsResponse.value));
      }
      if (enableTasksResponse.status === 'fulfilled') {
        setEnableTasks(normalizeEnableTasks(enableTasksResponse.value));
      }
    } catch (_) {}
  }, []);

  useEffect(() => {
    void loadData();
  }, [loadData]);

  useAsyncPolling(
    async () => {
      await loadData();
    },
    {
      enabled: true,
      intervalMs: 30000,
    },
  );

  useAsyncPolling(
    async () => {
      await loadRealtimeStatus();
    },
    {
      enabled: true,
      intervalMs: 5000,
    },
  );

  useRefreshOnResume(() => {
    void loadData();
  });

  const handleToggle = async () => {
    if (toggling || !settings) return;
    setToggling(true);
    try {
      await toggleScanning(!settings.is_scanning_enabled);
      setSettings((current) => ({ ...current, is_scanning_enabled: !current.is_scanning_enabled }));
    } finally {
      setToggling(false);
    }
  };

  const handleScanNow = async () => {
    if (scanning) return;
    setScanning(true);
    try {
      await triggerScanNow();
    } finally {
      setTimeout(() => setScanning(false), 3000);
    }
  };

  const handleRetryDisable = async (taskId) => {
    try {
      await retryDisableTask(taskId);
      const tasksResponse = await getDisableTasks({ limit: 20 });
      setTasks(tasksResponse);
    } catch (e) {
      setError(`Не удалось поставить в очередь: ${e.message}`);
    }
  };

  const handleRestart = async () => {
    try {
      await restartObserver();
      setTimeout(loadData, 5000);
    } catch (e) {
      setError(`Не удалось перезапустить воркер: ${e.message}`);
    }
  };

  const handleRestartDisableWorker = async (taskId) => {
    if (restartingDisableTaskId) return;
    setRestartingDisableTaskId(taskId);
    try {
      setError(null);
      await retryDisableTask(taskId);
      await restartDisableWorker();
      setTasks((current) => current.map((task) => (
        task.id === taskId
          ? {
              ...task,
              status: 'PENDING',
              next_retry_at: null,
              last_error: null,
            }
          : task
      )));
      setTimeout(() => {
        void loadRealtimeStatus();
      }, 2000);
      setTimeout(() => {
        void loadData();
      }, 5000);
    } catch (e) {
      setError(`Не удалось перезапустить воркер отключения: ${e.message}`);
    } finally {
      setTimeout(() => {
        setRestartingDisableTaskId(null);
      }, 2500);
    }
  };

  const handleCreateEnableTask = async (recommendationId) => {
    if (creatingRecommendationId) return;
    setCreatingRecommendationId(recommendationId);
    try {
      await createEnableTaskFromRecommendation(recommendationId);
      await loadRealtimeStatus();
    } catch (e) {
      setError(`Не удалось поставить задачу на включение: ${e.message}`);
    } finally {
      setCreatingRecommendationId(null);
    }
  };

  const handleSort = (key) => {
    if (!SORTABLE_CAMPAIGN_COLUMNS.has(key)) return;
    setCampaignSort((current) => (
      current.key === key
        ? { key, direction: current.direction === 'desc' ? 'asc' : 'desc' }
        : { key, direction: 'desc' }
    ));
  };

  const latestIncident = incidents[0] || null;
  const health = getTaskHealth(tasks);
  const campaignRows = sortCampaigns(performance?.campaigns || [], campaignSort);
  const campaignBudgetDeltas = operationalCharts?.campaign_budget_deltas
    || operationalCharts?.campaign_stop_overruns
    || [];
  const sortedEnableRecommendations = useMemo(
    () => sortEnableRecommendations(enableRecommendations),
    [enableRecommendations],
  );
  const sortedEnableTasks = useMemo(
    () => sortEnableTasks(enableTasks),
    [enableTasks],
  );
  const enableRecommendationTaskById = useMemo(() => {
    const map = {};
    for (const task of sortedEnableTasks) {
      if (!task.recommendation_event_id) continue;
      const current = map[task.recommendation_event_id];
      if (!current || new Date(task.updated_at || task.created_at || 0) > new Date(current.updated_at || current.created_at || 0)) {
        map[task.recommendation_event_id] = task;
      }
    }
    return map;
  }, [sortedEnableTasks]);
  const enableRecommendationTaskByAdId = useMemo(() => {
    const map = {};
    for (const task of sortedEnableTasks) {
      if (!task.fb_ad_id) continue;
      const current = map[task.fb_ad_id];
      if (!current || new Date(task.updated_at || task.created_at || 0) > new Date(current.updated_at || current.created_at || 0)) {
        map[task.fb_ad_id] = task;
      }
    }
    return map;
  }, [sortedEnableTasks]);

  return (
    <div className="dashboard-page">
      {error && <div className="error-banner">⚠ {error}</div>}

      <ScanStatusBar
        settings={settings}
        onToggle={handleToggle}
        onScanNow={handleScanNow}
        onRestart={handleRestart}
        scanning={scanning}
        lastScanAt={stats?.last_scan_at}
        observerStatus={stats?.observer_status}
        observerStatusMessage={stats?.observer_status_message}
        observerHeartbeatAt={stats?.observer_heartbeat_at}
        observerLastError={stats?.observer_last_error}
      />

      <div className="dashboard-primary-grid">
        <div className="dashboard-section dashboard-section--dense">
          <SectionHeader
            badge="Сейчас"
            badgeTone="live"
            title="Операционный контроль"
            hint="Сканирование, OFF, очередь и выход за базовые стопы по кампаниям."
          />
          <CompactSummaryStrip
            className="compact-summary-strip--ops"
            items={[
              {
                key: 'early-signal',
                value: formatCount(stats?.ads_in_early_signal ?? 0),
                label: 'Ранние сигналы',
                icon: 'EAR',
                tone: (stats?.ads_in_early_signal ?? 0) > 0 ? 'signal' : 'default',
                hint: 'Сигналы до лидов',
                onClick: () => navigate('/ads?view=all&state=EARLY_SIGNAL_SENT'),
              },
              {
                key: 'off-pending',
                value: formatCount(stats?.ads_claimed ?? 0),
                label: 'OFF не подтверждён',
                icon: 'OFF',
                tone: (stats?.ads_claimed ?? 0) > 0 ? 'stop' : 'default',
                hint: 'Ждём подтверждение OFF',
                onClick: () => navigate('/ads?view=all&state=CLAIMED'),
              },
              {
                key: 'queue',
                value: formatCount(health.activeCount),
                label: 'Активная очередь',
                icon: 'RUN',
                tone: health.activeCount > 0 ? 'warning' : 'default',
                hint: 'Очередь, работа и повторы',
                onClick: () => navigate('/ads?view=all&state=CLAIMED'),
              },
              {
                key: 'stale',
                value: formatCount(health.staleCount),
                label: 'Зависли > 5м',
                icon: 'STL',
                tone: health.staleCount > 0 ? 'stop' : 'default',
                hint: 'Без OFF больше 5 минут',
              },
              {
                key: 'disabled',
                value: formatCount(stats?.ads_disabled_today ?? 0),
                label: 'Отключено ботом',
                icon: 'BOT',
                tone: 'default',
                hint: 'Подтверждённые OFF',
                onClick: () => navigate('/ads?view=all&state=DISABLED'),
              },
            ]}
          />
          <CampaignBudgetDeltaPanel rows={campaignBudgetDeltas} />
        </div>

        <div>
          <ObserverHealthSection stats={stats} observerHeartbeatAt={stats?.observer_heartbeat_at} />
          <LatestActiveIncidentCard latestIncident={latestIncident} onNavigate={navigate} />
        </div>
      </div>

      <DisableTasksSection
        tasks={tasks}
        onRetry={handleRetryDisable}
        onRestartStale={handleRestartDisableWorker}
        restartingDisableTaskId={restartingDisableTaskId}
      />

      <div className="dashboard-enable-grid">
        <EnableRecommendationsSection
          recommendations={sortedEnableRecommendations}
          taskByRecommendationId={enableRecommendationTaskById}
          taskByAdId={enableRecommendationTaskByAdId}
          onCreateTask={handleCreateEnableTask}
          creatingRecommendationId={creatingRecommendationId}
        />

        <EnableTasksSection
          tasks={sortedEnableTasks}
          onRetry={handleCreateEnableTask}
          retryingRecommendationId={creatingRecommendationId}
        />
      </div>

      <div className="dashboard-section">
        <SectionHeader
          badge="Сейчас"
          badgeTone="live"
          title="Что происходит сейчас"
          hint="Живой операционный срез. Этот блок обновляется отдельно и не зависит от периода ниже."
        />
        <div className="dashboard-charts-grid dashboard-charts-grid--live">
          <StatusDistributionChart data={operationalCharts?.state_distribution} />
          <ActiveRiskReasonsChart data={operationalCharts?.rule_violations} />
          <TopAdsBySpendChart data={operationalCharts?.top_ads_by_spend} />
        </div>
      </div>

      <AnalyticsScopeSection period={period} onPeriodChange={setPeriod} />

      <PerformanceSummarySection
        summary={performance?.summary}
        period={period}
      />

      <div className="dashboard-performance-grid">
        <TotalFunnelSection funnel={performance?.funnel} period={period} />
        <PerformanceTimelineChart data={performance?.timeline} period={period} />
      </div>

      <div className="dashboard-section">
        <SectionHeader
          badge="Период"
          badgeTone="period"
          title="Кампании за период"
          hint={`Этот блок зависит от выбранного периода: ${performancePeriodLabel(period).toLowerCase()}.`}
        />
        <div className="dashboard-charts-grid dashboard-charts-grid--period">
          <TopCampaignsBySpendChart data={performance?.campaigns?.slice(0, 8) || []} period={period} />
        </div>
      </div>

      <CampaignFunnelTable rows={campaignRows} sortState={campaignSort} onSort={handleSort} />
    </div>
  );
}
