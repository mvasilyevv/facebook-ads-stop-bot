/* === API-клиент для бекенда === */

const BASE = '/api';

async function request(url, options = {}) {
  const resp = await fetch(`${BASE}${url}`, {
    headers: { 'Content-Type': 'application/json', ...options.headers },
    ...options,
  });
  if (!resp.ok) {
    throw new Error(`Ошибка API: ${resp.status} ${resp.statusText}`);
  }
  return resp.json();
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
export const getAdSnapshots = (params = {}) => {
  // Фильтруем null/undefined чтобы не попадали в query string
  const clean = Object.fromEntries(Object.entries(params).filter(([, v]) => v != null));
  const qs = new URLSearchParams(clean).toString();
  return request(`/dashboard/ads${qs ? '?' + qs : ''}`);
};
export const getAlertEvents = (params = {}) => {
  const qs = new URLSearchParams(params).toString();
  return request(`/dashboard/alerts${qs ? '?' + qs : ''}`);
};
export const getDisableTasks = (params = {}) => {
  const qs = new URLSearchParams(params).toString();
  return request(`/dashboard/disable-tasks${qs ? '?' + qs : ''}`);
};
export const retryDisableTask = (id) =>
  request(`/dashboard/disable-tasks/${id}/retry`, { method: 'POST' });
export const getSpendHistory = (params = {}) => {
  const qs = new URLSearchParams(params).toString();
  return request(`/dashboard/spend-history${qs ? '?' + qs : ''}`);
};
export const getChartData = (params = {}) => {
  const qs = new URLSearchParams(params).toString();
  return request(`/dashboard/chart-data${qs ? '?' + qs : ''}`);
};
export const getDashboardPerformance = (params = {}) => {
  const qs = new URLSearchParams(params).toString();
  return request(`/dashboard/performance${qs ? '?' + qs : ''}`);
};
export const getAdTimeline = (fb_ad_id) => request(`/ads/${fb_ad_id}/timeline`);
export const restartObserver = () => request('/observer/restart', { method: 'POST' });

// Vision настройки
export const getVisionSettings = () => request('/settings/vision');
export const updateVisionSettings = (data) =>
  request('/settings/vision', { method: 'PUT', body: JSON.stringify(data) });
export const visionReconnect = () =>
  request('/vision/reconnect', { method: 'POST' });
export const getVisionProfiles = () => request('/vision/profiles');

// Telegram получатели (мультипользователи)
export const getTelegramRecipients = () => request('/settings/telegram/recipients');
export const deleteTelegramRecipient = (id) =>
  request(`/settings/telegram/recipients/${id}`, { method: 'DELETE' });
export const createInviteCode = () =>
  request('/settings/telegram/recipients/invite', { method: 'POST' });
