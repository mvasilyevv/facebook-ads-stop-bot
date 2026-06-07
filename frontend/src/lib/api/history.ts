/**
 * API-хуки для History-страницы.
 *
 * Эндпоинты:
 *   GET /api/history/summary    → HistorySummary
 *   GET /api/history/timeline   → HistoryTimelineItem[]
 *   GET /api/history/campaigns  → HistoryCampaign[]
 *   GET /api/history/events     → HistoryEvent[]
 *   GET /api/history/offers     → HistoryOffer[]
 *   GET /api/history/ads        → HistoryAd[]
 */

import { useQuery } from "@tanstack/react-query";
import { apiGet } from "./client";
import type {
  HistoryAd,
  HistoryCampaign,
  HistoryEvent,
  HistoryOffer,
  HistorySummary,
  HistoryTimelineItem,
} from "@fb/shared";

interface HistoryParams {
  from_iso?: string;
  to_iso?: string;
}

// ─── Сводка ───────────────────────────────────────────────────────────────────

export function useHistorySummary(params?: HistoryParams) {
  return useQuery<HistorySummary>({
    queryKey: ["history", "summary", params],
    queryFn: ({ signal }) =>
      apiGet<HistorySummary>("/history/summary", params as Record<string, string | number | boolean | null | undefined>, signal),
    staleTime: 60_000,
  });
}

// ─── Timeline ─────────────────────────────────────────────────────────────────

interface TimelineParams extends HistoryParams {
  limit?: number;
  offset?: number;
}

export function useHistoryTimeline(params?: TimelineParams) {
  return useQuery<HistoryTimelineItem[]>({
    queryKey: ["history", "timeline", params],
    queryFn: ({ signal }) =>
      apiGet<HistoryTimelineItem[]>("/history/timeline", params as Record<string, string | number | boolean | null | undefined>, signal),
    staleTime: 30_000,
  });
}

// ─── Кампании ─────────────────────────────────────────────────────────────────

export function useHistoryCampaigns(params?: HistoryParams) {
  return useQuery<HistoryCampaign[]>({
    queryKey: ["history", "campaigns", params],
    queryFn: ({ signal }) =>
      apiGet<HistoryCampaign[]>("/history/campaigns", params as Record<string, string | number | boolean | null | undefined>, signal),
    staleTime: 60_000,
  });
}

// ─── События алертов (drill-down) ─────────────────────────────────────────────

interface EventsParams extends HistoryParams {
  campaign_id?: string;
  fb_ad_id?: string;
  stage?: string;
}

export function useHistoryEvents(params?: EventsParams) {
  return useQuery<HistoryEvent[]>({
    queryKey: ["history", "events", params],
    queryFn: ({ signal }) =>
      apiGet<HistoryEvent[]>("/history/events", params as Record<string, string | number | boolean | null | undefined>, signal),
    staleTime: 30_000,
  });
}

// ─── Офферы в истории ─────────────────────────────────────────────────────────

export function useHistoryOffers(params?: HistoryParams) {
  return useQuery<HistoryOffer[]>({
    queryKey: ["history", "offers", params],
    queryFn: ({ signal }) =>
      apiGet<HistoryOffer[]>("/history/offers", params as Record<string, string | number | boolean | null | undefined>, signal),
    staleTime: 60_000,
  });
}

// ─── Объявления в истории ─────────────────────────────────────────────────────

interface AdsHistoryParams extends HistoryParams {
  campaign_id?: string;
  offer_id?: string;
}

export function useHistoryAds(params?: AdsHistoryParams) {
  return useQuery<HistoryAd[]>({
    queryKey: ["history", "ads", params],
    queryFn: ({ signal }) =>
      apiGet<HistoryAd[]>("/history/ads", params as Record<string, string | number | boolean | null | undefined>, signal),
    staleTime: 60_000,
  });
}
