import { keepPreviousData } from "@tanstack/react-query";
import type { operations } from "@fb/shared/api/generated";
import {
  normalizeAnalyticsDaypart,
  normalizeAnalyticsLiveBudgetSeries,
  normalizeAnalyticsPerformance,
} from "@fb/shared/analytics/runtime";

import { generatedApi } from "./generatedClient";

type PerformanceQuery = NonNullable<
  operations["get_analytics_performance_api_analytics_performance_get"]["parameters"]["query"]
>;
type LiveBudgetQuery = NonNullable<
  operations["get_analytics_live_budget_api_analytics_live_budget_get"]["parameters"]["query"]
>;
type DaypartQuery = NonNullable<
  operations["get_analytics_daypart_api_analytics_daypart_get"]["parameters"]["query"]
>;

export type AnalyticsPerformanceParams = PerformanceQuery &
  Required<Pick<PerformanceQuery, "period" | "level">>;

export function useAnalyticsPerformance(params: AnalyticsPerformanceParams, enabled = true) {
  return generatedApi.useQuery(
    "get",
    "/api/analytics/performance",
    { params: { query: params } },
    {
      staleTime: params.period === "today" ? 20_000 : 60_000,
      placeholderData: keepPreviousData,
      select: normalizeAnalyticsPerformance,
      enabled,
    },
  );
}

export function useAnalyticsLiveBudget(params: LiveBudgetQuery, enabled = true) {
  return generatedApi.useQuery(
    "get",
    "/api/analytics/live-budget",
    { params: { query: params } },
    { staleTime: 20_000, select: normalizeAnalyticsLiveBudgetSeries, enabled },
  );
}

export function useAnalyticsDaypart(
  params: DaypartQuery,
  enabled = true,
) {
  return generatedApi.useQuery(
    "get",
    "/api/analytics/daypart",
    { params: { query: params } },
    {
      staleTime: 60_000,
      placeholderData: keepPreviousData,
      select: normalizeAnalyticsDaypart,
      enabled,
    },
  );
}
