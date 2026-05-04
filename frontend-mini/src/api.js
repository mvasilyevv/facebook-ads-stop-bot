import { getStoredToken } from "./auth.js";

const API_BASE = import.meta.env.VITE_API_BASE || "/api";

// Базовый fetch с Authorization Bearer из sessionStorage
export async function fetchJson(path, opts = {}) {
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

  if (!resp.ok) {
    const err = await resp.json().catch(() => ({}));
    throw new Error(err.detail || `Ошибка ${resp.status}`);
  }

  return resp.json();
}

export async function setTelegramWebAppUrl(web_app_url) {
  return fetchJson("/settings/telegram/web-app-url", { method: "PUT", body: JSON.stringify({ web_app_url }) });
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

// AI-чат: отправляет историю, получает ответ + tool_calls
export async function askAI(messages, allowTools = true) {
  return fetchJson("/chat/ask", {
    method: "POST",
    body: JSON.stringify({ messages, allow_tools: allowTools }),
  });
}
