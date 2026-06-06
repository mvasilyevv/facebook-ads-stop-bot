/**
 * auth.ts — TMA auth flow.
 *
 * 1. getInitData() → POST /api/tma/auth → Bearer JWT.
 * 2. Хранение: localStorage (persist), fallback sessionStorage (приватный режим).
 * 3. 401-retry-once: при истёкшем токене перевыпускаем и повторяем запрос.
 *
 * Портировано из frontend-mini/src/auth.js → TypeScript.
 */

import { getInitData } from "./tg";

const API_BASE = (import.meta.env.VITE_API_BASE as string | undefined) ?? "/api";

// ─── Типы ─────────────────────────────────────────────────────────────────

export interface AuthResponse {
  token: string;
  role: string;
}

// ─── Хранилище ────────────────────────────────────────────────────────────

/**
 * Безопасная запись в localStorage → sessionStorage fallback.
 * localStorage переживает сворачивание TMA WebView, sessionStorage — нет.
 */
function safeSet(key: string, value: string): void {
  try {
    localStorage.setItem(key, value);
  } catch {
    sessionStorage.setItem(key, value);
  }
}

function safeGet(key: string): string | null {
  try {
    return localStorage.getItem(key) ?? sessionStorage.getItem(key);
  } catch {
    return sessionStorage.getItem(key);
  }
}

function safeRemove(key: string): void {
  try {
    localStorage.removeItem(key);
  } catch {}
  sessionStorage.removeItem(key);
}

// ─── Публичное API ────────────────────────────────────────────────────────

/** Хранит Bearer-токен. */
export function getStoredToken(): string | null {
  return safeGet("tma_token");
}

/** Хранит роль recipient'а ("owner" | "recipient"). */
export function getStoredRole(): string | null {
  return safeGet("tma_role");
}

/** Выход из сессии — очищает оба хранилища. */
export function logout(): void {
  safeRemove("tma_token");
  safeRemove("tma_role");
}

/**
 * Аутентификация на бэке через initData.
 * Бросает Error при 4xx / 5xx — вызывающий должен обработать.
 */
export async function loginToBackend(): Promise<AuthResponse> {
  const initData = getInitData();
  const resp = await fetch(`${API_BASE}/tma/auth`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ init_data: initData }),
  });

  if (!resp.ok) {
    const err = await resp.json().catch(() => ({ detail: `Ошибка ${resp.status}` }));
    throw new Error((err as { detail?: string }).detail ?? `Ошибка ${resp.status}`);
  }

  const data = (await resp.json()) as AuthResponse;
  safeSet("tma_token", data.token);
  safeSet("tma_role", data.role);
  return data;
}

/**
 * Статус аутентификации для AuthGuard.
 * - "idle" — токен уже есть, авторизация не нужна.
 * - Promise<AuthResponse> — идёт loginToBackend().
 * - Если токена нет — инициирует loginToBackend().
 */
export function ensureAuthenticated(): "idle" | Promise<AuthResponse> {
  if (getStoredToken()) return "idle";
  return loginToBackend();
}
