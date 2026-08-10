/** Error and auth helpers shared by the generated OpenAPI transport. */

import { isApiProblem } from "@fb/operator-api";

export class ApiError extends Error {
  status: number;
  payload: unknown;

  constructor(message: string, status: number, payload: unknown) {
    super(message);
    this.name = "ApiError";
    this.status = status;
    this.payload = payload;
  }
}

/** Reject decoded JSON whose runtime shape contradicts the generated contract. */
export function invalidApiPayload(endpoint: string, payload: unknown): ApiError {
  return new ApiError(`Некорректный ответ API: ${endpoint}`, 502, payload);
}

export function shouldRetryApiQuery(failureCount: number, error: unknown): boolean {
  if (isApiProblem(error)) return false;
  if (error instanceof ApiError) {
    if (error.status < 500 || error.message.startsWith("Некорректный ответ API:")) {
      return false;
    }
  }
  return failureCount < 2;
}

/** Redirect a browser session to Telegram login while preserving its current route. */
export function redirectToLoginOnUnauthorized(resp: Response): void {
  if (resp.status !== 401 || typeof window === "undefined") return;
  const returnTo = `${window.location.pathname}${window.location.search}${window.location.hash}`;
  const fallback = `/auth/login?${new URLSearchParams({ return_to: returnTo }).toString()}`;
  const requested = resp.headers.get("X-Auth-Login-Url") || fallback;
  const destination = new URL(requested, window.location.origin);
  if (destination.origin === window.location.origin) {
    window.location.assign(destination.href);
  }
}
