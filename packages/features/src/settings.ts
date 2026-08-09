import type { components } from "@fb/shared/api/generated";

export const OBSERVER_INTERVAL_MIN_SECONDS = 30;
export const OBSERVER_INTERVAL_MAX_SECONDS = 600;

export const TELEGRAM_SEVERITY_OPTIONS = [
  { value: "warning", label: "Warning и critical" },
  { value: "critical", label: "Только critical" },
  { value: "unknown", label: "Unknown, warning и critical" },
  { value: "ok", label: "Все события" },
] as const;

export const TELEGRAM_CATEGORY_OPTIONS = [
  { value: "inherit", label: "По общему порогу" },
  { value: "off", label: "Выключено" },
  { value: "warning", label: "Warning и critical" },
  { value: "critical", label: "Только critical" },
] as const;

export const TELEGRAM_CATEGORIES = [
  { key: "incidents", label: "Инциденты" },
  { key: "actions", label: "Действия" },
  { key: "recommendations", label: "Рекомендации" },
  { key: "digest", label: "Дайджест" },
] as const;

type TelegramPreferenceRequest =
  components["schemas"]["TelegramRecipientPreferenceRequest"];
type TelegramPreferenceResponse =
  components["schemas"]["TelegramRecipientPreferenceResponse"];

export interface TelegramPreferenceDraft {
  timezone: string;
  minSeverity: TelegramPreferenceRequest["min_severity"];
  quietHoursStart: string;
  quietHoursEnd: string;
  digestLocalTime: string;
  categories: NonNullable<TelegramPreferenceRequest["categories"]>;
  isEnabled: boolean;
}

export interface TelegramPreferenceValidation {
  timezone?: string;
  quietHours?: string;
  digestLocalTime?: string;
}

export function validateObserverInterval(
  value: string | number,
): string | null {
  const parsed = typeof value === "number" ? value : Number(value.trim());
  if (!Number.isInteger(parsed)) return "Введите целое число секунд";
  if (
    parsed < OBSERVER_INTERVAL_MIN_SECONDS ||
    parsed > OBSERVER_INTERVAL_MAX_SECONDS
  ) {
    return `Интервал должен быть от ${OBSERVER_INTERVAL_MIN_SECONDS} до ${OBSERVER_INTERVAL_MAX_SECONDS} секунд`;
  }
  return null;
}

export function normalizeOwnerCampaignTags(value: string): string | null {
  const tags: string[] = [];
  const seen = new Set<string>();
  for (const rawTag of value.split(",")) {
    const tag = rawTag.trim();
    if (!tag) continue;
    const key = tag.toLocaleLowerCase("ru");
    if (seen.has(key)) continue;
    seen.add(key);
    tags.push(tag);
  }
  return tags.length ? tags.join(",") : null;
}

export function isValidIanaTimeZone(value: string): boolean {
  const timezone = value.trim();
  if (!timezone) return false;
  try {
    new Intl.DateTimeFormat("ru-RU", { timeZone: timezone }).format();
    return true;
  } catch {
    return false;
  }
}

export function telegramPreferenceDraftFromResponse(
  value: TelegramPreferenceResponse,
): TelegramPreferenceDraft {
  return {
    timezone: value.timezone,
    minSeverity: value.min_severity,
    quietHoursStart: value.quiet_hours_start ?? "",
    quietHoursEnd: value.quiet_hours_end ?? "",
    digestLocalTime: value.digest_local_time ?? "",
    categories: { ...(value.categories ?? {}) },
    isEnabled: value.is_enabled,
  };
}

export function validateTelegramPreferenceDraft(
  value: TelegramPreferenceDraft,
): TelegramPreferenceValidation {
  const errors: TelegramPreferenceValidation = {};
  if (!isValidIanaTimeZone(value.timezone)) {
    errors.timezone = "Укажите корректный IANA timezone";
  }
  const hasQuietStart = value.quietHoursStart.length > 0;
  const hasQuietEnd = value.quietHoursEnd.length > 0;
  if (hasQuietStart !== hasQuietEnd) {
    errors.quietHours = "Для тихих часов нужны время начала и окончания";
  } else if (
    (hasQuietStart && !isTimeOfDay(value.quietHoursStart)) ||
    (hasQuietEnd && !isTimeOfDay(value.quietHoursEnd))
  ) {
    errors.quietHours = "Используйте время в формате ЧЧ:ММ";
  }
  if (value.digestLocalTime && !isTimeOfDay(value.digestLocalTime)) {
    errors.digestLocalTime = "Используйте время в формате ЧЧ:ММ";
  }
  return errors;
}

export function telegramPreferencePayload(
  value: TelegramPreferenceDraft,
): TelegramPreferenceRequest {
  const errors = validateTelegramPreferenceDraft(value);
  if (Object.keys(errors).length) {
    throw new Error("Проверьте настройки получателя");
  }
  return {
    timezone: value.timezone.trim(),
    min_severity: value.minSeverity,
    quiet_hours_start: value.quietHoursStart || null,
    quiet_hours_end: value.quietHoursEnd || null,
    digest_local_time: value.digestLocalTime || null,
    categories: { ...value.categories },
    is_enabled: value.isEnabled,
  };
}

export function isSafeTelegramWebAppUrl(value: string): boolean {
  const trimmed = value.trim();
  if (!trimmed) return true;
  try {
    const url = new URL(trimmed);
    return url.protocol === "https:" && !url.username && !url.password;
  } catch {
    return false;
  }
}

function isTimeOfDay(value: string): boolean {
  return /^(?:[01]\d|2[0-3]):[0-5]\d$/.test(value);
}
