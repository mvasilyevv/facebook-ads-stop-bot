// MiniApp API client
const BASE_URL = '/api/miniapp';

function getInitData() {
  return window.Telegram?.WebApp?.initData || '';
}

async function req(path, opts = {}) {
  const res = await fetch(BASE_URL + path, {
    ...opts,
    headers: {
      'Content-Type': 'application/json',
      'X-Telegram-Init-Data': getInitData(),
      ...(opts.headers || {}),
    },
  });
  if (!res.ok) {
    const text = await res.text().catch(() => '');
    throw new Error(`${res.status}: ${text}`);
  }
  return res.json();
}

export const getDashboard = () => req('/dashboard');
export const getAlerts = (params = {}) =>
  req(`/alerts?limit=${params.limit ?? 20}&offset=${params.offset ?? 0}`);
export const getAd = (fbAdId) => req(`/ad/${encodeURIComponent(fbAdId)}`);
export const disableAd = (fbAdId) =>
  req(`/disable/${encodeURIComponent(fbAdId)}`, { method: 'POST' });
export const getAnalytics = () => req('/analytics');
