import type { AnalyticsPerformanceRow } from "../api/types";
import { formatSpend } from "../format/number";
import type { AnalyticsPreset, AnalyticsSort } from "./routeState";

export type AnalyticsMetricTone = "danger" | "success" | "accent";
export interface AnalyticsMetricView {
  key: string;
  label: string;
  value: string;
  tone?: AnalyticsMetricTone;
}

export interface AnalyticsColumnView {
  key: string;
  label: string;
  sort?: AnalyticsSort;
}

export const ANALYTICS_PRESETS: ReadonlyArray<{
  value: AnalyticsPreset;
  label: string;
}> = [
  { value: "economy", label: "Экономика" },
  { value: "funnel", label: "Воронка" },
  { value: "delivery", label: "Доставка" },
];

/** Six metrics plus the object column form the fixed seven-column desktop table. */
export function analyticsColumnsForPreset(
  preset: AnalyticsPreset,
): AnalyticsColumnView[] {
  if (preset === "funnel") {
    return [
      { key: "clicks", label: "Клики", sort: "clicks" },
      { key: "registrations", label: "Рег.", sort: "registrations" },
      { key: "ftds", label: "FTD", sort: "ftds" },
      { key: "deposits", label: "Деп.", sort: "confirmed_deposits" },
      { key: "click-registration", label: "Click→Reg" },
      { key: "registration-ftd", label: "Reg→FTD" },
    ];
  }
  if (preset === "delivery") {
    return [
      { key: "impressions", label: "Показы" },
      { key: "clicks", label: "Клики", sort: "clicks" },
      { key: "cpc", label: "CPC" },
      { key: "ctr", label: "CTR" },
      { key: "spend", label: "Расход", sort: "spend" },
      { key: "base", label: "База" },
    ];
  }
  return [
    { key: "spend", label: "Расход", sort: "spend" },
    { key: "revenue", label: "Выручка", sort: "revenue" },
    { key: "cost-registration", label: "Цена рег." },
    { key: "cost-ftd", label: "Цена FTD" },
    { key: "roi", label: "ROI" },
    { key: "base-delta", label: "Δ базы", sort: "base_delta" },
  ];
}

/** Shared row projection: desktop renders cells, mobile/TMA render ledger cards. */
export function analyticsMetricsForRow(
  row: AnalyticsPerformanceRow,
  preset: AnalyticsPreset,
  currency: string | null,
): AnalyticsMetricView[] {
  const budget = row.live_budget;
  const delta = decimal(budget?.base_delta);
  const roi = decimal(row.roi_pct);
  if (preset === "funnel") {
    return [
      { key: "clicks", label: "Клики", value: integer(row.clicks) },
      {
        key: "registrations",
        label: "Регистрации",
        value: integer(row.registrations),
        tone: row.registrations === null ? undefined : "accent",
      },
      {
        key: "ftds",
        label: "FTD",
        value: integer(row.ftds),
        tone: row.ftds === null ? undefined : "accent",
      },
      {
        key: "deposits",
        label: "Депозиты",
        value: integer(row.confirmed_deposits),
        tone: row.confirmed_deposits === null ? undefined : "accent",
      },
      {
        key: "click-registration",
        label: "Click→Reg",
        value: percent(row.click_registration_cr_pct),
      },
      {
        key: "registration-ftd",
        label: "Reg→FTD",
        value: percent(row.registration_ftd_cr_pct),
      },
    ];
  }
  if (preset === "delivery") {
    return [
      { key: "impressions", label: "Показы", value: integer(row.impressions) },
      { key: "clicks", label: "Клики", value: integer(row.clicks) },
      { key: "cpc", label: "CPC", value: formatSpend(row.cpc, currency) },
      { key: "ctr", label: "CTR", value: percent(row.ctr_pct) },
      {
        key: "spend",
        label: "Расход",
        value: formatSpend(row.spend, currency),
      },
      {
        key: "base",
        label: "База",
        value: formatSpend(budget?.base_budget, currency),
      },
    ];
  }
  return [
    { key: "spend", label: "Расход", value: formatSpend(row.spend, currency) },
    {
      key: "revenue",
      label: "Выручка",
      value: formatSpend(row.revenue, currency),
    },
    {
      key: "cost-registration",
      label: "Цена регистрации",
      value: formatSpend(row.cost_per_registration, currency),
    },
    {
      key: "cost-ftd",
      label: "Цена FTD",
      value: formatSpend(row.cost_per_ftd, currency),
    },
    {
      key: "roi",
      label: "ROI",
      value: percent(row.roi_pct, true),
      tone: roi === null ? undefined : roi < 0 ? "danger" : "success",
    },
    {
      key: "base-delta",
      label: "Δ базы",
      value:
        delta === null
          ? "—"
          : signedMoney(budget?.base_delta ?? null, currency),
      tone: delta === null ? undefined : delta > 0 ? "danger" : "success",
    },
  ];
}

function decimal(value?: string | null): number | null {
  const parsed = value == null ? null : Number(value);
  return parsed === null || !Number.isFinite(parsed) ? null : parsed;
}

function signedMoney(value: string | null, currency: string | null): string {
  const parsed = decimal(value);
  const formatted = formatSpend(value, currency);
  return parsed === null || formatted === "—"
    ? "—"
    : `${parsed > 0 ? "+" : ""}${formatted}`;
}

function integer(value: number | null | undefined): string {
  return value == null ? "—" : value.toLocaleString("ru-RU");
}

function percent(value?: string | null, signed = false): string {
  const parsed = decimal(value);
  return parsed === null
    ? "—"
    : `${signed && parsed > 0 ? "+" : ""}${parsed.toFixed(2)}%`;
}
