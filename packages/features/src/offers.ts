import type { components } from "@fb/shared/api/generated";

export type OfferRulesDraft = Partial<components["schemas"]["OfferRuleOut"]>;
type OfferRulesOut = components["schemas"]["OfferRuleOut"];

export interface OfferRulesValues {
  cpa: string;
  currency: string;
  stop_percent_of_rule: number;
  warning_percent_of_stop: number;
}

export const DEFAULT_OFFER_RULES_VALUES: Readonly<OfferRulesValues> = {
  cpa: "",
  currency: "USD",
  stop_percent_of_rule: 80,
  warning_percent_of_stop: 80,
};

/** Точность доллара. Колонка `offer_rules.cpa_threshold` — numeric(20,6). */
const CURRENCY_FRACTION_DIGITS = 2;

/**
 * CPA в точности валюты, а не в точности колонки БД.
 *
 * Postgres отдаёт `3.000000`, и оператор видел шесть знаков там, где у доллара
 * их два. Пустое значение остаётся пустым: `null` означает «ставка не задана»,
 * а `0.00` означало бы подтверждённый ноль.
 */
function cpaInCurrencyPrecision(raw: string | null | undefined): string {
  if (raw == null || raw === "") return "";
  const parsed = Number(raw);
  return Number.isFinite(parsed) ? parsed.toFixed(CURRENCY_FRACTION_DIGITS) : raw;
}

export function rulesValuesFromOut(
  rules: OfferRulesOut | null | undefined,
): OfferRulesValues {
  if (!rules) return { ...DEFAULT_OFFER_RULES_VALUES };
  return {
    cpa: cpaInCurrencyPrecision(rules.cpa_threshold),
    currency: rules.currency ?? "USD",
    stop_percent_of_rule: validPercentOrDefault(rules.stop_percent_of_rule),
    warning_percent_of_stop: validPercentOrDefault(
      rules.warning_percent_of_stop,
    ),
  };
}

export function rulesValuesToPayload(
  values: OfferRulesValues,
  frequencyThreshold?: string | null,
): OfferRulesDraft {
  const cpa = values.cpa.trim();
  const currency = values.currency.trim().toUpperCase();
  const frequency = frequencyThreshold?.trim();
  if (cpa && !isOfferCpaValid(cpa)) {
    throw new Error("CPA должен быть положительной десятичной строкой");
  }
  if (cpa && !isOfferCurrencyValid(currency)) {
    throw new Error("FB Agent поддерживает CPA только в USD");
  }
  return {
    cpa_threshold: cpa || null,
    currency: cpa ? currency : null,
    frequency_threshold:
      frequency === undefined ? undefined : frequency || null,
    stop_percent_of_rule: requiredPercent(values.stop_percent_of_rule),
    warning_percent_of_stop: requiredPercent(values.warning_percent_of_stop),
  };
}

export function isOfferCpaValid(value: string): boolean {
  const normalized = value.trim();
  if (!/^(?:0|[1-9][0-9]*)(?:\.[0-9]+)?$/.test(normalized)) {
    return false;
  }
  return !/^0(?:\.0+)?$/.test(normalized);
}

export function isOfferCurrencyValid(value: string): boolean {
  return value.trim().toUpperCase() === "USD";
}

export function parseOfferAccountIds(raw: string): string[] | null {
  return parseUniqueTokens(raw, (token) => {
    const normalized = token.replace(/^act_/i, "");
    return /^\d+$/.test(normalized) ? normalized : null;
  });
}

export function parseOfferCountries(raw: string): string[] | null {
  return parseUniqueTokens(raw, (token) => {
    const normalized = token.toUpperCase();
    return /^[A-Z]{2}$/.test(normalized) ? normalized : null;
  });
}

export function buildOfferRulesBody(
  data: OfferRulesDraft,
): components["schemas"]["OfferRuleUpsertIn"] {
  if (
    data.stop_percent_of_rule == null ||
    data.warning_percent_of_stop == null
  ) {
    throw new Error("Не заданы обязательные проценты правила оффера");
  }
  return {
    cpa_threshold: data.cpa_threshold,
    currency: data.currency,
    frequency_threshold: data.frequency_threshold,
    stop_percent_of_rule: data.stop_percent_of_rule,
    warning_percent_of_stop: data.warning_percent_of_stop,
  };
}

function validPercentOrDefault(value: string | null | undefined): number {
  const parsed = Number(value);
  return Number.isFinite(parsed) && parsed >= 1 && parsed <= 100 ? parsed : 80;
}

function requiredPercent(value: number): string {
  if (!Number.isFinite(value) || value < 1 || value > 100) {
    throw new Error("Процент правила оффера должен быть от 1 до 100");
  }
  return String(value);
}

function parseUniqueTokens(
  raw: string,
  normalize: (token: string) => string | null,
): string[] | null {
  const values: string[] = [];
  const seen = new Set<string>();
  for (const part of raw.split(/[\s,;]+/)) {
    const token = part.trim();
    if (!token) continue;
    const normalized = normalize(token);
    if (normalized === null) return null;
    if (!seen.has(normalized)) {
      seen.add(normalized);
      values.push(normalized);
    }
  }
  return values;
}
