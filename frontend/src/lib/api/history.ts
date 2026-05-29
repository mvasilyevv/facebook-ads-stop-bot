/**
 * Hooks для /history-страницы.
 */

import { useQuery } from "@tanstack/react-query";
import { apiClient } from "./client";
import type { AlertEvent, HistorySummary, TaskQueueRow } from "@/lib/types/api";

const KEYS = {
  summary: (params?: Record<string, unknown>) => ["history", "summary", params] as const,
  timeline: (params?: Record<string, unknown>) => ["history", "timeline", params] as const,
  campaigns: (params?: Record<string, unknown>) => ["history", "campaigns", params] as const,
  events: (params?: Record<string, unknown>) => ["history", "events", params] as const,
};

interface DateRangeParams {
  from_iso?: string;
  to_iso?: string;
  [key: string]: string | number | boolean | null | undefined;
}

interface TimelineParams extends DateRangeParams {
  limit?: number;
}

interface EventsParams extends DateRangeParams {
  campaign_id?: string;
  fb_ad_id?: string;
  stage?: string;
}

export function useHistorySummary(params: DateRangeParams = {}) {
  return useQuery({
    queryKey: KEYS.summary(params),
    queryFn: () => apiClient.get<HistorySummary>("/history/summary", params),
  });
}

export function useHistoryTimeline(params: TimelineParams = {}) {
  return useQuery({
    queryKey: KEYS.timeline(params),
    queryFn: () =>
      apiClient.get<Array<AlertEvent | TaskQueueRow>>("/history/timeline", params),
  });
}

export function useHistoryEvents(params: EventsParams = {}) {
  return useQuery({
    queryKey: KEYS.events(params),
    queryFn: () => apiClient.get<AlertEvent[]>("/history/events", params),
  });
}

export const historyKeys = KEYS;
