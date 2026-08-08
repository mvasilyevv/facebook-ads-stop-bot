import { keepPreviousData } from "@tanstack/react-query";
import type { operations } from "@fb/shared/api/generated";
import {
  normalizeAnalyticsDaypart,
  normalizeAnalyticsLiveBudgetSeries,
  normalizeAnalyticsPerformance,
} from "@fb/shared/analytics/runtime";

import { tmaApi } from "@/lib/auth";

type PerformanceQuery = NonNullable<
  operations["get_analytics_performance_api_analytics_performance_get"]["parameters"]["query"]
>;
type LiveBudgetQuery = NonNullable<
  operations["get_analytics_live_budget_api_analytics_live_budget_get"]["parameters"]["query"]
>;
type DaypartQuery = NonNullable<
  operations["get_analytics_daypart_api_analytics_daypart_get"]["parameters"]["query"]
>;
type OperatorEventsQuery = NonNullable<
  operations["get_operator_events_api_operator_events_get"]["parameters"]["query"]
>;

export type TmaAnalyticsPerformanceParams = PerformanceQuery &
  Required<Pick<PerformanceQuery, "period" | "level">>;

/**
 * TMA analytics uses the same generated OpenAPI path and runtime payload guard
 * as the desktop surface. Placeholder rows stay visible only as stale context.
 */
export function useTmaAnalyticsPerformance(
  params: TmaAnalyticsPerformanceParams,
  enabled = true,
) {
  return tmaApi.useQuery(
    "get",
    "/api/analytics/performance",
    { params: { query: params } },
    {
      enabled,
      placeholderData: keepPreviousData,
      select: normalizeAnalyticsPerformance,
      staleTime: params.period === "today" ? 20_000 : 60_000,
    },
  );
}

/** Typed current-cabinet-day budget series, available to compact TMA views. */
export function useTmaAnalyticsLiveBudget(
  params: LiveBudgetQuery,
  enabled = true,
) {
  return tmaApi.useQuery(
    "get",
    "/api/analytics/live-budget",
    { params: { query: params } },
    {
      enabled,
      select: normalizeAnalyticsLiveBudgetSeries,
      staleTime: 20_000,
    },
  );
}

/** Sparse weekday × hour data; missing cells remain unknown after validation. */
export function useTmaAnalyticsDaypart(
  params: DaypartQuery,
  enabled = true,
) {
  return tmaApi.useQuery(
    "get",
    "/api/analytics/daypart",
    { params: { query: params } },
    {
      enabled,
      placeholderData: keepPreviousData,
      select: normalizeAnalyticsDaypart,
      staleTime: 60_000,
    },
  );
}

/** Typed immutable alert/action feed for the compact analytics surface. */
export function useTmaAnalyticsEvents(
  params: OperatorEventsQuery,
  enabled = true,
) {
  return tmaApi.useQuery(
    "get",
    "/api/operator/events",
    { params: { query: params } },
    {
      enabled,
      staleTime: 30_000,
    },
  );
}
