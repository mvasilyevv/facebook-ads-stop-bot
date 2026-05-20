/* === API-клиент для бекенда === */

const BASE = '/api';

/** API-ключ: берём из Vite env или из localStorage (для ручной настройки). */
const API_KEY = import.meta.env.VITE_API_KEY || localStorage.getItem('api_key') || '';

async function request(url, options = {}) {
  const { signal, headers: optionHeaders = {}, ...restOptions } = options;
  const FormDataCtor = globalThis.FormData;
  const isFormData = typeof FormDataCtor !== 'undefined' && restOptions.body instanceof FormDataCtor;
  const headers = { ...optionHeaders };
  if (!isFormData && !headers['Content-Type'] && !headers['content-type']) {
    headers['Content-Type'] = 'application/json';
  }
  if (API_KEY) {
    headers['X-API-Key'] = API_KEY;
  }
  const resp = await fetch(`${BASE}${url}`, {
    cache: restOptions.cache ?? 'no-store',
    headers,
    ...(signal ? { signal } : {}),
    ...restOptions,
  });
  if (!resp.ok) {
    let detail = '';
    const contentType = resp.headers.get('content-type') || '';
    try {
      if (contentType.includes('application/json')) {
        const body = await resp.json();
        if (typeof body === 'string') {
          detail = body;
        } else if (Array.isArray(body?.detail)) {
          detail = body.detail
            .map((item) => {
              if (typeof item === 'string') return item;
              if (item && typeof item === 'object') return item.msg || item.message || JSON.stringify(item);
              return String(item);
            })
            .join(', ');
        } else if (body?.detail != null) {
          detail = typeof body.detail === 'string' ? body.detail : JSON.stringify(body.detail);
        } else if (body?.message) {
          detail = typeof body.message === 'string' ? body.message : JSON.stringify(body.message);
        } else if (Object.keys(body || {}).length > 0) {
          detail = JSON.stringify(body);
        }
      } else {
        detail = (await resp.text()).trim();
      }
    } catch {
      detail = '';
    }

    const message = detail
      ? `Ошибка API ${resp.status}: ${detail}`
      : `Ошибка API ${resp.status}: ${resp.statusText || 'неизвестная ошибка'}`;
    throw new Error(message);
  }
  if (resp.status === 204) {
    return null;
  }
  return resp.json();
}

/** GET-запрос с query-параметрами (фильтрует null/undefined/пустые строки). */
function requestWithQuery(path, params = {}) {
  const clean = Object.fromEntries(
    Object.entries(params).filter(([, v]) => v != null && v !== ''),
  );
  const qs = new URLSearchParams(clean).toString();
  return request(`${path}${qs ? '?' + qs : ''}`);
}

// Настройки observer
export const getObserverSettings = () => request('/settings/observer');
export const updateObserverSettings = (data) =>
  request('/settings/observer', { method: 'PUT', body: JSON.stringify(data) });
// Быстрое переключение сканирования
export const toggleScanning = (enabled) =>
  request('/settings/observer/scanning', { method: 'PATCH', body: JSON.stringify({ enabled }) });
// Немедленный запуск скана
export const triggerScanNow = () =>
  request('/settings/observer/scan-now', { method: 'POST' });

// Настройки Telegram
export const getTelegramSettings = () => request('/settings/telegram');
export const setTelegramToken = (bot_token) =>
  request('/settings/telegram/token', { method: 'PUT', body: JSON.stringify({ bot_token }) });
export const revokeTelegram = () =>
  request('/settings/telegram', { method: 'DELETE' });
export const setTelegramWebAppUrl = (web_app_url) =>
  request('/settings/telegram/web-app-url', { method: 'PUT', body: JSON.stringify({ web_app_url }) });

// Офферы
export const getOffers = () => request('/offers');
export const createOffer = (data) =>
  request('/offers', { method: 'POST', body: JSON.stringify(data) });
export const updateOffer = (id, data) =>
  request(`/offers/${id}`, { method: 'PUT', body: JSON.stringify(data) });
export const deleteOffer = (id) =>
  request(`/offers/${id}`, { method: 'DELETE' });
export const getOfferRules = (id) => request(`/offers/${id}/rules`);
export const updateOfferRules = (id, data) =>
  request(`/offers/${id}/rules`, { method: 'PUT', body: JSON.stringify(data) });

// Dashboard
export const getDashboardStats = () => request('/dashboard/stats');
export const getDashboardBatch = (params = {}) => requestWithQuery('/dashboard/batch', params);
export const getAdSnapshots = (params = {}) => requestWithQuery('/dashboard/ads', params);
export const getAlertEvents = (params = {}) => requestWithQuery('/dashboard/alerts', params);
export const getDashboardIncidents = (params = {}) => requestWithQuery('/dashboard/incidents', params);
export const getDisableTasks = (params = {}) => requestWithQuery('/dashboard/disable-tasks', params);
export const createDisableTask = (fbAdId) =>
  request('/dashboard/disable-tasks', { method: 'POST', body: JSON.stringify({ fb_ad_id: fbAdId }) });
export const getEnableRecommendations = (params = {}) => requestWithQuery('/dashboard/enable-recommendations', params);
export const createEnableTaskFromRecommendation = (eventId) =>
  request(`/dashboard/enable-recommendations/${eventId}/enable`, { method: 'POST' });
// requestWithQuery фильтрует null/undefined автоматически
export const getEnableTasks = (params = {}) => requestWithQuery('/dashboard/enable-tasks', params);
export const retryDisableTask = (id) =>
  request(`/dashboard/disable-tasks/${id}/retry`, { method: 'POST' });
export const cancelDisableTask = (id) =>
  request(`/dashboard/disable-tasks/${id}`, { method: 'DELETE' });
/** Сырые AdSnapshot за окно hours (не бакеты). Для графиков — /dashboard/performance. */
export const getSpendHistory = (params = {}) => requestWithQuery('/dashboard/spend-history', params);
export const getChartData = (params = {}) => requestWithQuery('/dashboard/chart-data', params);
export const getDashboardPerformance = (params = {}) => requestWithQuery('/dashboard/performance', params);
export const getAdTimeline = (fb_ad_id) => request(`/ads/${fb_ad_id}/timeline`);
export const restartObserver = () => request('/observer/restart', { method: 'POST' });
export const restartDisableWorker = () => request('/disable-worker/restart', { method: 'POST' });

// Vision настройки
export const getVisionSettings = () => request('/settings/vision');
export const updateVisionSettings = (data) =>
  request('/settings/vision', { method: 'PUT', body: JSON.stringify(data) });
export const visionReconnect = () =>
  request('/vision/reconnect', { method: 'POST' });
export const getVisionProfiles = () => request('/vision/profiles');

// --- История заливов ---
export const getHistorySummary = (params = {}) => requestWithQuery('/history/summary', params);
export const getHistoryTimeline = (params = {}) => requestWithQuery('/history/timeline', params);
export const getHistoryCampaigns = (params = {}) => requestWithQuery('/history/campaigns', params);
export const getHistoryEvents = (params = {}) => requestWithQuery('/history/events', params);
export const getHistoryOffers = (params = {}) => requestWithQuery('/history/offers', params);
export const getHistoryAds = (params = {}) => requestWithQuery('/history/ads', params);

// --- Ложные депозиты ---
export const getFakeDeposits = () => request('/fake-deposits');
export const setFakeDeposits = (fbAdId, fakeCount, note = '') =>
  request(`/fake-deposits/${fbAdId}`, {
    method: 'PUT',
    body: JSON.stringify({ fake_count: fakeCount, note }),
  });
export const deleteFakeDeposits = (fbAdId) =>
  request(`/fake-deposits/${fbAdId}`, { method: 'DELETE' });

// --- Трекер нейминга ---
export const getNamingPatterns = (params = {}) => requestWithQuery('/naming-tracker/patterns', params);

// --- Скрипты: уникализация креативов ---
export const createCreativeUniquifyJob = ({ offerName, copies, files }) => {
  const body = new globalThis.FormData();
  body.append('offer_name', offerName);
  body.append('copies', String(copies));
  Array.from(files || []).forEach((file) => body.append('files', file));
  return request('/tools/creative-uniquify', { method: 'POST', body });
};

export const openCreativeOutputFolder = (path) =>
  request('/tools/creative-uniquify/open-folder', {
    method: 'POST',
    body: JSON.stringify({ path }),
  });

// --- Скрипты: создание кампании из папки креативов ---
export const getCampaignCreativeFolders = () => request('/tools/campaign-create/folders');

export const buildCampaignCreatePlan = (data) =>
  request('/tools/campaign-create/plan', {
    method: 'POST',
    body: JSON.stringify(data),
  });

// Telegram получатели (мультипользователи)
export const getTelegramRecipients = () => request('/settings/telegram/recipients');
export const deleteTelegramRecipient = (id) =>
  request(`/settings/telegram/recipients/${id}`, { method: 'DELETE' });
export const createInviteCode = () =>
  request('/settings/telegram/recipients/invite', { method: 'POST' });

// --- Браузер ---
export const validateBrowserColumns = ({ startIfMissing = false } = {}) =>
  requestWithQuery('/settings/browser/validate-columns', {
    start_if_missing: startIfMissing ? 'true' : '',
  });
export const saveBrowserColumnWidths = () =>
  request('/settings/browser/save-column-widths', { method: 'POST' });
export const applyBrowserColumnWidths = () =>
  request('/settings/browser/apply-column-widths', { method: 'POST' });

// --- Авто-включение ---
export const toggleAutoEnable = (enabled) =>
  request('/settings/observer/auto-enable', {
    method: 'PATCH',
    body: JSON.stringify({ enabled }),
  });

export const getAutoEnableDisabled = () => request('/dashboard/auto-enable-disabled');
export const disableAutoEnable = (fbAdId) =>
  request(`/dashboard/auto-enable-disabled/${fbAdId}`, { method: 'POST' });
export const enableAutoEnable = (fbAdId) =>
  request(`/dashboard/auto-enable-disabled/${fbAdId}`, { method: 'DELETE' });

/** AI-чат: отправляет историю сообщений, получает ответ + tool_calls. */
export const askAI = (messages, allowTools = true) =>
  request('/chat/ask', {
    method: 'POST',
    body: JSON.stringify({ messages, allow_tools: allowTools }),
  });

// --- Campaign Recorder ---
export const startRecording = () =>
  request('/campaign-recorder/start', {
    method: 'POST',
    body: JSON.stringify({}),
  });

export const stopRecording = (sessionId) =>
  request(`/campaign-recorder/stop/${sessionId}`, { method: 'POST' });

export const getRecordingStatus = (sessionId, tail = 30) =>
  requestWithQuery(`/campaign-recorder/status/${sessionId}`, { tail });

export const analyzeLastRecording = () =>
  request('/campaign-recorder/analyze');

// --- Campaign Creator ---
export const startCampaignCreator = (data) =>
  request('/campaign-creator/start', {
    method: 'POST',
    body: JSON.stringify(data),
  });

export const getCampaignCreatorStatus = (taskId) =>
  request(`/campaign-creator/${taskId}/status`);

export const listCampaignCreatorSteps = () =>
  request('/campaign-creator/steps');

export const runCampaignCreatorStep = (taskId, stepName) =>
  request(`/campaign-creator/${taskId}/run-step/${encodeURIComponent(stepName)}`, {
    method: 'POST',
  });

export const runCampaignCreatorFrom = (taskId, stepName) =>
  request(`/campaign-creator/${taskId}/run-from/${encodeURIComponent(stepName)}`, {
    method: 'POST',
  });

export const resumeCampaignCreator = (taskId) =>
  request(`/campaign-creator/${taskId}/resume`, { method: 'POST' });

export const cancelCampaignCreator = (taskId) =>
  request(`/campaign-creator/${taskId}/cancel`, { method: 'POST' });

function activeTaskCount(tasks = []) {
  return tasks.filter((task) => ['PENDING', 'RUNNING', 'RETRYING', 'FAILED'].includes(task.status)).length;
}

function runningTaskCount(tasks = []) {
  return tasks.filter((task) => task.status === 'RUNNING').length;
}

function makeHealthNode(id, label, status, tone, headline, details = [], metrics = [], updatedAt = null) {
  return {
    id,
    label,
    status,
    tone,
    headline,
    details,
    metrics,
    updated_at: updatedAt,
  };
}

// --- Health Map собирается из существующих endpoint-ов dashboard/settings ---
export async function getDashboardHealthMap() {
  const [stats, disableTasks, enableTasks, enableRecs, telegram, vision] = await Promise.all([
    getDashboardStats().catch(() => null),
    getDisableTasks({ limit: 50 }).catch(() => []),
    getEnableTasks({ limit: 50 }).catch(() => []),
    getEnableRecommendations({ limit: 20 }).catch(() => []),
    getTelegramSettings().catch(() => null),
    getVisionSettings().catch(() => null),
  ]);

  const disableActive = activeTaskCount(disableTasks);
  const disableRunning = runningTaskCount(disableTasks);
  const enableActive = activeTaskCount(enableTasks);
  const enableRunning = runningTaskCount(enableTasks);
  const stopCount = Number(stats?.ads_in_stop ?? 0);
  const warningCount = Number(stats?.ads_in_warning ?? 0);
  const totalAlerts = stopCount + warningCount;
  const observerStatus = String(stats?.observer_status || 'UNKNOWN').toUpperCase();
  const scanEnabled = stats ? observerStatus !== 'PAUSED' : false;
  const telegramOnline = String(telegram?.poller_status || '').toUpperCase() === 'ONLINE';
  const visionReady = Boolean(vision?.profile_id && vision?.has_token);
  const visionAutoRestart = vision?.auto_restart_on_missing_cdp ?? true;
  const visionRuntimeStatus = String(vision?.runtime_status || 'NOT_CONFIGURED').toUpperCase();
  const visionCdpReady = Boolean(vision?.cdp_ready);
  const warnings = [];

  if (!stats) warnings.push('Dashboard stats недоступны.');
  if (!visionReady) warnings.push('Vision профиль или токен не настроены.');
  if (visionReady && visionRuntimeStatus === 'NOT_RUNNING') warnings.push('Профиль Vision не запущен.');
  if (visionReady && ['MISSING_CDP', 'CDP_NOT_READY'].includes(visionRuntimeStatus)) warnings.push('CDP-порт Vision недоступен.');
  if (!telegramOnline) warnings.push('Telegram poller не в состоянии ONLINE.');
  if (disableActive > 0) warnings.push(`В очереди отключения ${disableActive} задач.`);
  if (enableActive > 0) warnings.push(`В очереди включения ${enableActive} задач.`);

  const nodes = [
    makeHealthNode(
      'observer',
      'Observer',
      observerStatus,
      observerStatus === 'ERROR' ? 'danger' : observerStatus === 'WAITING_BROWSER' ? 'warning' : scanEnabled ? 'success' : 'neutral',
      stats ? 'Цикл мониторинга получает данные' : 'Нет данных observer',
      [stats?.observer_status_message || 'Статус берётся из dashboard stats.'],
      [
        { label: 'Скан', value: stats?.last_scan_at ? 'есть' : '—', tone: stats?.last_scan_at ? 'success' : 'neutral' },
        { label: 'Ads', value: stats?.total_ads_monitored ?? '—', tone: 'neutral' },
      ],
      stats?.last_scan_at,
    ),
    makeHealthNode(
      'browser_agent',
      'Browser',
      visionCdpReady ? 'READY' : visionRuntimeStatus,
      visionCdpReady ? 'success' : visionRuntimeStatus === 'NOT_RUNNING' ? 'warning' : visionReady ? 'danger' : 'warning',
      visionCdpReady ? 'Vision CDP готов' : visionReady ? 'Vision требует проверки CDP' : 'Vision требует настройки',
      [
        vision?.profile_id ? `Профиль: ${vision.profile_id}` : 'Профиль не выбран.',
        visionAutoRestart ? 'Автовосстановление CDP включено.' : 'Автовосстановление CDP выключено.',
        vision?.runtime_status_message || 'Runtime-статус Vision недоступен.',
      ],
      [
        { label: 'Token', value: vision?.has_token ? 'ok' : '—', tone: vision?.has_token ? 'success' : 'warning' },
        {
          label: 'CDP',
          value: vision?.cdp_port || (visionCdpReady ? 'ready' : '—'),
          tone: visionCdpReady ? 'success' : 'warning',
        },
      ],
      stats?.last_scan_at,
    ),
    makeHealthNode(
      'scan_batch',
      'Batch',
      stats?.last_scan_at ? 'LIVE' : 'EMPTY',
      stats?.last_scan_at ? 'success' : 'neutral',
      stats?.last_scan_at ? 'Последний scan batch сохранён' : 'Scan batch пока не найден',
      [stats?.last_scan_at ? `Последний скан: ${stats.last_scan_at}` : 'Нет времени последнего скана.'],
      [{ label: 'Мониторинг', value: stats?.total_ads_monitored ?? '—', tone: 'neutral' }],
      stats?.last_scan_at,
    ),
    makeHealthNode(
      'telegram',
      'Telegram',
      telegramOnline ? 'ONLINE' : telegram?.poller_status || 'OFFLINE',
      telegramOnline ? 'success' : telegram?.is_authorized ? 'warning' : 'danger',
      telegramOnline ? 'Доставка уведомлений активна' : 'Доставка требует проверки',
      [telegram?.is_authorized ? 'Бот авторизован.' : 'Бот не авторизован.'],
      [],
      telegram?.last_poller_heartbeat_at,
    ),
    makeHealthNode(
      'alerts',
      'Signals',
      totalAlerts > 0 ? 'STOP' : 'NORMAL',
      stopCount > 0 ? 'danger' : warningCount > 0 ? 'warning' : 'success',
      totalAlerts > 0 ? 'Есть активные сигналы' : 'Активных сигналов нет',
      [`Стоп: ${stopCount}, предупреждения: ${warningCount}.`],
      [
        { label: 'Stop', value: stopCount, tone: stopCount > 0 ? 'danger' : 'neutral' },
        { label: 'Warn', value: warningCount, tone: warningCount > 0 ? 'warning' : 'neutral' },
      ],
      stats?.last_scan_at,
    ),
    makeHealthNode(
      'disable_queue',
      'Disable queue',
      disableActive > 0 ? 'FLOWING' : 'EMPTY',
      disableActive > 0 ? 'warning' : 'success',
      disableActive > 0 ? 'Очередь отключения не пуста' : 'Очередь отключения пуста',
      [`Активных задач: ${disableActive}.`],
      [{ label: 'Active', value: disableActive, tone: disableActive > 0 ? 'warning' : 'success' }],
    ),
    makeHealthNode(
      'disable_worker',
      'Disable worker',
      disableRunning > 0 ? 'RUNNING' : disableActive > 0 ? 'WAITING' : 'IDLE',
      disableRunning > 0 ? 'warning' : disableActive > 0 ? 'info' : 'success',
      disableRunning > 0 ? 'Идёт отключение объявлений' : 'Нет активного отключения',
      [`В работе: ${disableRunning}.`],
      [{ label: 'Run', value: disableRunning, tone: disableRunning > 0 ? 'warning' : 'neutral' }],
    ),
    makeHealthNode(
      'enable_recommendations',
      'Enable recs',
      enableRecs.length > 0 ? 'READY' : 'EMPTY',
      enableRecs.length > 0 ? 'info' : 'success',
      enableRecs.length > 0 ? 'Есть рекомендации включения' : 'Рекомендаций включения нет',
      [`Рекомендаций: ${enableRecs.length}.`],
      [{ label: 'Recs', value: enableRecs.length, tone: enableRecs.length > 0 ? 'info' : 'success' }],
    ),
    makeHealthNode(
      'enable_queue',
      'Enable queue',
      enableActive > 0 ? 'FLOWING' : 'EMPTY',
      enableActive > 0 ? 'info' : 'success',
      enableActive > 0 ? 'Очередь включения не пуста' : 'Очередь включения пуста',
      [`Активных задач: ${enableActive}.`],
      [{ label: 'Active', value: enableActive, tone: enableActive > 0 ? 'info' : 'success' }],
    ),
    makeHealthNode(
      'enable_worker',
      'Enable worker',
      enableRunning > 0 ? 'RUNNING' : enableActive > 0 ? 'WAITING' : 'IDLE',
      enableRunning > 0 ? 'info' : enableActive > 0 ? 'warning' : 'success',
      enableRunning > 0 ? 'Идёт включение объявлений' : 'Нет активного включения',
      [`В работе: ${enableRunning}.`],
      [{ label: 'Run', value: enableRunning, tone: enableRunning > 0 ? 'info' : 'neutral' }],
    ),
  ];

  return {
    generated_at: new Date().toISOString(),
    nodes,
    warnings,
  };
}

export const getAIAnalysis = (block_type, scope_key = 'global', force_refresh = false) =>
  request('/ai/analyze', {
    method: 'POST',
    body: JSON.stringify({ block_type, scope_key, force_refresh }),
  });
