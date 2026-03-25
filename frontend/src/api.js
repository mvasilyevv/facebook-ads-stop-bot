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

// Настройки Telegram
export const getTelegramSettings = () => request('/settings/telegram');
export const updateTelegramSettings = (data) =>
  request('/settings/telegram', { method: 'PUT', body: JSON.stringify(data) });

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
  const qs = new URLSearchParams(params).toString();
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
export const getSpendHistory = (params = {}) => {
  const qs = new URLSearchParams(params).toString();
  return request(`/dashboard/spend-history${qs ? '?' + qs : ''}`);
};
