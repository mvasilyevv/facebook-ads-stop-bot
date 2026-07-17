import { keepPreviousData, useQuery } from "@tanstack/react-query";
import type { AnalyticsDaypart, AnalyticsLiveBudgetSeries, AnalyticsPerformance } from "@fb/shared";

import { apiGet } from "./client";

export interface AnalyticsFilters {
  period: "today" | "custom";
  from_iso?: string;
  to_iso?: string;
  account_id?: string;
  offer_id?: string;
  campaign_id?: string;
  search?: string;
}

export interface AnalyticsPerformanceParams extends AnalyticsFilters {
  level: "campaign" | "adset" | "ad";
  parent_id?: string;
  sort?:
    | "name"
    | "spend"
    | "clicks"
    | "registrations"
    | "ftds"
    | "confirmed_deposits"
    | "revenue"
    | "base_delta";
  direction?: "asc" | "desc";
  page?: number;
  page_size?: number;
}

export function useAnalyticsPerformance(params: AnalyticsPerformanceParams, enabled = true) {
  return useQuery<AnalyticsPerformance>({
    queryKey: ["analytics", "performance", params],
    queryFn: ({ signal }) =>
      apiGet<AnalyticsPerformance>(
        "/analytics/performance",
        params as unknown as Record<string, string | number | boolean | null | undefined>,
        signal,
      ),
    staleTime: params.period === "today" ? 20_000 : 60_000,
    placeholderData: keepPreviousData,
    enabled,
  });
}

export function useAnalyticsLiveBudget(
  params: Pick<AnalyticsFilters, "account_id" | "offer_id" | "campaign_id">,
  enabled = true,
) {
  return useQuery<AnalyticsLiveBudgetSeries>({
    queryKey: ["analytics", "live-budget", params],
    queryFn: ({ signal }) =>
      apiGet<AnalyticsLiveBudgetSeries>(
        "/analytics/live-budget",
        params as Record<string, string | number | boolean | null | undefined>,
        signal,
      ),
    staleTime: 20_000,
    enabled,
  });
}

export function useAnalyticsDaypart(
  params: Omit<AnalyticsFilters, "period" | "search"> & { timezone: string },
  enabled = true,
) {
  return useQuery<AnalyticsDaypart>({
    queryKey: ["analytics", "daypart", params],
    queryFn: ({ signal }) =>
      apiGet<AnalyticsDaypart>(
        "/analytics/daypart",
        params as Record<string, string | number | boolean | null | undefined>,
        signal,
      ),
    staleTime: 60_000,
    placeholderData: keepPreviousData,
    enabled,
  });
}
