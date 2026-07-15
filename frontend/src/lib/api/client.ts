/**
 * Базовый HTTP-клиент над fetch.
 *
 * - Префикс /api для всех путей.
 * - Same-origin auth завершается в Caddy; master API key в браузер не попадает.
 * - JSON по умолчанию; FormData передаётся как есть.
 * - Унифицированный error message с разбором FastAPI detail (string / array / object).
 *
 * В компонентах НЕ вызывать fetch напрямую — только через `apiGet`/`apiSend`
 * или через домен-специфичные модули (dashboard.ts, ads.ts и т.д.).
 */

import type { QueryParams } from "@/lib/types/api";

const BASE = "/api";

export class ApiError extends Error {
  status: number;
  detail: unknown;

  constructor(message: string, status: number, detail: unknown) {
    super(message);
    this.name = "ApiError";
    this.status = status;
    this.detail = detail;
  }
}

interface RequestOptions {
  method?: "GET" | "POST" | "PUT" | "PATCH" | "DELETE";
  body?: unknown;
  signal?: AbortSignal;
  headers?: Record<string, string>;
}

/** Низкоуровневый fetch с auth/headers; бросает ApiError на !ok. Возвращает Response. */
async function rawFetch(path: string, options: RequestOptions = {}): Promise<Response> {
  const { method = "GET", body, signal, headers: extraHeaders = {} } = options;
  const FormDataCtor = globalThis.FormData;
  const isFormData = typeof FormDataCtor !== "undefined" && body instanceof FormDataCtor;

  const headers: Record<string, string> = { ...extraHeaders };
  if (!isFormData && body != null && !headers["Content-Type"]) {
    headers["Content-Type"] = "application/json";
  }

  const resp = await fetch(`${BASE}${path}`, {
    method,
    cache: "no-store",
    headers,
    body: isFormData ? (body as BodyInit) : body != null ? JSON.stringify(body) : undefined,
    signal,
  });

  if (!resp.ok) {
    throw await buildApiError(resp);
  }
  return resp;
}

/**
 * Парсит тело успешного (resp.ok) ответа: 204 → null, json → объект.
 *
 * Не-JSON тело успешного ответа (мисконфиг endpoint'а, edge-кейс прокси) раньше
 * молча кастовался в T — каллер получал строку вместо ожидаемого объекта и падал
 * на .property с undefined (M9-аудит). Бросаем явную ApiError вместо тихой подмены
 * типа: ни один каллер apiGet/apiSend в проекте не ожидает text/blob-ответ.
 */
async function parseBody<T>(resp: Response): Promise<T> {
  if (resp.status === 204) {
    return null as T;
  }
  const ct = resp.headers.get("content-type") || "";
  if (ct.includes("application/json")) {
    return (await resp.json()) as T;
  }
  const text = await resp.text();
  throw new ApiError(
    `Ожидался JSON-ответ, получен content-type=${ct || "(пусто)"}`,
    resp.status,
    text,
  );
}

async function request<T>(path: string, options: RequestOptions = {}): Promise<T> {
  const resp = await rawFetch(path, options);
  return parseBody<T>(resp);
}

export async function buildApiError(resp: Response): Promise<ApiError> {
  let detail: unknown = null;
  let message = `Ошибка API ${resp.status}: ${resp.statusText || "неизвестная ошибка"}`;

  try {
    const ct = resp.headers.get("content-type") || "";
    if (ct.includes("application/json")) {
      const body = await resp.json();
      detail = body?.detail ?? body;

      if (typeof detail === "string") {
        message = `Ошибка API ${resp.status}: ${detail}`;
      } else if (Array.isArray(detail)) {
        const parts = detail.map((item) => {
          if (typeof item === "string") return item;
          if (item && typeof item === "object") {
            const obj = item as { msg?: string; message?: string };
            return obj.msg ?? obj.message ?? JSON.stringify(item);
          }
          return String(item);
        });
        message = `Ошибка API ${resp.status}: ${parts.join(", ")}`;
      } else if (detail) {
        message = `Ошибка API ${resp.status}: ${JSON.stringify(detail)}`;
      }
    } else {
      const text = (await resp.text()).trim();
      if (text) {
        detail = text;
        message = `Ошибка API ${resp.status}: ${text}`;
      }
    }
  } catch {
    // Игнорируем ошибки парсинга — message останется дефолтным.
  }

  return new ApiError(message, resp.status, detail);
}

/** Собирает query-string из объекта, фильтруя null/undefined/пустые. */
export function buildQuery(params: QueryParams | undefined): string {
  if (!params) return "";
  const clean: Record<string, string> = {};
  for (const [key, value] of Object.entries(params)) {
    if (value == null || value === "") continue;
    clean[key] = String(value);
  }
  const qs = new URLSearchParams(clean).toString();
  return qs ? `?${qs}` : "";
}

/** GET с query-параметрами. */
export function apiGet<T>(path: string, params?: QueryParams, signal?: AbortSignal): Promise<T> {
  const full = `${path}${buildQuery(params)}`;
  return request<T>(full, { method: "GET", signal });
}

/** GET, возвращающий тело + общее число из заголовка X-Total-Count (для пагинации). */
export async function apiGetWithCount<T>(
  path: string,
  params?: QueryParams,
  signal?: AbortSignal,
): Promise<{ data: T; total: number | null }> {
  const full = `${path}${buildQuery(params)}`;
  const resp = await rawFetch(full, { method: "GET", signal });
  const header = resp.headers.get("X-Total-Count");
  const total = header != null && header !== "" ? Number(header) : null;
  const data = await parseBody<T>(resp);
  return { data, total: Number.isNaN(total as number) ? null : total };
}

/** POST/PUT/PATCH/DELETE с JSON body. */
export function apiSend<T>(
  method: "POST" | "PUT" | "PATCH" | "DELETE",
  path: string,
  body?: unknown,
  signal?: AbortSignal,
): Promise<T> {
  return request<T>(path, { method, body, signal });
}

export const apiClient = {
  get: apiGet,
  post: <T>(path: string, body?: unknown) => apiSend<T>("POST", path, body),
  put: <T>(path: string, body?: unknown) => apiSend<T>("PUT", path, body),
  patch: <T>(path: string, body?: unknown) => apiSend<T>("PATCH", path, body),
  delete: <T>(path: string, body?: unknown) => apiSend<T>("DELETE", path, body),
};
