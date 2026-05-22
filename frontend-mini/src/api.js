import { getStoredToken, loginToBackend, logout } from "./auth.js";

const API_BASE = import.meta.env.VITE_API_BASE || "/api";

// Базовый fetch с Authorization Bearer из хранилища.
// При получении 401 один раз пытается перевыпустить токен через loginToBackend.
export async function fetchJson(path, opts = {}, _retry = false) {
  const token = getStoredToken();
  const headers = {
    "Content-Type": "application/json",
    ...(opts.headers || {}),
  };
  if (token) {
    headers["Authorization"] = `Bearer ${token}`;
  }

  const resp = await fetch(`${API_BASE}${path}`, {
    ...opts,
    headers,
  });

  if (resp.status === 401 && !_retry) {
    // Токен протух или потерян — перевыпускаем и повторяем запрос один раз.
    try {
      logout();
      await loginToBackend();
      return fetchJson(path, opts, true);
    } catch {
      // не удалось — пробросим оригинальную ошибку ниже
    }
  }

  if (!resp.ok) {
    const err = await resp.json().catch(() => ({}));
    throw new Error(err.detail || `Ошибка ${resp.status}`);
  }

  return resp.json();
}

export async function setTelegramWebAppUrl(web_app_url) {
  return fetchJson("/settings/telegram/web-app-url", { method: "PUT", body: JSON.stringify({ web_app_url }) });
}

export async function getOffers() {
  return fetchJson("/offers");
}

export async function getOfferRules(offerId) {
  return fetchJson(`/offers/${encodeURIComponent(offerId)}/rules`);
}

export async function updateOfferRules(offerId, rules) {
  return fetchJson(`/offers/${encodeURIComponent(offerId)}/rules`, {
    method: "PUT",
    body: JSON.stringify(rules),
  });
}

export async function getObserverSettings() {
  return fetchJson("/settings/observer");
}

export async function getDashboardIncidents({ limit = 5 } = {}) {
  const params = new URLSearchParams();
  if (limit != null) params.set("limit", String(limit));
  const qs = params.toString();
  return fetchJson(`/dashboard/incidents${qs ? `?${qs}` : ""}`);
}

export async function getDisableTasks({ limit = 20 } = {}) {
  const params = new URLSearchParams();
  if (limit != null) params.set("limit", String(limit));
  const qs = params.toString();
  return fetchJson(`/dashboard/disable-tasks${qs ? `?${qs}` : ""}`);
}

export async function getAdDetail(fbAdId) {
  return fetchJson(`/tma/ads/${encodeURIComponent(fbAdId)}`);
}
export async function disableAd(fbAdId, reason) {
  return fetchJson(`/tma/ads/${encodeURIComponent(fbAdId)}/disable`, {
    method: "POST",
    body: JSON.stringify({ reason: reason || null }),
  });
}
export async function snoozeAd(fbAdId, minutes) {
  return fetchJson(`/tma/ads/${encodeURIComponent(fbAdId)}/snooze`, {
    method: "POST",
    body: JSON.stringify({ minutes }),
  });
}
export async function claimAd(fbAdId) {
  return fetchJson(`/tma/ads/${encodeURIComponent(fbAdId)}/claim`, {
    method: "POST",
    body: "{}",
  });
}

// AI-аналитика с кэшированием
export async function getAIAnalysis(blockType, scopeKey = "global", forceRefresh = false, clientData = null) {
  return fetchJson("/ai/analyze", {
    method: "POST",
    body: JSON.stringify({
      block_type: blockType,
      scope_key: scopeKey,
      force_refresh: forceRefresh,
      client_data: clientData,
    }),
  });
}
