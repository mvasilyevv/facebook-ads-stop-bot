import { useEffect, useMemo, useState } from 'react';
import { getAdSnapshots, getDashboardStats, getDisableTasks, getAdTimeline } from '../api.js';

// Буфер в минутах: объявления виденные в пределах N минут от последнего скана = "активные".
// Это разделяет текущую сессию от вчерашних кампаний независимо от времени суток.
const ACTIVE_SCAN_BUFFER_MS = 30 * 60 * 1000; // 30 минут
const STALE_DISABLE_TASK_MS = 5 * 60 * 1000;

const STATE_ORDER = {
  STOP_SENT: 0,
  WARNING_SENT: 1,
  CLAIMED: 2,
  NORMAL: 3,
  DISABLED: 4,
};

const STATE_LABELS = {
  NORMAL: 'Норма',
  WARNING_SENT: 'Предупреждение',
  STOP_SENT: 'Стоп',
  CLAIMED: 'Ожидает OFF',
  DISABLED: 'Отключено',
};

const RULE_LABELS = {
  cpc_stop: 'Дорогой клик',
  cpl_stop: 'Дорогой лид',
  cpr_stop: 'Дорогая рега',
  regs_no_dep_stop: 'Реги без депозитов',
  spend_no_dep_range: 'Расход без депа',
  spend_with_dep_range: 'Расход с депозитом',
};

function ruleLabel(code) {
  return RULE_LABELS[code] || code;
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

function fmtTime(isoStr) {
  if (!isoStr) return '—';
  const d = new Date(isoStr);
  return d.toLocaleString('ru-RU', { day: '2-digit', month: '2-digit', hour: '2-digit', minute: '2-digit' });
}

function isDisableTaskStale(task) {
  if (!task?.created_at || task.status !== 'RUNNING') return false;
  return Date.now() - new Date(task.created_at).getTime() >= STALE_DISABLE_TASK_MS;
}

function isDisableTaskRelevant(ad, task) {
  if (!ad || !task) return false;
  if (task.status === 'PENDING' || task.status === 'RUNNING' || task.status === 'RETRYING' || task.status === 'FAILED') {
    return true;
  }
  if (task.status === 'SUCCEEDED') {
    return ad.alert_state === 'CLAIMED' || ad.alert_state === 'DISABLED' || ad.alert_state === 'STOP_SENT';
  }
  return false;
}

function isDeliveryOff(ad) {
  return ad?.delivery_status === 'OFF' || ad?.delivery_status === 'NOT_DELIVERING';
}

// --- Таймлайн объявления ---

const STAGE_ICONS = { WARNING: '⚠️', STOP: '🛑' };
const TASK_STATUS_ICONS = { PENDING: '⏳', RUNNING: '🔄', SUCCEEDED: '✅', RETRYING: '🔁', FAILED: '❌' };

function AdTimeline({ fbAdId, onClose }) {
  const [data, setData] = useState(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState(null);

  useEffect(() => {
    setLoading(true);
    getAdTimeline(fbAdId)
      .then(setData)
      .catch((e) => setError(e.message))
      .finally(() => setLoading(false));
  }, [fbAdId]);

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
          <button className="timeline-drawer__close" onClick={onClose}>✕</button>
        </div>

        {loading && <div className="timeline-loading">Загрузка...</div>}
        {error && <div className="timeline-error">Ошибка: {error}</div>}

        {data && !loading && (
          <>
            {/* Текущие метрики */}
            <div className="timeline-current-metrics">
              <div className="timeline-metric">
                <span>Расход</span>
                <strong>{fmt(data.current_metrics?.spend)}</strong>
              </div>
              <div className="timeline-metric">
                <span>CPC</span>
                <strong>{fmt(data.current_metrics?.cpc)}</strong>
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
                <span>Депы</span>
                <strong>{fmtNum(data.current_metrics?.deposits)}</strong>
              </div>
              <div className="timeline-metric">
                <span>Последний скан</span>
                <strong>{fmtTime(data.last_observed_at)}</strong>
              </div>
            </div>

            {/* Таймлайн событий */}
            <div className="timeline-events">
              {data.timeline.length === 0 && (
                <div className="timeline-empty">Событий пока нет</div>
              )}
              {[...(data.timeline || [])].sort((a, b) => new Date(b.time) - new Date(a.time)).map((ev, i) => (
                <div key={i} className={`timeline-event timeline-event--${ev.type === 'alert' ? (ev.stage === 'STOP' ? 'stop' : 'warning') : 'task'}`}>
                  <div className="timeline-event__time">{fmtTime(ev.time)}</div>
                  {ev.type === 'alert' && (
                    <div className="timeline-event__body">
                      <span className="timeline-event__icon">{STAGE_ICONS[ev.stage] || '📌'}</span>
                      <div className="timeline-event__content">
                        <div className="timeline-event__title">
                          {ev.stage === 'STOP' ? 'Стоп-алерт' : 'Предупреждение'}
                          {ev.matched_rules?.length > 0 && (
                            <span className="timeline-event__rules"> — {ev.matched_rules.map(ruleLabel).join(', ')}</span>
                          )}
                        </div>
                        <div className="timeline-event__metrics">
                          {ev.spend != null && <span>💰 {fmt(ev.spend)}</span>}
                          {ev.cpc != null && <span>🖱 {fmt(ev.cpc)}</span>}
                          {ev.clicks != null && <span>Кликов: {ev.clicks}</span>}
                          {ev.leads != null && <span>Лидов: {ev.leads}</span>}
                          {ev.registrations != null && <span>Реги: {ev.registrations}</span>}
                          {ev.deposits != null && <span>Депы: {ev.deposits}</span>}
                        </div>
                      </div>
                    </div>
                  )}
                  {ev.type === 'disable_task' && (
                    <div className="timeline-event__body">
                      <span className="timeline-event__icon">{TASK_STATUS_ICONS[ev.status] || '🔧'}</span>
                      <div className="timeline-event__content">
                        <div className="timeline-event__title">
                          Задача на отключение — {ev.status}
                          {ev.requested_by && <span className="timeline-event__who"> (@{ev.requested_by})</span>}
                        </div>
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

function AdCard({ ad, disableTask, onClick }) {
  const isStop = ad.alert_state === 'STOP_SENT';
  const isWarning = ad.alert_state === 'WARNING_SENT';
  const isDisabled = ad.alert_state === 'DISABLED';
  const isClaimed = ad.alert_state === 'CLAIMED';

  let cardVariant = 'normal';
  if (isStop) cardVariant = 'stop';
  else if (isWarning) cardVariant = 'warning';
  else if (isDisabled || isClaimed) cardVariant = 'disabled';

  const allRules = [
    ...(ad.stop_rule_codes || []).map((r) => ({ code: r, type: 'stop' })),
    ...(ad.warning_rule_codes || []).map((r) => ({ code: r, type: 'warn' })),
  ];

  // Дедупликация: stop приоритет
  const seenCodes = new Set();
  const rules = allRules.filter(({ code }) => {
    if (seenCodes.has(code)) return false;
    seenCodes.add(code);
    return true;
  });

  const stateLabel = STATE_LABELS[ad.alert_state] || ad.alert_state;
  const isDisableConfirmed = ad.alert_state === 'DISABLED' || isDeliveryOff(ad);

  return (
    <div className={`ad-card ad-card--${cardVariant}`} onClick={onClick} style={{ cursor: 'pointer' }}>
      <div className="ad-card__header">
        <span className={`ad-card__state-badge ad-card__state-badge--${cardVariant}`}>
          {isStop && '⛔ '}
          {isWarning && '⚠️ '}
          {isClaimed && '🔄 '}
          {isDisabled && '🔕 '}
          {!isStop && !isWarning && !isDisabled && !isClaimed && '✅ '}
          {stateLabel}
        </span>
        {ad.offer_code && (
          <span className="ad-card__offer-code">{ad.offer_code}</span>
        )}
      </div>

      <div className="ad-card__name" title={ad.ad_name}>{ad.ad_name}</div>
      {ad.campaign_name && (
        <div className="ad-card__campaign" title={ad.campaign_name}>{ad.campaign_name}</div>
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
          <span className="ad-card__metric-label">Депозиты</span>
          <span className={`ad-card__metric-value ${ad.deposits === 0 && Number(ad.spend) > 0 ? 'ad-card__metric-value--zero' : ''}`}>
            {fmtNum(ad.deposits)}
          </span>
        </div>
      </div>

      {rules.length > 0 && (
        <div className="ad-card__rules">
          {rules.map(({ code, type }) => (
            <span key={code} className={`rule-tag rule-tag--${type}`}>
              {ruleLabel(code)}
            </span>
          ))}
        </div>
      )}

      {disableTask && isDisableTaskRelevant(ad, disableTask) && (
        <div className="ad-card__disable-status">
          {disableTask.status === 'RUNNING' && (
            <span className={`task-status ${isDisableTaskStale(disableTask) ? 'task-status--stale' : 'task-status--running'}`}>
              {isDisableTaskStale(disableTask) ? '⚠️ Зависло в браузере' : '🔄 Выключаем в браузере'}
            </span>
          )}
          {disableTask.status === 'PENDING' && (
            <span className="task-status task-status--pending">⏳ В очереди на выключение</span>
          )}
          {disableTask.status === 'RETRYING' && (
            <span className="task-status task-status--retrying">🔁 Повтор ({disableTask.attempt_count}/10)</span>
          )}
          {disableTask.status === 'FAILED' && (
            <span className="task-status task-status--failed">❌ Ошибка отключения</span>
          )}
          {disableTask.status === 'SUCCEEDED' && (
            <span className={`task-status ${isDisableConfirmed ? 'task-status--done' : 'task-status--pending'}`}>
              {isDisableConfirmed ? '✅ Выключение подтверждено' : '⏳ Клик выполнен, ждём OFF'}
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
  const [allAds, setAllAds] = useState([]);
  const [lastScanAt, setLastScanAt] = useState(null);
  const [tasks, setTasks] = useState([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState(null);
  const [timelineAdId, setTimelineAdId] = useState(null);

  // Загружаем все данные одним запросом
  const loadAds = async () => {
    try {
      setLoading(true);
      const [allData, statsData, taskData] = await Promise.all([
        getAdSnapshots({ limit: 200 }),
        getDashboardStats(),
        getDisableTasks({ limit: 50 }),
      ]);
      setAllAds(allData);
      setLastScanAt(statsData.last_scan_at || null);
      setTasks(taskData);
      setError(null);
    } catch (e) {
      setError(e.message);
    } finally {
      setLoading(false);
    }
  };

  useEffect(() => {
    loadAds();
    const id = setInterval(loadAds, 10000);
    return () => clearInterval(id);
  }, []);

  // Словарь задач по fb_ad_id (последняя задача)
  const tasksByAdId = useMemo(() => {
    const map = {};
    for (const t of tasks) {
      if (!map[t.fb_ad_id] || new Date(t.created_at) > new Date(map[t.fb_ad_id].created_at)) {
        map[t.fb_ad_id] = t;
      }
    }
    return map;
  }, [tasks]);

  // Активные = объявления из последнего скана (в пределах ACTIVE_SCAN_BUFFER_MS от last_scan_at).
  // Это разделяет вчерашние кампании от сегодняшних независимо от времени суток.
  const { activeAds, archiveAds } = useMemo(() => {
    if (!lastScanAt) {
      // Если нет данных о последнем скане — всё "активное"
      return { activeAds: allAds, archiveAds: [] };
    }
    const cutoff = new Date(lastScanAt).getTime() - ACTIVE_SCAN_BUFFER_MS;
    const active = [];
    const archive = [];
    for (const ad of allAds) {
      if (ad.last_observed_at && new Date(ad.last_observed_at).getTime() >= cutoff) {
        active.push(ad);
      } else {
        archive.push(ad);
      }
    }
    return { activeAds: active, archiveAds: archive };
  }, [allAds, lastScanAt]);

  // Выбираем нужный набор по view
  const sourceAds = useMemo(() => {
    if (view === 'active') return activeAds;
    if (view === 'archive') return archiveAds;
    return allAds;
  }, [view, activeAds, archiveAds, allAds]);

  // Уникальные офферы для фильтра
  const offerCodes = useMemo(() => {
    const codes = [...new Set(sourceAds.map((a) => a.offer_code).filter(Boolean))];
    return codes.sort();
  }, [sourceAds]);

  // Фильтрация + сортировка
  const filtered = useMemo(() => {
    let result = sourceAds;
    if (offerFilter) result = result.filter((a) => a.offer_code === offerFilter);
    if (stateFilter) result = result.filter((a) => a.alert_state === stateFilter);
    // Сортировка: проблемные сначала
    return [...result].sort((a, b) => {
      const ao = STATE_ORDER[a.alert_state] ?? 99;
      const bo = STATE_ORDER[b.alert_state] ?? 99;
      return ao - bo;
    });
  }, [sourceAds, offerFilter, stateFilter]);

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
            <option value="WARNING_SENT">Предупреждение</option>
            <option value="STOP_SENT">Стоп</option>
            <option value="CLAIMED">Ожидает OFF</option>
            <option value="DISABLED">Отключено</option>
          </select>
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
            ? 'Нет активных объявлений за последние 24 часа'
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
              disableTask={tasksByAdId[ad.fb_ad_id] || null}
              onClick={() => setTimelineAdId(ad.fb_ad_id)}
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
