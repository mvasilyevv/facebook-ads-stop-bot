/**
 * Хелперы для срока жизни черновиков (draft tasks).
 *
 * Reconciler (core/tasks/queue.py::cancel_stale_drafts) дропает DRAFT-задачи
 * старше 24 часов: older_than_seconds = 24 * 3600.
 * UI показывает предупреждение, когда до истечения < 1 часа (порог по умолчанию).
 */

/** 24 часа в миллисекундах — время жизни черновика (согласно cancel_stale_drafts). */
export const DRAFT_TTL_MS = 24 * 60 * 60 * 1000;

/**
 * Рассчитывает момент истечения срока черновика.
 * @param createdAtIso — ISO-строка created_at из TaskQueueRow / TmaDraftOut.
 * @returns Date момента истечения (created_at + 24h).
 */
export function draftExpiresAt(createdAtIso: string | null | undefined): Date {
  if (!createdAtIso) return new Date(Date.now() + DRAFT_TTL_MS);
  const created = new Date(createdAtIso);
  return new Date(created.getTime() + DRAFT_TTL_MS);
}

/**
 * Проверяет, истекает ли черновик в ближайшее время.
 * @param expiresAt — момент истечения (из draftExpiresAt).
 * @param now — текущее время (для тестируемости; default = Date.now()).
 * @param thresholdMs — порог предупреждения в мс (default = 1 час).
 * @returns true, если до истечения осталось меньше thresholdMs.
 */
export function isExpiringSoon(
  expiresAt: Date,
  now: number = Date.now(),
  thresholdMs: number = 60 * 60 * 1000,
): boolean {
  return expiresAt.getTime() - now < thresholdMs;
}

/**
 * Проверяет, истёк ли черновик.
 * @param expiresAt — момент истечения.
 * @param now — текущее время (default = Date.now()).
 */
export function isDraftExpired(expiresAt: Date, now: number = Date.now()): boolean {
  return expiresAt.getTime() <= now;
}
