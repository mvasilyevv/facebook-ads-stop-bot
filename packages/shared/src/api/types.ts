/** Stable ergonomic aliases over the generated OpenAPI contract. */

import type { components } from "./generated";

export type Offer = components["schemas"]["OfferOut"];
export type OfferRules = components["schemas"]["OfferRuleOut"];

export type ObserverConfig = components["schemas"]["ObserverSettingsResponse"];
export type TelegramSettings = components["schemas"]["TelegramSettingsResponse"];
export type TelegramNotificationDiagnostics =
  components["schemas"]["TelegramNotificationDiagnosticsResponse"];

type GeneratedAnalyticsMetrics = components["schemas"]["AnalyticsMetricsOut"];
type AnalyticsMetricKey =
  | "spend"
  | "impressions"
  | "clicks"
  | "leads"
  | "registrations"
  | "ftds"
  | "confirmed_deposits"
  | "redeposits"
  | "revenue";

export type AnalyticsMetrics = Omit<GeneratedAnalyticsMetrics, AnalyticsMetricKey> & {
  spend: string | null;
  impressions: number | null;
  clicks: number | null;
  leads: number | null;
  registrations: number | null;
  ftds: number | null;
  confirmed_deposits: number | null;
  redeposits: number | null;
  revenue: string | null;
};

type AnalyticsSectionState = {
  state: components["schemas"]["DataState"];
  as_of: string | null;
  freshness_seconds: number | null;
  issues: string[];
};

type GeneratedAnalyticsPerformanceRow =
  components["schemas"]["AnalyticsPerformanceRowOut"];
export type AnalyticsPerformanceRow = Omit<
  GeneratedAnalyticsPerformanceRow,
  AnalyticsMetricKey
> &
  AnalyticsMetrics & {
    state: components["schemas"]["DataState"];
    issues: string[];
  };

type GeneratedAnalyticsPerformance =
  components["schemas"]["AnalyticsPerformanceOut"];
export type AnalyticsPerformance = Omit<
  GeneratedAnalyticsPerformance,
  "totals" | "rows"
> &
  AnalyticsSectionState & {
    totals: AnalyticsMetrics;
    rows: AnalyticsPerformanceRow[];
  };
export type AnalyticsLiveBudget = components["schemas"]["AnalyticsLiveBudgetOut"];
type GeneratedBudgetPoint = components["schemas"]["AnalyticsBudgetPointOut"];
export type AnalyticsLiveBudgetSeries = Omit<
  components["schemas"]["AnalyticsLiveBudgetSeriesOut"],
  "points"
> &
  AnalyticsSectionState & {
    sources: components["schemas"]["AnalyticsSourcesOut"];
    points: Array<
      Omit<GeneratedBudgetPoint, "actual" | "base" | "stop"> & {
        actual: string | null;
        base: string | null;
        stop: string | null;
      }
    >;
  };

type GeneratedDaypartCell = components["schemas"]["AnalyticsDaypartCellOut"];
export type AnalyticsDaypart = Omit<
  components["schemas"]["AnalyticsDaypartOut"],
  "cells"
> &
  AnalyticsSectionState & {
    sources: components["schemas"]["AnalyticsSourcesOut"];
    cells: Array<
      Omit<GeneratedDaypartCell, "clicks" | "registrations" | "ftds"> & {
        clicks: number | null;
        registrations: number | null;
        ftds: number | null;
      }
    >;
  };
