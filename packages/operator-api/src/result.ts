import type { ApiProblem } from "@fb/shared/operator/contracts";

export class GeneratedApiError extends Error {
  readonly status: number;
  readonly payload: unknown;

  constructor(status: number, payload: unknown) {
    super(apiProblemMessage(payload, `Ошибка API ${status}`));
    this.name = "GeneratedApiError";
    this.status = status;
    this.payload = payload;
  }
}

export interface GeneratedApiResult<T> {
  data?: T;
  error?: unknown;
  response: Response;
}

/** Return typed success data or throw a readable canonical API error. */
export async function dataOrThrow<T>(
  promise: Promise<GeneratedApiResult<T>>,
): Promise<T> {
  const result = await promise;
  if (result.response.ok && result.data !== undefined) return result.data;
  throw new GeneratedApiError(result.response.status, result.error);
}

/** Validate a generated 204 operation without inventing a response body. */
export async function noContentOrThrow(
  promise: Promise<GeneratedApiResult<unknown>>,
): Promise<void> {
  const result = await promise;
  if (result.response.ok) return;
  throw new GeneratedApiError(result.response.status, result.error);
}

export function apiProblemMessage(
  value: unknown,
  fallback = "Неизвестная ошибка",
): string {
  if (!isApiProblem(value)) {
    return value instanceof Error ? value.message : fallback;
  }
  return value.correlation_id
    ? `${value.message} · reference ${value.correlation_id}`
    : value.message;
}

/**
 * Operator-facing errors expose recovery copy only. Correlation identifiers
 * remain available to diagnostics, but are never rendered as raw UUIDs.
 */
export function safeApiProblemMessage(
  value: unknown,
  fallback = "Данные временно недоступны",
): string {
  const payload = value instanceof GeneratedApiError ? value.payload : value;
  return isApiProblem(payload) ? payload.message : fallback;
}

export function isApiProblem(value: unknown): value is ApiProblem {
  if (!value || typeof value !== "object" || Array.isArray(value)) return false;
  const candidate = value as Partial<ApiProblem>;
  return (
    typeof candidate.code === "string" &&
    typeof candidate.message === "string" &&
    typeof candidate.correlation_id === "string" &&
    (candidate.field_errors === null ||
      candidate.field_errors === undefined ||
      typeof candidate.field_errors === "object")
  );
}
