export interface AnalyticsRouteSearch {
  tab: "uploads" | "events";
  period: "today" | "7d" | "30d" | "custom";
  from_iso: string | undefined;
  to_iso: string | undefined;
  account_id: string | undefined;
  offer_id: string | undefined;
  campaign_id: string | undefined;
  search: string | undefined;
  sort: string;
  direction: "asc" | "desc";
  page: number;
  event_level: string | undefined;
  task_result: string | undefined;
}

export function analyticsRouteSearch(
  overrides: Partial<AnalyticsRouteSearch> = {},
): AnalyticsRouteSearch {
  return {
    tab: "uploads",
    period: "today",
    from_iso: undefined,
    to_iso: undefined,
    account_id: undefined,
    offer_id: undefined,
    campaign_id: undefined,
    search: undefined,
    sort: "spend",
    direction: "desc",
    page: 1,
    event_level: undefined,
    task_result: undefined,
    ...overrides,
  };
}

export function analyticsHref(overrides: Partial<AnalyticsRouteSearch> = {}): string {
  const params = new URLSearchParams();
  for (const [key, value] of Object.entries(analyticsRouteSearch(overrides))) {
    if (value !== undefined && value !== null && value !== "") {
      params.set(key, String(value));
    }
  }
  return `/analytics?${params.toString()}`;
}
