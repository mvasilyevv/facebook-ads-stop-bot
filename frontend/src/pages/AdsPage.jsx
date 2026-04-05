import { useCallback, useEffect, useMemo, useState } from 'react';
import {
  getAdSnapshots,
  getDashboardStats,
  getDashboardIncidents,
  getDisableTasks,
  getAdTimeline,
  retryDisableTask,
  restartDisableWorker,
  setFakeDeposits,
  deleteFakeDeposits,
} from '../api.js';
import { fmt$ as _sharedFmt$, fmtN as _sharedFmtN } from '../utils/formatters.js';
import { formatTime as _sharedFmtTime } from '../utils/timeUtils.js';
import { useAsyncPolling } from '../hooks/useAsyncPolling.js';
import { useRefreshOnResume } from '../hooks/useRefreshOnResume.js';
import { StateIcon } from '../components/StateIcon.jsx';
import { ALERT_STATE_LABELS } from '../constants/alertStates.js';

// Буфер в минутах: объявления виденные в пределах N минут от последнего скана = "активные".
// Это разделяет текущую сессию от вчерашних кампаний независимо от времени суток.
const ACTIVE_SCAN_BUFFER_MS = 30 * 60 * 1000; // 30 минут
const STALE_DISABLE_TASK_MS = 5 * 60 * 1000;
const ARCHIVE_STATE = 'ARCHIVED';
const TEXT_COLLATOR = new Intl.Collator('ru', { sensitivity: 'base', numeric: true });

const STATE_ORDER = {
  STOP_SENT: 0,
  WARNING_SENT: 1,
  EARLY_SIGNAL_SENT: 2,
  CLAIMED: 3,
  NORMAL: 4,
  DISABLED: 5,
  ARCHIVED: 6,
};

const SORT_OPTIONS = [
  { value: 'state_priority', label: 'Статус', kind: 'state', defaultDirection: 'asc' },
  { value: 'last_observed_at', label: 'Последний скан', kind: 'date', defaultDirection: 'desc' },
  { value: 'ad_name', label: 'Название объявления', kind: 'text', defaultDirection: 'asc' },
  { value: 'adset_name', label: 'Adset', kind: 'text', defaultDirection: 'asc' },
  { value: 'campaign_name', label: 'Campaign', kind: 'text', defaultDirection: 'asc' },
  { value: 'offer_code', label: 'Код оффера', kind: 'text', defaultDirection: 'asc' },
  { value: 'spend', label: 'Расход', kind: 'number', defaultDirection: 'desc' },
  { value: 'cpc', label: 'CPC', kind: 'number', defaultDirection: 'desc' },
  { value: 'cpm', label: 'CPM', kind: 'number', defaultDirection: 'desc' },
  { value: 'frequency', label: 'Частота', kind: 'number', defaultDirection: 'desc' },
  { value: 'clicks', label: 'Клики', kind: 'number', defaultDirection: 'desc' },
  { value: 'leads', label: 'Лиды', kind: 'number', defaultDirection: 'desc' },
  { value: 'registrations', label: 'Реги', kind: 'number', defaultDirection: 'desc' },
  { value: 'deposits', label: 'Депозиты', kind: 'number', defaultDirection: 'desc' },
];

import { RULE_LABELS } from '../constants/ruleLabels.js';

const STATE_PRIORITY = {
  STOP_SENT: 5,
  WARNING_SENT: 4,
  EARLY_SIGNAL_SENT: 3,
  CLAIMED: 2,
  NORMAL: 1,
  DISABLED: 0,
  ARCHIVED: -1,
};

const QUICK_FILTERS = [
  {
    id: 'problems',
    label: 'Проблемные',
    predicate: (ad) => {
      const s = getAdDisplayState(ad);
      return s === 'WARNING_SENT' || s === 'STOP_SENT' || s === 'EARLY_SIGNAL_SENT' || s === 'CLAIMED';
    },
  },
  {
    id: 'no-deposits',
    label: 'Без депозитов',
    predicate: (ad) => (ad.effective_deposits ?? ad.deposits) === 0 && parseFloat(ad.spend || 0) > 0,
  },
  {
    id: 'with-deposits',
    label: 'С депозитами',
    predicate: (ad) => (ad.effective_deposits ?? ad.deposits) > 0,
  },
  {
    id: 'has-fake',
    label: 'Есть фейки',
    predicate: (ad) => (ad.fake_deposits || 0) > 0,
  },
];

function ruleLabel(code) {
  return RULE_LABELS[code] || code;
}

function formatRuleSummary(codes) {
  const labels = (codes || []).map(ruleLabel).filter(Boolean);
  if (labels.length <= 2) return labels.join(', ');
  return `${labels.slice(0, 2).join(', ')} +${labels.length - 2}`;
}

function diagnosticStatusLabel(status) {
  const map = {
    normal: 'Норма',
    elevated: 'Повышено',
    critical: 'Критично',
    insufficient_data: 'Мало данных',
  };
  return map[status] || 'Нет данных';
}

function diagnosticBars(data) {
  if (!data?.diagnostics) return [];
  return [
    {
      key: 'cpm',
      title: 'CPM vs медиана оффера',
      payload: data.diagnostics.cpm,
    },
    {
      key: 'frequency',
      title: 'Частота vs норма оффера',
      payload: data.diagnostics.frequency,
    },
  ];
}

function fmt(val, digits = 2) {
  if (val == null) return '—';
  const num = Number(val);
  if (!Number.isFinite(num)) return '—';
  return `$${num.toFixed(digits)}`;
}

function fmtNum(val) {
  if (val == null) return '—';
  return String(val);
}

const fmt$ = _sharedFmt$;
const fmtN = _sharedFmtN;

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

const fmtTime = _sharedFmtTime;

function formatNextRetry(isoStr) {
  if (!isoStr) return '';
  const diff = Math.ceil((new Date(isoStr).getTime() - Date.now()) / 1000);
  if (diff <= 0) return 'сейчас';
  if (diff < 60) return `через ${diff}с`;
  return `через ${Math.floor(diff / 60)}м`;
}

function timeAgo(isoStr) {
  if (!isoStr) return '—';
  const diff = Math.floor((Date.now() - new Date(isoStr).getTime()) / 1000);
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

function compareNullableNumbers(left, right) {
  const leftNumber = left == null ? null : Number(left);
  const rightNumber = right == null ? null : Number(right);
  const leftMissing = leftNumber == null || !Number.isFinite(leftNumber);
  const rightMissing = rightNumber == null || !Number.isFinite(rightNumber);
  if (leftMissing && rightMissing) return 0;
  if (leftMissing) return 1;
  if (rightMissing) return -1;
  return leftNumber - rightNumber;
}

function compareNullableDates(left, right) {
  const leftDate = left ? Date.parse(left) : NaN;
  const rightDate = right ? Date.parse(right) : NaN;
  const leftMissing = Number.isNaN(leftDate);
  const rightMissing = Number.isNaN(rightDate);
  if (leftMissing && rightMissing) return 0;
  if (leftMissing) return 1;
  if (rightMissing) return -1;
  return leftDate - rightDate;
}

function compareNullableText(left, right) {
  const leftText = String(left || '').trim();
  const rightText = String(right || '').trim();
  const leftMissing = leftText.length === 0;
  const rightMissing = rightText.length === 0;
  if (leftMissing && rightMissing) return 0;
  if (leftMissing) return 1;
  if (rightMissing) return -1;
  return TEXT_COLLATOR.compare(leftText, rightText);
}

function getIncidentActivityAt(incident) {
  return (
    incident?.last_activity_at ||
    incident?.latest_alert_at ||
    incident?.latest_disable_task_updated_at ||
    incident?.started_at ||
    incident?.created_at ||
    new Date(0).toISOString()
  );
}

function getIncidentStateLabel(incident) {
  const state = String(incident?.current_state || '').toUpperCase();
  if (state === 'CLAIMED') return 'Ожидает OFF';
  if (state === 'DISABLED') return 'Отключено';
  if (state === 'STOP_SENT') return 'Стоп-алерт';
  if (state === 'WARNING_SENT') return 'Предупреждение';
  if (state === 'EARLY_SIGNAL_SENT') return 'Ранний сигнал';
  const stage = String(incident?.current_stage || incident?.latest_alert_stage || '').toUpperCase();
  if (stage === 'STOP') return 'Стоп-алерт';
  if (stage === 'WARNING') return 'Предупреждение';
  if (stage === 'EARLY_SIGNAL') return 'Ранний сигнал';
  return 'Активный инцидент';
}

function getIncidentVariant(incident) {
  if (!incident) return 'normal';
  if (incident.needs_manual_attention) return 'stop';
  const state = String(incident.current_state || '').toUpperCase();
  const stage = String(incident.current_stage || incident.latest_alert_stage || '').toUpperCase();
  if (state === 'CLAIMED' || state === 'STOP_SENT' || stage === 'STOP') return 'stop';
  if (state === 'WARNING_SENT' || stage === 'WARNING') return 'warning';
  if (state === 'EARLY_SIGNAL_SENT' || stage === 'EARLY_SIGNAL') return 'signal';
  return 'normal';
}

function getIncidentSummaryText(incident) {
  if (!incident) return '';
  const parts = [];
  if (incident.waiting_for_off) parts.push('Ждём подтверждения OFF');
  if (incident.needs_manual_attention) parts.push('Нужен ручной разбор');
  if (incident.latest_disable_task_status) parts.push(`Последняя задача ${incident.latest_disable_task_status}`);
  if (incident.incident_retry_count != null) parts.push(`Автоповторы ${incident.incident_retry_count}/3`);
  return parts.join(' · ');
}

function getAdDisplayState(ad) {
  if (isArchivedAd(ad)) return ARCHIVE_STATE;
  if (isDeliveryOff(ad)) return 'DISABLED';
  const incidentState = ad?.incident_summary?.current_state || ad?.active_incident?.current_state;
  return String(incidentState || ad?.alert_state || 'NORMAL').toUpperCase();
}

function getAdDisplayStage(ad) {
  const stage = ad?.incident_summary?.current_stage || ad?.active_incident?.current_stage || ad?.current_stage;
  return String(stage || '').toUpperCase() || null;
}

function getDisableTaskActivityAt(task) {
  return task?.updated_at || task?.completed_at || task?.created_at || null;
}

function compareAds(a, b, sortBy, sortDirection) {
  const direction = sortDirection === 'asc' ? 1 : -1;
  let result = 0;

  if (sortBy === 'state_priority') {
    result = (STATE_ORDER[getAdDisplayState(a)] ?? 99) - (STATE_ORDER[getAdDisplayState(b)] ?? 99);
  } else if (sortBy === 'last_observed_at') {
    result = compareNullableDates(a.last_observed_at, b.last_observed_at);
  } else if (
    sortBy === 'spend' ||
    sortBy === 'cpc' ||
    sortBy === 'cpm' ||
    sortBy === 'frequency' ||
    sortBy === 'clicks' ||
    sortBy === 'leads' ||
    sortBy === 'registrations' ||
    sortBy === 'deposits'
  ) {
    const key = sortBy === 'deposits' ? 'effective_deposits' : sortBy;
    result = compareNullableNumbers(a[key] ?? a[sortBy], b[key] ?? b[sortBy]);
  } else {
    result = compareNullableText(a[sortBy], b[sortBy]);
  }

  if (result !== 0) {
    return result * direction;
  }

  if (sortBy !== 'state_priority') {
    const stateDiff = (STATE_ORDER[getAdDisplayState(a)] ?? 99) - (STATE_ORDER[getAdDisplayState(b)] ?? 99);
    if (stateDiff !== 0) return stateDiff;
  }

  if (sortBy !== 'last_observed_at') {
    const observedDiff = compareNullableDates(a.last_observed_at, b.last_observed_at);
    if (observedDiff !== 0) return observedDiff * -1;
  }

  const nameDiff = compareNullableText(a.ad_name, b.ad_name);
  if (nameDiff !== 0) return nameDiff;

  return compareNullableText(a.fb_ad_id, b.fb_ad_id);
}

function isArchivedAd(ad) {
  return ad?.alert_state === ARCHIVE_STATE;
}

function normalizeArchiveAd(ad) {
  return {
    ...ad,
    alert_state: ARCHIVE_STATE,
    incident_summary: null,
    current_stage: null,
    early_signal_rule_codes: [],
    warning_rule_codes: [],
    stop_rule_codes: [],
    cpm_diagnostic_status: null,
    frequency_diagnostic_status: null,
    diagnostic_short_text: null,
  };
}

function isDisableTaskStale(task) {
  const activityAt = getDisableTaskActivityAt(task);
  if (!activityAt || task.status !== 'RUNNING') return false;
  return Date.now() - new Date(activityAt).getTime() >= STALE_DISABLE_TASK_MS;
}

function isDisableTaskRelevant(ad, task) {
  if (!ad || !task || isArchivedAd(ad)) return false;
  const displayState = getAdDisplayState(ad);
  if (task.status === 'PENDING' || task.status === 'RUNNING' || task.status === 'RETRYING' || task.status === 'FAILED') {
    return true;
  }
  if (task.status === 'SUCCEEDED') {
    return displayState === 'CLAIMED' || displayState === 'DISABLED' || displayState === 'STOP_SENT';
  }
  return false;
}

function isDeliveryOff(ad) {
  return ad?.delivery_status === 'OFF' || ad?.delivery_status === 'NOT_DELIVERING';
}

// --- Таймлайн объявления ---

const STAGE_ICONS = { EARLY_SIGNAL: '◎', WARNING: '△', STOP: '×' };
const TASK_STATUS_ICONS = { PENDING: '○', RUNNING: '●', SUCCEEDED: '✓', RETRYING: '↻', FAILED: '×' };
const ENABLE_TASK_STATUS_LABELS = {
  PENDING: 'В очереди',
  RUNNING: 'В работе',
  RETRYING: 'На повторе',
  SUCCEEDED: 'Включено',
  FAILED: 'Ошибка',
};
const ENABLE_RECOMMENDATION_LEVEL_META = {
  OK: {
    label: 'Нет блокирующих сигналов',
    icon: '○',
    tone: 'signal',
  },
  EARLY_SIGNAL: {
    label: 'Ранний сигнал восстановления',
    icon: '◎',
    tone: 'signal',
    secondary: 'Есть ранний сигнал',
  },
  WARNING: {
    label: 'Требует проверки',
    icon: '△',
    tone: 'warning',
    secondary: 'Близко к порогу',
  },
};

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

function getTimelineEventTime(ev) {
  return ev?.time || ev?.created_at || ev?.updated_at || ev?.completed_at || null;
}

function getEnableRecommendationMeta(level) {
  const normalized = String(level || 'OK').toUpperCase();
  return ENABLE_RECOMMENDATION_LEVEL_META[normalized] || ENABLE_RECOMMENDATION_LEVEL_META.OK;
}

function getEnableRecommendationRules(ev) {
  return Array.isArray(ev?.matched_rule_codes)
    ? ev.matched_rule_codes
    : Array.isArray(ev?.matched_rules)
    ? ev.matched_rules
    : [];
}

function getEnableRecommendationMetrics(ev) {
  const metrics = parseJsonObject(ev?.metrics_json);
  const items = [];
  if (metrics.spend != null) items.push(`Расход ${fmt(metrics.spend)}`);
  if (metrics.cpc != null) items.push(`CPC ${fmt(metrics.cpc)}`);
  if (metrics.outbound_ctr != null) items.push(`CTR исх. ${Number(metrics.outbound_ctr).toFixed(2)}%`);
  if (metrics.landing_page_views != null) items.push(`LPV ${fmtNum(metrics.landing_page_views)}`);
  if (metrics.leads != null) items.push(`Лиды ${fmtNum(metrics.leads)}`);
  if (metrics.registrations != null) items.push(`Реги ${fmtNum(metrics.registrations)}`);
  if (metrics.cost_per_registration != null) items.push(`CPR ${fmt(metrics.cost_per_registration, 4)}`);
  if (metrics.deposits != null) items.push(`Депы ${fmtNum(metrics.deposits)}`);
  return items;
}

function getEnableTaskStatusLabel(status) {
  const normalized = String(status || 'PENDING').toUpperCase();
  return ENABLE_TASK_STATUS_LABELS[normalized] || normalized || 'Неизвестно';
}

function getEnableTaskStatus(ev) {
  return String(ev?.status || ev?.task_status || ev?.state || 'PENDING').toUpperCase();
}

function isEnableRecommendationEvent(ev) {
  return ev?.type === 'enable_recommendation' || ev?.type === 'enable_recommendation_event' || ev?.recommendation_level != null;
}

function isEnableTaskEvent(ev) {
  return ev?.type === 'enable_task' || ev?.task_type === 'enable' || ev?.task_kind === 'enable';
}

function getRequestedByName(ev) {
  return ev?.requested_by || ev?.requested_by_username || null;
}

function AdTimeline({ fbAdId, onClose }) {
  const [data, setData] = useState(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState(null);

  const loadTimeline = useCallback(async () => {
    const nextData = await getAdTimeline(fbAdId);
    setData(nextData);
    setError(null);
    setLoading(false);
  }, [fbAdId]);

  useEffect(() => {
    setLoading(true);
    setError(null);
    setData(null);
    void loadTimeline().catch((e) => {
      setError(e.message);
      setLoading(false);
    });
  }, [loadTimeline]);

  useAsyncPolling(async () => {
    try {
      await loadTimeline();
    } catch (e) {
      setError(e.message);
    }
  }, {
    enabled: Boolean(fbAdId),
    intervalMs: 10000,
  });

  return (
    <div className="fixed inset-0 z-50 bg-black/60 animate-fade-in" onClick={(e) => e.target === e.currentTarget && onClose()}>
      <div className="fixed right-0 top-0 z-50 flex h-full w-full max-w-lg flex-col border-l border-border bg-surface animate-slide-in-right overflow-y-auto">
        <div className="flex items-start justify-between border-b border-border px-5 py-4">
          <div>
            <div className="text-sm font-semibold text-primary">
              {data?.ad_name || 'Загрузка...'}
            </div>
            {data?.campaign_name && (
              <div className="text-2xs text-muted">
                {data.campaign_name}
                {data.adset_name && ` › ${data.adset_name}`}
              </div>
            )}
            {data?.current_incident && (
              <div className="text-2xs text-secondary">
                {getIncidentStateLabel(data.current_incident)}
                {data.current_incident.last_activity_at && ` · ${timeAgo(data.current_incident.last_activity_at)}`}
              </div>
            )}
          </div>
          <button className="rounded p-1 text-muted hover:bg-elevated hover:text-primary" onClick={onClose}>✕</button>
        </div>

        {loading && <div className="p-8 text-center text-sm text-muted">Загрузка...</div>}
        {error && <div className="p-4 text-sm text-danger">Ошибка: {error}</div>}

        {data && !loading && (
          <div className="space-y-4 px-5 py-4">
            {/* Текущие метрики */}
            <div className="grid grid-cols-2 sm:grid-cols-3 gap-2">
              {data.current_incident && (
                <div className="flex flex-col rounded bg-elevated px-3 py-2">
                  <span className="text-2xs text-muted">Инцидент</span>
                  <strong className="text-sm text-primary font-mono">{getIncidentStateLabel(data.current_incident)}</strong>
                </div>
              )}
              <div className="flex flex-col rounded bg-elevated px-3 py-2">
                <span className="text-2xs text-muted">Расход</span>
                <strong className="text-sm text-primary font-mono">{fmt(data.current_metrics?.spend)}</strong>
              </div>
              <div className="flex flex-col rounded bg-elevated px-3 py-2">
                <span className="text-2xs text-muted">CPC</span>
                <strong className="text-sm text-primary font-mono">{fmt(data.current_metrics?.cpc)}</strong>
              </div>
              <div className="flex flex-col rounded bg-elevated px-3 py-2">
                <span className="text-2xs text-muted">Статус Meta</span>
                <strong className="text-sm text-primary">{data.current_metrics?.delivery_status || data.delivery_status || '—'}</strong>
              </div>
              <div className="flex flex-col rounded bg-elevated px-3 py-2">
                <span className="text-2xs text-muted">Исх. CTR</span>
                <strong className="text-sm text-primary font-mono">{data.current_metrics?.outbound_ctr ? `${Number(data.current_metrics.outbound_ctr).toFixed(2)}%` : '—'}</strong>
              </div>
              <div className="flex flex-col rounded bg-elevated px-3 py-2">
                <span className="text-2xs text-muted">LPV</span>
                <strong className="text-sm text-primary font-mono">{fmtNum(data.current_metrics?.landing_page_views)}</strong>
              </div>
              <div className="flex flex-col rounded bg-elevated px-3 py-2">
                <span className="text-2xs text-muted">Лиды</span>
                <strong className="text-sm text-primary font-mono">{fmtNum(data.current_metrics?.leads)}</strong>
              </div>
              <div className="flex flex-col rounded bg-elevated px-3 py-2">
                <span className="text-2xs text-muted">Реги</span>
                <strong className="text-sm text-primary font-mono">{fmtNum(data.current_metrics?.registrations)}</strong>
              </div>
              <div className="flex flex-col rounded bg-elevated px-3 py-2">
                <span className="text-2xs text-muted">CPR</span>
                <strong className="text-sm text-primary font-mono">{fmt(data.current_metrics?.cost_per_registration, 4)}</strong>
              </div>
              <div className="flex flex-col rounded bg-elevated px-3 py-2">
                <span className="text-2xs text-muted">Депы</span>
                <strong className="text-sm text-primary font-mono">{fmtNum(data.current_metrics?.deposits)}</strong>
              </div>
              <div className="flex flex-col rounded bg-elevated px-3 py-2">
                <span className="text-2xs text-muted">Последний скан</span>
                <strong className="text-sm text-primary font-mono">{fmtTime(data.last_observed_at)}</strong>
              </div>
            </div>

            {data.current_incident && (
              <div className="rounded-md border border-border p-4 space-y-3">
                <div className="space-y-1">
                  <div className="text-sm font-semibold text-primary">Текущий инцидент</div>
                  <div className="text-2xs text-secondary">
                    {getIncidentSummaryText(data.current_incident) || 'Инцидент активен и обновляется.'}
                  </div>
                </div>
                <div className="grid grid-cols-1 sm:grid-cols-2 gap-3">
                  <div className="rounded-md border border-border bg-elevated/50 p-3">
                    <div className="flex items-center justify-between text-sm">
                      <span>Статус</span>
                      <strong>{getIncidentStateLabel(data.current_incident)}</strong>
                    </div>
                    <div className="mt-1 text-2xs text-muted">
                      {data.current_incident.last_activity_at
                        ? `Последняя активность: ${fmtTime(data.current_incident.last_activity_at)}`
                        : 'Последняя активность пока не определена.'}
                    </div>
                  </div>
                  <div className="rounded-md border border-border bg-elevated/50 p-3">
                    <div className="flex items-center justify-between text-sm">
                      <span>Автоповторы</span>
                      <strong>
                        {data.current_incident.incident_retry_count != null
                          ? `${data.current_incident.incident_retry_count}/3`
                          : '—'}
                      </strong>
                    </div>
                    <div className="mt-1 text-2xs text-muted">
                      {data.current_incident.needs_manual_attention
                        ? 'Нужен ручной разбор после серии автопопыток.'
                        : 'Инцидент ещё может повторно ставить задачу на отключение.'}
                    </div>
                  </div>
                </div>
              </div>
            )}

            {data.diagnostics && (
              <div className="rounded-md border border-border p-4 space-y-3">
                <div className="space-y-1">
                  <div className="text-sm font-semibold text-primary">Диагностика трафика</div>
                  <div className="text-2xs text-secondary">{data.diagnostics.summary_text}</div>
                </div>
                <div className="grid grid-cols-1 sm:grid-cols-2 gap-3">
                  {diagnosticBars(data).map((item) => (
                    <div key={item.key} className="rounded-md border border-border bg-elevated/50 p-3">
                      <div className="flex items-center justify-between text-sm">
                        <span>{item.title}</span>
                        <strong>{diagnosticStatusLabel(item.payload?.status)}</strong>
                      </div>
                      <div className="mt-1.5 h-1.5 overflow-hidden rounded-full bg-elevated">
                        <span
                          className={`h-full rounded-full transition-all ${
                            item.payload?.status === 'critical' ? 'bg-danger' :
                            item.payload?.status === 'elevated' ? 'bg-warning' :
                            item.payload?.status === 'normal' ? 'bg-success' : 'bg-muted'
                          }`}
                          style={{
                            width: `${Number(item.payload?.bar_percent || 0) > 0 ? Math.max(6, Number(item.payload?.bar_percent || 0)) : 0}%`,
                          }}
                        />
                      </div>
                      <div className="mt-1 text-2xs text-muted">{item.payload?.text || 'Диагностика пока недоступна.'}</div>
                    </div>
                  ))}
                </div>
              </div>
            )}

            {/* Таймлайн событий */}
            <div className="space-y-2">
              {data.timeline.length === 0 && (
                <div className="py-6 text-center text-sm text-muted">Событий пока нет</div>
              )}
              {[...(data.timeline || [])].sort((a, b) => {
                const left = getTimelineEventTime(a);
                const right = getTimelineEventTime(b);
                return new Date(right || 0) - new Date(left || 0);
              }).map((ev, i) => (
                <div
                  key={i}
                  className={`rounded-md border p-3 ${
                    isEnableRecommendationEvent(ev)
                      ? ev.recommendation_level === 'WARNING'
                        ? 'border-warning/30 bg-warning-muted'
                        : 'border-early/30 bg-early-muted'
                      : isEnableTaskEvent(ev)
                      ? 'border-accent/30 bg-accent-muted'
                      : ev.type === 'alert'
                      ? ev.stage === 'STOP'
                        ? 'border-danger/30 bg-danger-muted'
                        : ev.stage === 'EARLY_SIGNAL'
                        ? 'border-early/30 bg-early-muted'
                        : 'border-warning/30 bg-warning-muted'
                      : 'border-border bg-elevated/50'
                  }`}
                >
                  <div className="mb-0.5 font-mono text-2xs text-muted">{fmtTime(getTimelineEventTime(ev))}</div>
                  {isEnableRecommendationEvent(ev) && (
                    <div className="flex items-start gap-2">
                      <span className="mt-0.5 flex-shrink-0 text-sm text-secondary">{getEnableRecommendationMeta(ev.recommendation_level).icon}</span>
                      <div className="min-w-0 flex-1">
                        <div className="text-sm font-medium text-primary">
                          {getEnableRecommendationMeta(ev.recommendation_level).label}
                          {ev.recommendation_level === 'EARLY_SIGNAL' && (
                            <span className="text-2xs text-muted"> · есть ранний сигнал</span>
                          )}
                          {ev.recommendation_level === 'WARNING' && (
                            <span className="text-2xs text-muted"> · близко к порогу</span>
                          )}
                          {getEnableRecommendationRules(ev).length > 0 && (
                            <span className="text-2xs text-secondary" title={getEnableRecommendationRules(ev).map(ruleLabel).join(', ')}>
                              {' — '}
                              {formatRuleSummary(getEnableRecommendationRules(ev))}
                            </span>
                          )}
                        </div>
                        {ev.delivery_status && (
                          <div className="text-2xs text-muted">
                            Статус Meta: <strong>{ev.delivery_status}</strong>
                          </div>
                        )}
                        {ev.reason_text && (
                          <div className="text-2xs text-muted">{ev.reason_text}</div>
                        )}
                        {getEnableRecommendationMetrics(ev).length > 0 && (
                          <div className="mt-1 flex flex-wrap gap-2 font-mono text-2xs text-secondary">
                            {getEnableRecommendationMetrics(ev).map((item) => (
                              <span key={item}>{item}</span>
                            ))}
                          </div>
                        )}
                      </div>
                    </div>
                  )}
                  {ev.type === 'alert' && (
                    <div className="flex items-start gap-2">
                      <span className="mt-0.5 flex-shrink-0 text-sm text-secondary">{STAGE_ICONS[ev.stage] || '○'}</span>
                      <div className="min-w-0 flex-1">
                        <div className="text-sm font-medium text-primary">
                          {ev.reason_title || (ev.stage === 'STOP' ? 'Стоп-алерт' : ev.stage === 'EARLY_SIGNAL' ? 'Ранний сигнал' : 'Предупреждение')}
                          {ev.matched_rules?.length > 0 && (
                            <span
                              className="text-2xs text-secondary"
                              title={ev.matched_rules.map(ruleLabel).join(', ')}
                            >
                              {' — '}
                              {formatRuleSummary(ev.matched_rules)}
                            </span>
                          )}
                        </div>
                        {ev.reason_text && (
                          <div className="text-2xs text-muted">{ev.reason_text}</div>
                        )}
                        <div className="mt-1 flex flex-wrap gap-2 font-mono text-2xs text-secondary">
                          {ev.spend != null && <span>Расход: {fmt(ev.spend)}</span>}
                          {ev.cpc != null && <span>CPC: {fmt(ev.cpc)}</span>}
                          {ev.outbound_ctr != null && <span>CTR исх.: {Number(ev.outbound_ctr).toFixed(2)}%</span>}
                          {ev.landing_page_views != null && <span>LPV: {ev.landing_page_views}</span>}
                          {ev.cpm != null && <span>CPM: {fmt(ev.cpm)}</span>}
                          {ev.frequency != null && <span>Частота: {Number(ev.frequency).toFixed(2)}</span>}
                          {ev.clicks != null && <span>Кликов: {ev.clicks}</span>}
                          {ev.leads != null && <span>Лидов: {ev.leads}</span>}
                          {ev.registrations != null && <span>Реги: {ev.registrations}</span>}
                          {ev.cost_per_registration != null && <span>CPR: {fmt(ev.cost_per_registration, 4)}</span>}
                          {ev.deposits != null && <span>Депы: {ev.deposits}</span>}
                        </div>
                      </div>
                    </div>
                  )}
                  {isEnableTaskEvent(ev) && (() => {
                    const taskStatus = getEnableTaskStatus(ev);
                    return (
                      <div className="flex items-start gap-2">
                        <span className="mt-0.5 flex-shrink-0 text-sm text-secondary">{TASK_STATUS_ICONS[taskStatus] || '○'}</span>
                        <div className="min-w-0 flex-1">
                          <div className="text-sm font-medium text-primary">
                            Задача на включение — {getEnableTaskStatusLabel(taskStatus)}
                            {getRequestedByName(ev) && <span className="text-2xs text-muted"> (@{getRequestedByName(ev)})</span>}
                          </div>
                          {ev.completed_at && (
                            <div className="text-2xs text-muted">Выполнено: {fmtTime(ev.completed_at)}</div>
                          )}
                          {ev.last_error && (
                            <div className="mt-1 text-2xs text-danger">{ev.last_error}</div>
                          )}
                        </div>
                      </div>
                    );
                  })()}
                  {ev.type === 'disable_task' && (
                    <div className="flex items-start gap-2">
                      <span className="mt-0.5 flex-shrink-0 text-sm text-secondary">{TASK_STATUS_ICONS[ev.status] || '○'}</span>
                      <div className="min-w-0 flex-1">
                        <div className="text-sm font-medium text-primary">
                          Задача на отключение — {ev.status}
                          {getRequestedByName(ev) && <span className="text-2xs text-muted"> (@{getRequestedByName(ev)})</span>}
                        </div>
                        {ev.updated_at && (
                          <div className="text-2xs text-muted">Обновлено: {fmtTime(ev.updated_at)}</div>
                        )}
                        {ev.completed_at && (
                          <div className="text-2xs text-muted">Выполнено: {fmtTime(ev.completed_at)}</div>
                        )}
                        {ev.last_error && (
                          <div className="mt-1 text-2xs text-danger">{ev.last_error}</div>
                        )}
                      </div>
                    </div>
                  )}
                </div>
              ))}
            </div>
          </div>
        )}
      </div>
    </div>
  );
}

// Вспомогательная функция: состояние строки таблицы
function getTableRowState(ad) {
  const displayState = getAdDisplayState(ad);
  if (displayState === 'STOP_SENT') return 'stop';
  if (displayState === 'WARNING_SENT') return 'warning';
  if (displayState === 'EARLY_SIGNAL_SENT') return 'signal';
  if (displayState === 'CLAIMED') return 'claimed';
  if (displayState === 'DISABLED') return 'disabled';
  return 'normal';
}

// Вспомогательная функция: значок действия в конце строки
function getTableRowActionIcon(ad) {
  const displayState = getAdDisplayState(ad);
  if (displayState === 'STOP_SENT') return '×';
  if (displayState === 'WARNING_SENT') return '△';
  if (displayState === 'EARLY_SIGNAL_SENT') return '◎';
  return null;
}

// --- Главный компонент ---

export default function AdsPage({ initialView = 'active', initialState = '' }) {
  const [view, setView] = useState(initialView); // 'active' | 'archive' | 'all'
  const [offerFilter, setOfferFilter] = useState('');
  const [stateFilter, setStateFilter] = useState(initialState);
  const [sortBy, setSortBy] = useState('state_priority');
  const [sortDirection, setSortDirection] = useState('asc');
  const [allAds, setAllAds] = useState([]);
  const [incidents, setIncidents] = useState([]);
  const [lastScanAt, setLastScanAt] = useState(null);
  const [tasks, setTasks] = useState([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState(null);
  const [timelineAdId, setTimelineAdId] = useState(null);
  const [restartingDisableWorker, setRestartingDisableWorker] = useState(false);
  const [activeQuickFilter, setActiveQuickFilter] = useState(null);
  const [fakeDepModal, setFakeDepModal] = useState(null); // { fb_ad_id, ad_name, deposits, fake_deposits }

  useEffect(() => {
    setView(initialView);
    setStateFilter(initialState);
    setOfferFilter('');
    setTimelineAdId(null);
  }, [initialView, initialState]);

  // Загружаем все данные одним запросом
  const loadAds = useCallback(async () => {
    try {
      setLoading(true);
      /* Баг 8: все промисы обёрнуты в catch — один упавший запрос не крашит страницу */
      const [allData, statsData, taskData, incidentsData] = await Promise.all([
        getAdSnapshots({ limit: 200 }).catch(() => []),
        getDashboardStats().catch(() => null),
        getDisableTasks({ limit: 50 }).catch(() => []),
        getDashboardIncidents({ limit: 200 }).catch(() => []),
      ]);
      setAllAds(Array.isArray(allData) ? allData : []);
      setLastScanAt(statsData?.last_scan_at || null);
      setTasks(Array.isArray(taskData) ? taskData : []);
      setIncidents(normalizeIncidentList(incidentsData));
      setError(null);
    } catch (e) {
      /* Баг 6: обновляем error state при ошибках polling */
      setError(e.message);
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    void loadAds();
  }, [loadAds]);

  useAsyncPolling(
    async () => {
      /* Баг 6: перехватываем ошибки polling и обновляем error state */
      try {
        await loadAds();
      } catch (e) {
        setError(e.message);
      }
    },
    {
      enabled: true,
      intervalMs: 10000,
    },
  );

  useRefreshOnResume(() => {
    void loadAds();
  });

  const handleSaveFakeDeps = useCallback(async (fbAdId, fakeCount, note) => {
    try {
      if (fakeCount <= 0) {
        await deleteFakeDeposits(fbAdId);
      } else {
        await setFakeDeposits(fbAdId, fakeCount, note);
      }
      setFakeDepModal(null);
      await loadAds();
    } catch (e) {
      console.error('Ошибка сохранения ложных депозитов:', e);
    }
  }, [loadAds]);

  const handleSort = useCallback((field) => {
    if (sortBy === field) {
      // Повторный клик — переключить направление
      setSortDirection((d) => (d === 'asc' ? 'desc' : 'asc'));
    } else {
      setSortBy(field);
      const opt = SORT_OPTIONS.find((o) => o.value === field);
      if (opt) setSortDirection(opt.defaultDirection);
    }
  }, [sortBy]);

  const handleRestartDisableWorker = useCallback(async (taskId) => {
    if (restartingDisableWorker) {
      return;
    }

    try {
      setRestartingDisableWorker(true);
      if (taskId) {
        await retryDisableTask(taskId);
      }
      await restartDisableWorker();
      setError(null);
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
      await loadAds();
      window.setTimeout(() => {
        void loadAds();
      }, 3000);
    } catch (e) {
      setError(`Не удалось перезапустить воркер отключения: ${e.message}`);
    } finally {
      setRestartingDisableWorker(false);
    }
  }, [loadAds, restartingDisableWorker]);

  // Словарь задач по fb_ad_id (последняя задача)
  const tasksByAdId = useMemo(() => {
    const map = {};
    for (const t of tasks) {
      if (
        !map[t.fb_ad_id] ||
        new Date(getDisableTaskActivityAt(t) || 0) > new Date(getDisableTaskActivityAt(map[t.fb_ad_id]) || 0)
      ) {
        map[t.fb_ad_id] = t;
      }
    }
    return map;
  }, [tasks]);

  const incidentsByAdId = useMemo(() => {
    const map = {};
    for (const incident of incidents) {
      if (!incident?.fb_ad_id) continue;
      if (
        !map[incident.fb_ad_id] ||
        new Date(getIncidentActivityAt(incident) || 0) > new Date(getIncidentActivityAt(map[incident.fb_ad_id]) || 0)
      ) {
        map[incident.fb_ad_id] = incident;
      }
    }
    return map;
  }, [incidents]);

  const adsWithIncidents = useMemo(() => (
    allAds.map((ad) => ({
      ...ad,
      incident_summary: incidentsByAdId[ad.fb_ad_id] || null,
    }))
  ), [allAds, incidentsByAdId]);

  // Активные = объявления из последнего скана (в пределах ACTIVE_SCAN_BUFFER_MS от last_scan_at).
  // Это разделяет вчерашние кампании от сегодняшних независимо от времени суток.
  const { activeAds, archiveAds } = useMemo(() => {
    if (!lastScanAt) {
      // Если нет данных о последнем скане — всё "активное"
      return { activeAds: adsWithIncidents, archiveAds: [] };
    }
    const cutoff = new Date(lastScanAt).getTime() - ACTIVE_SCAN_BUFFER_MS;
    const active = [];
    const archive = [];
    for (const ad of adsWithIncidents) {
      if (ad.last_observed_at && new Date(ad.last_observed_at).getTime() >= cutoff) {
        active.push(ad);
      } else {
        archive.push(normalizeArchiveAd(ad));
      }
    }
    return { activeAds: active, archiveAds: archive };
  }, [adsWithIncidents, lastScanAt]);

  const allDisplayAds = useMemo(() => [...activeAds, ...archiveAds], [activeAds, archiveAds]);

  // Выбираем нужный набор по view
  const sourceAds = useMemo(() => {
    if (view === 'active') return activeAds;
    if (view === 'archive') return archiveAds;
    return allDisplayAds;
  }, [view, activeAds, archiveAds, allDisplayAds]);

  // Уникальные офферы для фильтра
  const offerCodes = useMemo(() => {
    const codes = [...new Set(sourceAds.map((a) => a.offer_code).filter(Boolean))];
    return codes.sort((left, right) => TEXT_COLLATOR.compare(left, right));
  }, [sourceAds]);

  // Фильтрация + сортировка
  const filtered = useMemo(() => {
    let result = sourceAds;
    if (offerFilter) result = result.filter((a) => a.offer_code === offerFilter);
    if (stateFilter) {
      result = result.filter((a) => (
        stateFilter === ARCHIVE_STATE
          ? isArchivedAd(a)
          : getAdDisplayState(a) === stateFilter
      ));
    }
    // Применяем quick filter (предикат), если выбран
    if (activeQuickFilter) {
      const filter = QUICK_FILTERS.find((f) => f.id === activeQuickFilter);
      if (filter) {
        result = result.filter(filter.predicate);
      }
    }
    // Сортировка всегда работает
    result = [...result].sort((a, b) => compareAds(a, b, sortBy, sortDirection));
    return result;
  }, [sourceAds, offerFilter, stateFilter, sortBy, sortDirection, activeQuickFilter]);

  const selectCls = 'rounded bg-elevated border border-border px-3 py-1.5 text-sm text-secondary focus:border-accent focus:outline-none';

  const ROW_BORDER = {
    stop: 'border-l-2 border-l-danger',
    warning: 'border-l-2 border-l-warning',
    signal: 'border-l-2 border-l-early',
    claimed: 'border-l-2 border-l-muted',
    disabled: 'opacity-60',
    normal: '',
  };

  const SEV_BADGE = {
    stop: 'bg-danger-muted text-danger',
    warn: 'bg-warning-muted text-warning',
    signal: 'bg-early-muted text-early',
    muted: 'bg-elevated text-muted',
    offer: 'bg-accent-muted text-accent',
  };

  return (
    <div className="space-y-md">
      {error && (
        <div className="rounded-md bg-danger-muted border border-danger/30 px-4 py-3 text-sm text-danger">{error}</div>
      )}

      {/* Тулбар */}
      <div className="flex flex-wrap items-center gap-3">
        {/* Табы */}
        <div className="flex gap-1 rounded-md bg-elevated p-1">
          {[
            { id: 'active', label: 'Активные', count: activeAds.length },
            { id: 'archive', label: 'Архив', count: archiveAds.length },
            { id: 'all', label: 'Все', count: allAds.length },
          ].map((tab) => (
            <button
              key={tab.id}
              className={`rounded px-3 py-1.5 text-sm transition-colors ${view === tab.id ? 'bg-surface font-medium text-primary' : 'text-secondary hover:text-primary'}`}
              onClick={() => { setView(tab.id); setStateFilter(''); }}
            >
              {tab.label}
              <span className={`ml-1.5 rounded-full px-1.5 text-2xs ${view === tab.id ? 'bg-accent-muted text-accent' : 'bg-elevated text-muted'}`}>
                {tab.count}
              </span>
            </button>
          ))}
        </div>

        {/* Фильтры */}
        <div className="flex flex-wrap gap-2">
          {offerCodes.length > 0 && (
            <select className={selectCls} value={offerFilter} onChange={(e) => setOfferFilter(e.target.value)}>
              <option value="">Все офферы</option>
              {offerCodes.map((c) => <option key={c} value={c}>{c}</option>)}
            </select>
          )}
          <select className={selectCls} value={stateFilter} onChange={(e) => setStateFilter(e.target.value)}>
            <option value="">Все статусы</option>
            <option value="NORMAL">{ALERT_STATE_LABELS.NORMAL}</option>
            <option value="EARLY_SIGNAL_SENT">{ALERT_STATE_LABELS.EARLY_SIGNAL_SENT}</option>
            <option value="WARNING_SENT">{ALERT_STATE_LABELS.WARNING_SENT}</option>
            <option value="STOP_SENT">{ALERT_STATE_LABELS.STOP_SENT}</option>
            <option value="CLAIMED">{ALERT_STATE_LABELS.CLAIMED}</option>
            <option value="DISABLED">{ALERT_STATE_LABELS.DISABLED}</option>
            {archiveAds.length > 0 && <option value="ARCHIVED">{ALERT_STATE_LABELS.ARCHIVED}</option>}
          </select>
        </div>

        {/* Quick-фильтры */}
        <div className="ml-auto flex gap-1.5">
          {QUICK_FILTERS.map((f) => (
            <button
              key={f.id}
              className={`rounded px-2.5 py-1.5 text-2xs font-medium transition-colors ${activeQuickFilter === f.id ? 'bg-accent-muted text-accent' : 'bg-elevated text-secondary hover:text-primary'}`}
              onClick={() => setActiveQuickFilter(activeQuickFilter === f.id ? null : f.id)}
            >
              {f.label}
            </button>
          ))}
        </div>
      </div>

      {/* Счётчик */}
      <div className="text-2xs text-muted">
        Показано: {filtered.length}
        {view === 'active' && archiveAds.length > 0 && <span> · В архиве: {archiveAds.length}</span>}
        {lastScanAt && <span title={`Последний скан: ${fmtTime(lastScanAt)}`}> · Скан {timeAgo(lastScanAt)}</span>}
      </div>

      {/* Таблица */}
      {loading && allAds.length === 0 ? (
        <div className="flex items-center gap-3 py-12 text-sm text-muted">
          <div className="h-5 w-5 animate-spin rounded-full border-2 border-accent border-t-transparent" />
          Загрузка объявлений...
        </div>
      ) : filtered.length === 0 ? (
        <div className="py-12 text-center" role="status">
          <div className="text-2xl text-muted">{view === 'active' ? '✓' : view === 'archive' ? '○' : '—'}</div>
          <div className="mt-2 text-sm font-medium text-primary">
            {view === 'active' ? 'Нет активных объявлений' : view === 'archive' ? 'Архив пуст' : 'Нет объявлений'}
          </div>
          {stateFilter && <div className="mt-1 text-2xs text-muted">Попробуйте сбросить фильтр</div>}
        </div>
      ) : (
        <div className="panel overflow-hidden">
          <div className="overflow-x-auto">
            <table className="w-full text-sm">
              <thead>
                <tr className="border-b border-border bg-elevated/50">
                  <th className="w-9 px-2 py-2" />
                  <th className="th-sortable px-3 py-2 text-left" onClick={() => handleSort('ad_name')}>
                    Название{sortBy === 'ad_name' && <span className="ml-1">{sortDirection === 'asc' ? '↑' : '↓'}</span>}
                  </th>
                  <th className={`th-sortable px-3 py-2 text-right w-20 ${sortBy === 'spend' ? 'text-accent' : ''}`} onClick={() => handleSort('spend')}>
                    Расход{sortBy === 'spend' && <span className="ml-1">{sortDirection === 'asc' ? '↑' : '↓'}</span>}
                  </th>
                  <th className={`th-sortable px-3 py-2 text-right w-[70px] ${sortBy === 'cpc' ? 'text-accent' : ''}`} onClick={() => handleSort('cpc')}>
                    CPC{sortBy === 'cpc' && <span className="ml-1">{sortDirection === 'asc' ? '↑' : '↓'}</span>}
                  </th>
                  <th className={`th-sortable px-3 py-2 text-right w-[60px] ${sortBy === 'leads' ? 'text-accent' : ''}`} onClick={() => handleSort('leads')}>
                    Лиды{sortBy === 'leads' && <span className="ml-1">{sortDirection === 'asc' ? '↑' : '↓'}</span>}
                  </th>
                  <th className={`th-sortable px-3 py-2 text-right w-[70px] ${sortBy === 'deposits' ? 'text-accent' : ''}`} onClick={() => handleSort('deposits')}>
                    Депозит{sortBy === 'deposits' && <span className="ml-1">{sortDirection === 'asc' ? '↑' : '↓'}</span>}
                  </th>
                  <th className="px-3 py-2 text-left w-[120px] text-2xs uppercase tracking-wider text-muted">Правила</th>
                  <th className="w-10 px-2 py-2" />
                </tr>
              </thead>
              <tbody>
                {filtered.map((ad) => {
                  const displayState = getAdDisplayState(ad);
                  const rowState = getTableRowState(ad);
                  const actionIcon = getTableRowActionIcon(ad);
                  const allRules = [
                    ...ad.stop_rule_codes.map((c) => ({ code: c, sev: 'stop' })),
                    ...ad.warning_rule_codes.map((c) => ({ code: c, sev: 'warn' })),
                    ...ad.early_signal_rule_codes.map((c) => ({ code: c, sev: 'signal' })),
                  ];
                  const shown = allRules.slice(0, 2);
                  const extra = allRules.length - shown.length;

                  return (
                    <tr
                      key={ad.fb_ad_id}
                      className={`tr-hover cursor-pointer border-b border-border ${ROW_BORDER[rowState] || ''}`}
                      onClick={() => setTimelineAdId(ad.fb_ad_id)}
                    >
                      <td className="w-9 px-2 py-2.5 text-center">
                        <StateIcon state={displayState} size="sm" />
                      </td>
                      <td className="px-3 py-2.5">
                        <div className="truncate text-primary" title={ad.ad_name}>{ad.ad_name}</div>
                        {ad.offer_code && (
                          <span className={`mt-0.5 inline-block rounded-sm px-1.5 py-0.5 text-[10px] font-medium ${SEV_BADGE.offer}`}>
                            {ad.offer_code}
                          </span>
                        )}
                      </td>
                      <td className="px-3 py-2.5 text-right font-mono text-primary">{fmt$(ad.spend)}</td>
                      <td className="px-3 py-2.5 text-right font-mono text-primary">{fmt$(ad.cpc)}</td>
                      <td className="px-3 py-2.5 text-right font-mono text-primary">{fmtN(ad.leads)}</td>
                      <td
                        className={`px-3 py-2.5 text-right font-mono cursor-pointer hover:bg-elevated/50 ${ad.effective_deposits === 0 && Number(ad.spend) > 0 ? 'text-danger font-semibold' : 'text-primary'}`}
                        onClick={(e) => { e.stopPropagation(); setFakeDepModal({ fb_ad_id: ad.fb_ad_id, ad_name: ad.ad_name, deposits: ad.deposits, fake_deposits: ad.fake_deposits || 0 }); }}
                        title="Нажмите для настройки ложных депозитов"
                      >
                        {fmtN(ad.effective_deposits)}
                        {ad.fake_deposits > 0 && (
                          <span className="ml-1 text-[10px] text-warning">({ad.fake_deposits} фейк)</span>
                        )}
                      </td>
                      <td className="px-3 py-2.5">
                        <div className="flex flex-wrap gap-1">
                          {shown.map((r) => (
                            <span key={r.code} className={`rounded-sm px-1.5 py-0.5 text-[10px] font-medium ${SEV_BADGE[r.sev]}`}>
                              {r.code}
                            </span>
                          ))}
                          {extra > 0 && (
                            <span className={`rounded-sm px-1.5 py-0.5 text-[10px] font-medium ${SEV_BADGE.muted}`}>+{extra}</span>
                          )}
                        </div>
                      </td>
                      <td className="w-10 px-2 py-2.5 text-center text-base">
                        {actionIcon}
                      </td>
                    </tr>
                  );
                })}
              </tbody>
            </table>
          </div>
        </div>
      )}

      {/* Баг 7: key={timelineAdId} гарантирует remount при смене объявления — polling перезапускается без stale closure */}
      {timelineAdId && (
        <AdTimeline key={timelineAdId} fbAdId={timelineAdId} onClose={() => setTimelineAdId(null)} />
      )}

      {/* Модалка ложных депозитов */}
      {fakeDepModal && (
        <FakeDepositModal
          data={fakeDepModal}
          onSave={handleSaveFakeDeps}
          onClose={() => setFakeDepModal(null)}
        />
      )}
    </div>
  );
}


function FakeDepositModal({ data, onSave, onClose }) {
  const [fakeCount, setFakeCount] = useState(data.fake_deposits || 0);
  const [note, setNote] = useState('');
  const [saving, setSaving] = useState(false);

  const handleSave = async () => {
    setSaving(true);
    await onSave(data.fb_ad_id, fakeCount, note);
    setSaving(false);
  };

  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/50" onClick={onClose}>
      <div className="bg-surface border border-border rounded-lg p-5 w-80 shadow-xl" onClick={(e) => e.stopPropagation()}>
        <h3 className="text-sm font-semibold text-primary mb-3">Ложные депозиты</h3>
        <p className="text-xs text-secondary mb-3 truncate" title={data.ad_name}>{data.ad_name}</p>
        <div className="flex items-center gap-3 mb-3">
          <span className="text-xs text-secondary">Всего в FB:</span>
          <span className="font-mono text-sm text-primary">{data.deposits}</span>
        </div>
        <div className="mb-3">
          <label className="text-xs text-secondary block mb-1">Ложных:</label>
          <div className="flex items-center gap-2">
            <button
              type="button"
              className="w-7 h-7 rounded bg-elevated border border-border text-primary text-sm font-bold hover:bg-border"
              onClick={() => setFakeCount(Math.max(0, fakeCount - 1))}
            >−</button>
            <input
              type="number"
              min={0}
              max={data.deposits}
              value={fakeCount}
              onChange={(e) => setFakeCount(Math.max(0, Math.min(data.deposits, Number(e.target.value) || 0)))}
              className="w-14 text-center font-mono text-sm bg-elevated border border-border rounded px-2 py-1 text-primary"
            />
            <button
              type="button"
              className="w-7 h-7 rounded bg-elevated border border-border text-primary text-sm font-bold hover:bg-border"
              onClick={() => setFakeCount(Math.min(data.deposits, fakeCount + 1))}
            >+</button>
          </div>
        </div>
        <div className="mb-4">
          <label className="text-xs text-secondary block mb-1">Причина:</label>
          <input
            type="text"
            value={note}
            onChange={(e) => setNote(e.target.value)}
            placeholder="Нет в Keitaro"
            className="w-full text-sm bg-elevated border border-border rounded px-2 py-1 text-primary placeholder:text-secondary/50"
          />
        </div>
        <div className="flex gap-2 justify-end">
          <button
            type="button"
            onClick={onClose}
            className="px-3 py-1.5 text-xs rounded bg-elevated border border-border text-secondary hover:text-primary"
          >Отмена</button>
          <button
            type="button"
            onClick={handleSave}
            disabled={saving}
            className="px-3 py-1.5 text-xs rounded bg-accent text-white hover:bg-accent/80 disabled:opacity-50"
          >{saving ? 'Сохраняю...' : 'Сохранить'}</button>
        </div>
      </div>
    </div>
  );
}
