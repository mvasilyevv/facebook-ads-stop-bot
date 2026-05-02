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
