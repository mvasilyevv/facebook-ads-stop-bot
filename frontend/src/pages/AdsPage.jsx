import { useCallback, useEffect, useMemo, useState } from 'react';
import {
  getAdSnapshots,
  getDashboardStats,
  getDashboardIncidents,
  getDisableTasks,
  getAdTimeline,
  retryDisableTask,
  restartDisableWorker,
} from '../api.js';
import { useAsyncPolling } from '../hooks/useAsyncPolling.js';
import { useRefreshOnResume } from '../hooks/useRefreshOnResume.js';

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

const STATE_LABELS = {
  NORMAL: 'Норма',
  EARLY_SIGNAL_SENT: 'Ранний сигнал',
  WARNING_SENT: 'Предупреждение',
  STOP_SENT: 'Стоп',
  CLAIMED: 'Ожидает OFF',
  DISABLED: 'Отключено',
  ARCHIVED: 'Архив',
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

const RULE_LABELS = {
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

function fmtTime(isoStr) {
  if (!isoStr) return '—';
  const d = new Date(isoStr);
  return d.toLocaleString('ru-RU', { day: '2-digit', month: '2-digit', hour: '2-digit', minute: '2-digit' });
}

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
    null
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
    result = compareNullableNumbers(a[sortBy], b[sortBy]);
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

const STAGE_ICONS = { EARLY_SIGNAL: '🔎', WARNING: '⚠️', STOP: '🛑' };
const TASK_STATUS_ICONS = { PENDING: '⏳', RUNNING: '🔄', SUCCEEDED: '✅', RETRYING: '🔁', FAILED: '❌' };
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
    icon: 'ℹ️',
    tone: 'signal',
  },
  EARLY_SIGNAL: {
    label: 'Ранний сигнал восстановления',
    icon: '🔎',
    tone: 'signal',
    secondary: 'Есть ранний сигнал',
  },
  WARNING: {
    label: 'Требует проверки',
    icon: '⚠️',
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
    <div className="timeline-overlay" onClick={(e) => e.target === e.currentTarget && onClose()}>
      <div className="timeline-drawer">
        <div className="timeline-drawer__header">
          <div className="timeline-drawer__title">
            {data?.ad_name || 'Загрузка...'}
          </div>
          {data?.campaign_name && (
            <div className="timeline-drawer__subtitle">
              📁 {data.campaign_name}
              {data.adset_name && ` › ${data.adset_name}`}
            </div>
          )}
          {data?.current_incident && (
            <div className="timeline-drawer__subtitle">
              🧭 {getIncidentStateLabel(data.current_incident)}
              {data.current_incident.last_activity_at && ` · ${timeAgo(data.current_incident.last_activity_at)}`}
            </div>
          )}
          <button className="timeline-drawer__close" onClick={onClose}>✕</button>
        </div>

        {loading && <div className="timeline-loading">Загрузка...</div>}
        {error && <div className="timeline-error">Ошибка: {error}</div>}

        {data && !loading && (
          <>
            {/* Текущие метрики */}
            <div className="timeline-current-metrics">
              {data.current_incident && (
                <div className="timeline-metric">
                  <span>Инцидент</span>
                  <strong>{getIncidentStateLabel(data.current_incident)}</strong>
                </div>
              )}
              <div className="timeline-metric">
                <span>Расход</span>
                <strong>{fmt(data.current_metrics?.spend)}</strong>
              </div>
              <div className="timeline-metric">
                <span>CPC</span>
                <strong>{fmt(data.current_metrics?.cpc)}</strong>
              </div>
              <div className="timeline-metric">
                <span>Статус Meta</span>
                <strong>{data.current_metrics?.delivery_status || data.delivery_status || '—'}</strong>
              </div>
              <div className="timeline-metric">
                <span>Исх. CTR</span>
                <strong>{data.current_metrics?.outbound_ctr ? `${Number(data.current_metrics.outbound_ctr).toFixed(2)}%` : '—'}</strong>
              </div>
              <div className="timeline-metric">
                <span>LPV</span>
                <strong>{fmtNum(data.current_metrics?.landing_page_views)}</strong>
              </div>
              <div className="timeline-metric">
                <span>Лиды</span>
                <strong>{fmtNum(data.current_metrics?.leads)}</strong>
              </div>
              <div className="timeline-metric">
                <span>Реги</span>
                <strong>{fmtNum(data.current_metrics?.registrations)}</strong>
              </div>
              <div className="timeline-metric">
                <span>CPR</span>
                <strong>{fmt(data.current_metrics?.cost_per_registration, 4)}</strong>
              </div>
              <div className="timeline-metric">
                <span>Депы</span>
                <strong>{fmtNum(data.current_metrics?.deposits)}</strong>
              </div>
              <div className="timeline-metric">
                <span>Последний скан</span>
                <strong>{fmtTime(data.last_observed_at)}</strong>
              </div>
            </div>

            {data.current_incident && (
              <div className="timeline-diagnostics">
                <div className="timeline-diagnostics__header">
                  <div className="timeline-diagnostics__title">Текущий инцидент</div>
                  <div className="timeline-diagnostics__summary">
                    {getIncidentSummaryText(data.current_incident) || 'Инцидент активен и обновляется.'}
                  </div>
                </div>
                <div className="timeline-diagnostics__grid">
                  <div className="timeline-diagnostic timeline-diagnostic--normal">
                    <div className="timeline-diagnostic__top">
                      <span>Статус</span>
                      <strong>{getIncidentStateLabel(data.current_incident)}</strong>
                    </div>
                    <div className="timeline-diagnostic__text">
                      {data.current_incident.last_activity_at
                        ? `Последняя активность: ${fmtTime(data.current_incident.last_activity_at)}`
                        : 'Последняя активность пока не определена.'}
                    </div>
                  </div>
                  <div className="timeline-diagnostic timeline-diagnostic--normal">
                    <div className="timeline-diagnostic__top">
                      <span>Автоповторы</span>
                      <strong>
                        {data.current_incident.incident_retry_count != null
                          ? `${data.current_incident.incident_retry_count}/3`
                          : '—'}
                      </strong>
                    </div>
                    <div className="timeline-diagnostic__text">
                      {data.current_incident.needs_manual_attention
                        ? 'Нужен ручной разбор после серии автопопыток.'
                        : 'Инцидент ещё может повторно ставить задачу на отключение.'}
                    </div>
                  </div>
                </div>
              </div>
            )}

            {data.diagnostics && (
              <div className="timeline-diagnostics">
                <div className="timeline-diagnostics__header">
                  <div className="timeline-diagnostics__title">Диагностика трафика</div>
                  <div className="timeline-diagnostics__summary">{data.diagnostics.summary_text}</div>
                </div>
                <div className="timeline-diagnostics__grid">
                  {diagnosticBars(data).map((item) => (
                    <div key={item.key} className={`timeline-diagnostic timeline-diagnostic--${item.payload?.status || 'insufficient_data'}`}>
                      <div className="timeline-diagnostic__top">
                        <span>{item.title}</span>
                        <strong>{diagnosticStatusLabel(item.payload?.status)}</strong>
                      </div>
                      <div className="timeline-diagnostic__bar">
                        <span
                          className={`timeline-diagnostic__fill timeline-diagnostic__fill--${item.payload?.status || 'insufficient_data'}`}
                          style={{
                            width: `${Number(item.payload?.bar_percent || 0) > 0 ? Math.max(6, Number(item.payload?.bar_percent || 0)) : 0}%`,
                          }}
                        />
                      </div>
                      <div className="timeline-diagnostic__text">{item.payload?.text || 'Диагностика пока недоступна.'}</div>
                    </div>
                  ))}
                </div>
              </div>
            )}

            {/* Таймлайн событий */}
            <div className="timeline-events">
              {data.timeline.length === 0 && (
                <div className="timeline-empty">Событий пока нет</div>
              )}
              {[...(data.timeline || [])].sort((a, b) => {
                const left = getTimelineEventTime(a);
                const right = getTimelineEventTime(b);
                return new Date(right || 0) - new Date(left || 0);
              }).map((ev, i) => (
                <div
                  key={i}
                  className={`timeline-event timeline-event--${
                    isEnableRecommendationEvent(ev)
                      ? ev.recommendation_level === 'WARNING'
                        ? 'warning'
                        : 'signal'
                      : isEnableTaskEvent(ev)
                      ? 'task'
                      : ev.type === 'alert'
                      ? ev.stage === 'STOP'
                        ? 'stop'
                        : ev.stage === 'EARLY_SIGNAL'
                        ? 'signal'
                        : 'warning'
                      : 'task'
                  }`}
                >
                  <div className="timeline-event__time">{fmtTime(getTimelineEventTime(ev))}</div>
                  {isEnableRecommendationEvent(ev) && (
                    <div className="timeline-event__body">
                      <span className="timeline-event__icon">{getEnableRecommendationMeta(ev.recommendation_level).icon}</span>
                      <div className="timeline-event__content">
                        <div className="timeline-event__title">
                          {getEnableRecommendationMeta(ev.recommendation_level).label}
                          {ev.recommendation_level === 'EARLY_SIGNAL' && (
                            <span className="timeline-event__who"> · есть ранний сигнал</span>
                          )}
                          {ev.recommendation_level === 'WARNING' && (
                            <span className="timeline-event__who"> · близко к порогу</span>
                          )}
                          {getEnableRecommendationRules(ev).length > 0 && (
                            <span className="timeline-event__rules" title={getEnableRecommendationRules(ev).map(ruleLabel).join(', ')}>
                              {' — '}
                              {formatRuleSummary(getEnableRecommendationRules(ev))}
                            </span>
                          )}
                        </div>
                        {ev.delivery_status && (
                          <div className="timeline-event__sub">
                            Статус Meta: <strong>{ev.delivery_status}</strong>
                          </div>
                        )}
                        {ev.reason_text && (
                          <div className="timeline-event__sub">{ev.reason_text}</div>
                        )}
                        {getEnableRecommendationMetrics(ev).length > 0 && (
                          <div className="timeline-event__metrics">
                            {getEnableRecommendationMetrics(ev).map((item) => (
                              <span key={item}>{item}</span>
                            ))}
                          </div>
                        )}
                      </div>
                    </div>
                  )}
                  {ev.type === 'alert' && (
                    <div className="timeline-event__body">
                      <span className="timeline-event__icon">{STAGE_ICONS[ev.stage] || '📌'}</span>
                      <div className="timeline-event__content">
                        <div className="timeline-event__title">
                          {ev.reason_title || (ev.stage === 'STOP' ? 'Стоп-алерт' : ev.stage === 'EARLY_SIGNAL' ? 'Ранний сигнал' : 'Предупреждение')}
                          {ev.matched_rules?.length > 0 && (
                            <span
                              className="timeline-event__rules"
                              title={ev.matched_rules.map(ruleLabel).join(', ')}
                            >
                              {' — '}
                              {formatRuleSummary(ev.matched_rules)}
                            </span>
                          )}
                        </div>
                        {ev.reason_text && (
                          <div className="timeline-event__sub">{ev.reason_text}</div>
                        )}
                        <div className="timeline-event__metrics">
                          {ev.spend != null && <span>💰 {fmt(ev.spend)}</span>}
                          {ev.cpc != null && <span>🖱 {fmt(ev.cpc)}</span>}
                          {ev.outbound_ctr != null && <span>🌐 CTR исх.: {Number(ev.outbound_ctr).toFixed(2)}%</span>}
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
                      <div className="timeline-event__body">
                        <span className="timeline-event__icon">{TASK_STATUS_ICONS[taskStatus] || '🔧'}</span>
                        <div className="timeline-event__content">
                          <div className="timeline-event__title">
                            Задача на включение — {getEnableTaskStatusLabel(taskStatus)}
                            {getRequestedByName(ev) && <span className="timeline-event__who"> (@{getRequestedByName(ev)})</span>}
                          </div>
                          {ev.completed_at && (
                            <div className="timeline-event__sub">Выполнено: {fmtTime(ev.completed_at)}</div>
                          )}
                          {ev.last_error && (
                            <div className="timeline-event__error">{ev.last_error}</div>
                          )}
                        </div>
                      </div>
                    );
                  })()}
                  {ev.type === 'disable_task' && (
                    <div className="timeline-event__body">
                      <span className="timeline-event__icon">{TASK_STATUS_ICONS[ev.status] || '🔧'}</span>
                      <div className="timeline-event__content">
                        <div className="timeline-event__title">
                          Задача на отключение — {ev.status}
                          {getRequestedByName(ev) && <span className="timeline-event__who"> (@{getRequestedByName(ev)})</span>}
                        </div>
                        {ev.updated_at && (
                          <div className="timeline-event__sub">Обновлено: {fmtTime(ev.updated_at)}</div>
                        )}
                        {ev.completed_at && (
                          <div className="timeline-event__sub">Выполнено: {fmtTime(ev.completed_at)}</div>
                        )}
                        {ev.last_error && (
                          <div className="timeline-event__error">{ev.last_error}</div>
                        )}
                      </div>
                    </div>
                  )}
                </div>
              ))}
            </div>
          </>
        )}
      </div>
    </div>
  );
}

// --- Карточка объявления ---

function AdCard({ ad, incident, disableTask, onClick, onRestartDisableWorker, restartingDisableWorker }) {
  const isArchived = isArchivedAd(ad);
  const activeIncident = incident || ad.incident_summary || null;
  const displayState = getAdDisplayState(ad);
  const displayStage = getAdDisplayStage(ad);
  const isDisabled = displayState === 'DISABLED';
  const isStop = !isDisabled && (displayState === 'STOP_SENT' || displayStage === 'STOP');
  const isWarning = !isDisabled && (displayState === 'WARNING_SENT' || displayStage === 'WARNING');
  const isEarlySignal = !isDisabled && (displayState === 'EARLY_SIGNAL_SENT' || displayStage === 'EARLY_SIGNAL');
  const isClaimed = displayState === 'CLAIMED';
  const isStaleDisableTask = disableTask?.status === 'RUNNING' && isDisableTaskStale(disableTask);
  const incidentVariant = getIncidentVariant(activeIncident);
  const incidentText = getIncidentSummaryText(activeIncident);
  const incidentTime = getIncidentActivityAt(activeIncident);

  let cardVariant = 'normal';
  if (isArchived) cardVariant = 'archived';
  else if (isDisabled || isClaimed || incidentVariant === 'stop') cardVariant = 'disabled';
  else if (isStop) cardVariant = 'stop';
  else if (isWarning) cardVariant = 'warning';
  else if (isEarlySignal) cardVariant = 'signal';

  const allRules = [
    ...(ad.stop_rule_codes || []).map((r) => ({ code: r, type: 'stop' })),
    ...(ad.warning_rule_codes || []).map((r) => ({ code: r, type: 'warn' })),
    ...(ad.early_signal_rule_codes || []).map((r) => ({ code: r, type: 'signal' })),
  ];

  // Дедупликация: stop приоритет
  const seenCodes = new Set();
  const rules = allRules.filter(({ code }) => {
    if (seenCodes.has(code)) return false;
    seenCodes.add(code);
    return true;
  });
  const visibleRules = rules.slice(0, 3);
  const rulesTitle = rules.map(({ code }) => ruleLabel(code)).join(', ');

  const incidentStateLabel = getIncidentStateLabel(activeIncident);
  const stateLabel = isDisabled
    ? STATE_LABELS.DISABLED
    : activeIncident
      ? incidentStateLabel
      : (STATE_LABELS[displayState] || displayState);
  const isDisableConfirmed = activeIncident?.current_state === 'DISABLED' || displayState === 'DISABLED' || isDeliveryOff(ad);
  const deliveryStatus = ad.delivery_status === 'OFF' || ad.delivery_status === 'NOT_DELIVERING' ? ad.delivery_status : null;
  const manualAttention = Boolean(activeIncident?.needs_manual_attention);
  const waitingForOff = Boolean(activeIncident?.waiting_for_off || isClaimed || (disableTask?.status === 'SUCCEEDED' && !isDisableConfirmed));

  return (
    <div className={`ad-card ad-card--${cardVariant}`} onClick={onClick}>
      <div className="ad-card__header">
        <span className={`ad-card__state-badge ad-card__state-badge--${cardVariant}`}>
          {isArchived && '🗂 '}
          {isStop && '⛔ '}
          {isWarning && '⚠️ '}
          {isEarlySignal && '🔎 '}
          {isClaimed && '🔄 '}
          {isDisabled && '🔕 '}
          {!isArchived && !isStop && !isWarning && !isEarlySignal && !isDisabled && !isClaimed && '✅ '}
          {stateLabel}
        </span>
        {activeIncident && (
          <span className="ad-card__offer-code" title={incidentStateLabel}>
            {incidentStateLabel}
          </span>
        )}
        {ad.offer_code && (
          <span className="ad-card__offer-code" title={ad.offer_code}>{ad.offer_code}</span>
        )}
        {deliveryStatus && (
          <span
            className={`ad-card__delivery-status ad-card__delivery-status--${deliveryStatus === 'OFF' ? 'off' : 'not-delivering'}`}
            title={`Статус Meta: ${deliveryStatus}`}
          >
            Статус Meta: {deliveryStatus}
          </span>
        )}
      </div>

      <div className="ad-card__name" title={ad.ad_name}>{ad.ad_name}</div>
      {(ad.campaign_name || ad.adset_name) && (
        <div className="ad-card__meta">
          {ad.campaign_name && (
            <div className="ad-card__campaign" title={ad.campaign_name}>
              <span className="ad-card__meta-label">Campaign</span>
              <span className="ad-card__meta-value">{ad.campaign_name}</span>
            </div>
          )}
          {ad.adset_name && (
            <div className="ad-card__adset" title={ad.adset_name}>
              <span className="ad-card__meta-label">Adset</span>
              <span className="ad-card__meta-value">{ad.adset_name}</span>
            </div>
          )}
        </div>
      )}

      <div className="ad-card__metrics">
        <div className="ad-card__metric">
          <span className="ad-card__metric-label">Расход</span>
          <span className="ad-card__metric-value">{fmt(ad.spend)}</span>
        </div>
        <div className="ad-card__metric">
          <span className="ad-card__metric-label">CPC</span>
          <span className="ad-card__metric-value">{fmt(ad.cpc)}</span>
        </div>
        <div className="ad-card__metric">
          <span className="ad-card__metric-label">Лиды</span>
          <span className="ad-card__metric-value">{fmtNum(ad.leads)}</span>
        </div>
        <div className="ad-card__metric">
          <span className="ad-card__metric-label">Реги</span>
          <span className="ad-card__metric-value">{fmtNum(ad.registrations)}</span>
        </div>
        <div className="ad-card__metric">
          <span className="ad-card__metric-label">CPR</span>
          <span className="ad-card__metric-value">{fmt(ad.cost_per_registration, 4)}</span>
        </div>
        <div className="ad-card__metric">
          <span className="ad-card__metric-label">Депозиты</span>
          <span className={`ad-card__metric-value ${ad.deposits === 0 && Number(ad.spend) > 0 ? 'ad-card__metric-value--zero' : ''}`}>
            {fmtNum(ad.deposits)}
          </span>
        </div>
      </div>

      {rules.length > 0 && (
        <div className="ad-card__rules" title={rulesTitle}>
          {visibleRules.map(({ code, type }) => (
            <span key={code} className={`rule-tag rule-tag--${type}`}>
              {ruleLabel(code)}
            </span>
          ))}
          {rules.length > visibleRules.length && (
            <span className="rule-tag rule-tag--muted">+{rules.length - visibleRules.length}</span>
          )}
        </div>
      )}

      {(ad.cpm_diagnostic_status || ad.frequency_diagnostic_status || ad.diagnostic_short_text) && (
        <div className="ad-card__diagnostics">
          <div className="ad-card__diagnostic-badges">
            {ad.cpm_diagnostic_status && (
              <span className={`diagnostic-badge diagnostic-badge--${ad.cpm_diagnostic_status}`}>
                CPM: {diagnosticStatusLabel(ad.cpm_diagnostic_status)}
              </span>
            )}
            {ad.frequency_diagnostic_status && (
              <span className={`diagnostic-badge diagnostic-badge--${ad.frequency_diagnostic_status}`}>
                Частота: {diagnosticStatusLabel(ad.frequency_diagnostic_status)}
              </span>
            )}
          </div>
          {ad.diagnostic_short_text && (
            <div className="ad-card__diagnostic-text" title={ad.diagnostic_short_text}>{ad.diagnostic_short_text}</div>
          )}
        </div>
      )}

      {activeIncident && (
        <div className="ad-card__incident">
          <div className="ad-card__incident-line">
            <span className={`task-status task-status--${manualAttention ? 'failed' : waitingForOff ? 'pending' : 'running'}`}>
              {manualAttention
                ? '⛔ Нужен ручной разбор'
                : waitingForOff
                ? '⏳ Ждём подтверждения OFF'
                : `🧭 ${incidentStateLabel}`}
            </span>
            {activeIncident.incident_retry_count != null && (
              <span className="task-status task-status--pending">
                Автоповторы {activeIncident.incident_retry_count}/3
              </span>
            )}
            {incidentTime && (
              <span className="task-status task-status--pending" title={fmtTime(incidentTime)}>
                Активность {timeAgo(incidentTime)}
              </span>
            )}
          </div>
          {incidentText && (
            <div className="ad-card__diagnostic-text" title={incidentText}>
              {incidentText}
            </div>
          )}
          {activeIncident.latest_disable_task_status && (
            <div className="ad-card__diagnostic-text">
              Последняя задача: {activeIncident.latest_disable_task_status}
              {activeIncident.latest_disable_task_attempt != null && ` · попытка ${activeIncident.latest_disable_task_attempt}`}
            </div>
          )}
          {activeIncident.incident_key && (
            <div className="ad-card__diagnostic-text" title={activeIncident.incident_key}>
              Инцидент: {activeIncident.incident_key}
            </div>
          )}
        </div>
      )}

      {disableTask && isDisableTaskRelevant(ad, disableTask) && (
        <div className="ad-card__disable-status">
          {disableTask.status === 'RUNNING' && (
            <div className="ad-card__disable-status-row">
              <span className={`task-status ${isStaleDisableTask ? 'task-status--stale' : 'task-status--running'}`}>
                {isStaleDisableTask ? '⚠️ Зависло в браузере' : '🔄 Выключаем в браузере'}
              </span>
              {isStaleDisableTask && onRestartDisableWorker && (
                <button
                  className="restart-btn restart-btn--inline"
                  onClick={(event) => {
                    event.stopPropagation();
                    void onRestartDisableWorker(disableTask.id);
                  }}
                  disabled={restartingDisableWorker}
                  title="Перезапустить воркер отключения"
                  type="button"
                >
                  {restartingDisableWorker ? 'Перезапуск...' : 'Рестарт'}
                </button>
              )}
            </div>
          )}
          {disableTask.status === 'PENDING' && (
            <span className="task-status task-status--pending">⏳ В очереди на выключение</span>
          )}
          {disableTask.status === 'RETRYING' && (
            <span className="task-status task-status--retrying">
              🔁 Повтор ({disableTask.attempt_count}/10)
              {disableTask.next_retry_at && ` · ${formatNextRetry(disableTask.next_retry_at)}`}
            </span>
          )}
          {disableTask.status === 'FAILED' && (
            <span className="task-status task-status--failed">❌ Ошибка отключения</span>
          )}
          {disableTask.status === 'SUCCEEDED' && (
            <span className={`task-status ${isDisableConfirmed ? 'task-status--done' : 'task-status--pending'}`}>
              {isDisableConfirmed
                ? `✅ ${ad.delivery_status || 'OFF'} подтверждён`
                : '⏳ Клик выполнен, ждём OFF'}
            </span>
          )}
        </div>
      )}
    </div>
  );
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
      const incidentsPromise = getDashboardIncidents({ limit: 200 }).catch(() => []);
      const [allData, statsData, taskData, incidentsData] = await Promise.all([
        getAdSnapshots({ limit: 200 }),
        getDashboardStats(),
        getDisableTasks({ limit: 50 }),
        incidentsPromise,
      ]);
      setAllAds(allData);
      setLastScanAt(statsData.last_scan_at || null);
      setTasks(taskData);
      setIncidents(normalizeIncidentList(incidentsData));
      setError(null);
    } catch (e) {
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
      await loadAds();
    },
    {
      enabled: true,
      intervalMs: 10000,
    },
  );

  useRefreshOnResume(() => {
    void loadAds();
  });

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
    return [...result].sort((a, b) => compareAds(a, b, sortBy, sortDirection));
  }, [sourceAds, offerFilter, stateFilter, sortBy, sortDirection]);

  return (
    <div className="ads-page">
      {error && <div className="error-banner">⚠ {error}</div>}

      {/* Панель фильтров */}
      <div className="ads-toolbar">
        <div className="view-tabs">
          <button
            className={`view-tab ${view === 'active' ? 'view-tab--active' : ''}`}
            onClick={() => { setView('active'); setStateFilter(''); }}
          >
            Активные
            <span className="view-tab__count">{activeAds.length}</span>
          </button>
          <button
            className={`view-tab ${view === 'archive' ? 'view-tab--active' : ''}`}
            onClick={() => { setView('archive'); setStateFilter(''); }}
          >
            Архив
            <span className="view-tab__count view-tab__count--muted">{archiveAds.length}</span>
          </button>
          <button
            className={`view-tab ${view === 'all' ? 'view-tab--active' : ''}`}
            onClick={() => { setView('all'); setStateFilter(''); }}
          >
            Все
            <span className="view-tab__count view-tab__count--muted">{allAds.length}</span>
          </button>
        </div>

        <div className="ads-toolbar__controls">
          <div className="ads-filters">
            {offerCodes.length > 0 && (
              <select
                className="filter-select"
                value={offerFilter}
                onChange={(e) => setOfferFilter(e.target.value)}
              >
                <option value="">Все офферы</option>
                {offerCodes.map((c) => (
                  <option key={c} value={c}>{c}</option>
                ))}
              </select>
            )}

            <select
              className="filter-select"
              value={stateFilter}
              onChange={(e) => setStateFilter(e.target.value)}
            >
              <option value="">Все статусы</option>
              <option value="NORMAL">Норма</option>
              <option value="EARLY_SIGNAL_SENT">Ранний сигнал</option>
              <option value="WARNING_SENT">Предупреждение</option>
              <option value="STOP_SENT">Стоп</option>
              <option value="CLAIMED">Ожидает OFF</option>
              <option value="DISABLED">Отключено</option>
              {archiveAds.length > 0 && <option value="ARCHIVED">Архив</option>}
            </select>
          </div>

          <div className="ads-sort-row">
            <span className="ads-sort-row__label">Сортировка</span>
            <select
              className="filter-select filter-select--sort"
              value={sortBy}
              onChange={(event) => {
                const nextSortBy = event.target.value;
                setSortBy(nextSortBy);
                const nextOption = SORT_OPTIONS.find((option) => option.value === nextSortBy);
                if (nextOption) {
                  setSortDirection(nextOption.defaultDirection);
                }
              }}
            >
              {SORT_OPTIONS.map((option) => (
                <option key={option.value} value={option.value}>
                  {option.label}
                </option>
              ))}
            </select>

            <select
              className="filter-select filter-select--narrow"
              value={sortDirection}
              onChange={(event) => setSortDirection(event.target.value)}
            >
              <option value="asc">По возрастанию</option>
              <option value="desc">По убыванию</option>
            </select>
          </div>
        </div>
      </div>

      {/* Счётчик */}
      <div className="ads-count">
        Показано: {filtered.length}
        {view === 'active' && archiveAds.length > 0 && (
          <span className="ads-count__archive"> · В архиве: {archiveAds.length}</span>
        )}
      </div>

      {/* Сетка карточек */}
      {loading && allAds.length === 0 ? (
        <div className="ads-loading">Загрузка...</div>
      ) : filtered.length === 0 ? (
        <div className="ads-empty">
          {view === 'active'
            ? 'Нет активных объявлений за текущую сессию'
            : view === 'archive'
            ? 'Архив пуст'
            : 'Нет объявлений'}
        </div>
      ) : (
        <div className="ads-cards-grid">
          {filtered.map((ad) => (
          <AdCard
              key={ad.fb_ad_id}
              ad={ad}
              incident={incidentsByAdId[ad.fb_ad_id] || null}
              disableTask={tasksByAdId[ad.fb_ad_id] || null}
              onClick={() => setTimelineAdId(ad.fb_ad_id)}
              onRestartDisableWorker={handleRestartDisableWorker}
              restartingDisableWorker={restartingDisableWorker}
            />
          ))}
        </div>
      )}

      {timelineAdId && (
        <AdTimeline fbAdId={timelineAdId} onClose={() => setTimelineAdId(null)} />
      )}
    </div>
  );
}
