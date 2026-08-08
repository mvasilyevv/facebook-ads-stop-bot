/**
 * auth.ts — TMA auth flow.
 *
 * 1. getInitData() → POST /api/tma/auth → Bearer JWT.
 * 2. Токен и роль живут только в памяти текущего WebView.
 * 3. 401-retry-once: при истёкшем токене перевыпускаем и повторяем запрос.
 *
 * Портировано из frontend-mini/src/auth.js → TypeScript.
 */

import { useSyncExternalStore } from "react";
import { createOperatorFetchClient, createOperatorQueryClient } from "@fb/operator-api";
import type { components } from "@fb/shared/api/generated";
import { validateOperatorPayload } from "@fb/shared/operator/runtimeValidation";

import { getInitData } from "./tg";

// ─── Типы ────────────────────────────────────────────────────────────────

export type AuthResponse = components["schemas"]["TmaAuthResponse"];

type AuthSessionListener = () => void;

const authSessionListeners = new Set<AuthSessionListener>();
let authSession: AuthResponse | null = null;

function emitAuthSessionChange(): void {
  for (const listener of [...authSessionListeners]) listener();
}

/** Subscribe to token rotations inside the current TMA WebView. */
export function subscribeAuthSession(listener: AuthSessionListener): () => void {
  authSessionListeners.add(listener);
  return () => {
    authSessionListeners.delete(listener);
  };
}

// ─── Сессия текущего запуска ────────────────────────────────────────

/**
 * Старые сборки хранили identity на весь origin. Мы никогда её не читаем и
 * best-effort удаляем перед launch-auth.
 */
function clearLegacyPersistedIdentity(): void {
  if (typeof window === "undefined") return;
  try {
    window.localStorage.removeItem("tma_token");
    window.localStorage.removeItem("tma_role");
  } catch {
    /* Web Storage может быть запрещён WebView. */
  }
  try {
    window.sessionStorage.removeItem("tma_token");
    window.sessionStorage.removeItem("tma_role");
  } catch {
    /* Web Storage может быть запрещён WebView. */
  }
}

function replaceAuthSession(nextSession: AuthResponse | null): void {
  authSession = nextSession;
  emitAuthSessionChange();
}

// ─── Публичное API ──────────────────────────────────────────────────────────────

/** Хранит Bearer-токен текущего launch только в памяти. */
export function getStoredToken(): string | null {
  return authSession?.token ?? null;
}

/** Reactive token used by transports which must reconnect after a 401 refresh. */
export function useStoredToken(): string | null {
  return useSyncExternalStore(subscribeAuthSession, getStoredToken, () => null);
}

/** Хранит роль recipient'а ("owner" | "recipient") только в памяти. */
export function getStoredRole(): string | null {
  return authSession?.role ?? null;
}

/** Выход из сессии — очищает launch identity и legacy storage. */
export function logout(): void {
  launchAuthenticated = false;
  replaceAuthSession(null);
  clearLegacyPersistedIdentity();
}

/**
 * Аутентификация на бэке через initData.
 * Бросает Error при 4xx / 5xx — вызывающий должен обработать.
 */
/** Таймаут логина: висящий бэк не должен держать TMA на сплеше бесконечно. */
const LOGIN_TIMEOUT_MS = 15_000;

export async function loginToBackend(): Promise<AuthResponse> {
  clearLegacyPersistedIdentity();
  const initData = getInitData();
  let data: AuthResponse;
  try {
    const result = await unauthenticatedApi.POST("/api/tma/auth", {
      body: { init_data: initData },
      // AbortSignal.timeout: при недоступном бэке падаем с понятной ошибкой,
      // AuthGuard покажет её вместо вечного сплеша.
      signal: AbortSignal.timeout(LOGIN_TIMEOUT_MS),
    });
    if (!result.response.ok || !result.data) {
      const message =
        typeof result.error === "object" &&
        result.error !== null &&
        "message" in result.error &&
        typeof result.error.message === "string"
          ? result.error.message
          : `Ошибка ${result.response.status}`;
      throw new Error(message);
    }
    data = result.data;
  } catch (e) {
    if (e instanceof Error && (e.name === "TimeoutError" || e.name === "AbortError")) {
      throw new Error("Сервер не отвечает — попробуйте открыть приложение позже");
    }
    throw e;
  }
  replaceAuthSession(data);
  return data;
}

const apiBaseUrl = globalThis.location?.origin ?? "http://localhost";
const dynamicFetch: typeof globalThis.fetch = (...args) => globalThis.fetch(...args);
const unauthenticatedApi = createOperatorFetchClient({ baseUrl: apiBaseUrl, fetch: dynamicFetch });

/**
 * The sole TMA transport: it owns Bearer rotation and is shared by every
 * generated OpenAPI operation. `/api/tma/auth` deliberately uses the unauthenticated
 * client above to avoid recursively refreshing a rejected token.
 */
export async function tmaAuthenticatedFetch(
  input: RequestInfo | URL,
  init?: RequestInit,
): Promise<Response> {
  const requestSource = new Request(input, init);
  const send = (token: string | null) => {
    const attempt = requestSource.clone();
    const headers = new Headers(attempt.headers);
    headers.delete("Authorization");
    if (token) headers.set("Authorization", `Bearer ${token}`);
    return fetch(new Request(attempt, { headers }));
  };
  const attemptedToken = getStoredToken();
  let response = await send(attemptedToken);
  if (response.status !== 401) return validateTmaOperatorResponse(response, requestSource.url);
  try {
    response = await send(await refreshTmaSession(attemptedToken));
  } catch {
    return response;
  }
  return validateTmaOperatorResponse(response, requestSource.url);
}

async function validateTmaOperatorResponse(response: Response, requestUrl: string): Promise<Response> {
  if (!response.ok) return response;
  const path = new URL(requestUrl, globalThis.location?.origin ?? "http://localhost").pathname;
  if (!path.startsWith("/api/operator/")) return response;
  const body: unknown = await response.clone().json().catch(() => {
    throw new Error(`Некорректный JSON API: ${path}`);
  });
  try {
    validateOperatorPayload(path, body);
  } catch {
    throw new Error(`Некорректный ответ API: ${path}`);
  }
  return response;
}

let authRefreshInFlight: Promise<string> | null = null;
let launchAuthenticationInFlight: Promise<AuthResponse> | null = null;
let launchAuthenticated = false;

export async function refreshTmaSession(expiredToken: string | null): Promise<string> {
  const currentToken = getStoredToken();
  if (currentToken && currentToken !== expiredToken) return currentToken;
  if (!authRefreshInFlight) {
    const refresh = loginToBackend()
      .then(({ token }) => token)
      .catch((error) => {
        if (getStoredToken() === expiredToken) logout();
        throw error;
      })
      .finally(() => {
        if (authRefreshInFlight === refresh) authRefreshInFlight = null;
      });
    authRefreshInFlight = refresh;
  }
  return authRefreshInFlight;
}

/** Authenticated OpenAPI query factory for all TMA endpoints. */
export const tmaApi = createOperatorQueryClient({ baseUrl: apiBaseUrl, fetch: tmaAuthenticatedFetch });
export const tmaFetchApi = createOperatorFetchClient({ baseUrl: apiBaseUrl, fetch: tmaAuthenticatedFetch });

/**
 * Обязательная аутентификация текущего launch для AuthGuard.
 * Первый вызов в каждом JS/WebView boot всегда перепроверяет текущий
 * Telegram initData на backend. Параллельные React StrictMode effects делят один
 * запрос, но persisted identity до boot-auth не считается доверенной.
 */
export function ensureAuthenticated(): Promise<AuthResponse> {
  if (launchAuthenticated && authSession) return Promise.resolve(authSession);
  if (launchAuthenticationInFlight) return launchAuthenticationInFlight;

  clearLegacyPersistedIdentity();
  replaceAuthSession(null);
  const authentication = loginToBackend()
    .then((session) => {
      if (launchAuthenticationInFlight === authentication) {
        launchAuthenticated = true;
      }
      return session;
    })
    .finally(() => {
      if (launchAuthenticationInFlight === authentication) {
        launchAuthenticationInFlight = null;
      }
    });
  launchAuthenticationInFlight = authentication;
  return authentication;
}
