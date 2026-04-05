/* === API-клиент для бекенда === */

const BASE = '/api';

/** API-ключ: берём из Vite env или из localStorage (для ручной настройки). */
const API_KEY = import.meta.env.VITE_API_KEY || localStorage.getItem('api_key') || '';

async function request(url, options = {}) {
  const headers = { 'Content-Type': 'application/json', ...options.headers };
  if (API_KEY) {
    headers['X-API-Key'] = API_KEY;
  }
  // signal передаётся из AbortController для отмены запроса при unmount
  const { signal, ...restOptions } = options;
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

// Telegram получатели (мультипользователи)
export const getTelegramRecipients = () => request('/settings/telegram/recipients');
export const deleteTelegramRecipient = (id) =>
  request(`/settings/telegram/recipients/${id}`, { method: 'DELETE' });
export const createInviteCode = () =>
  request('/settings/telegram/recipients/invite', { method: 'POST' });
