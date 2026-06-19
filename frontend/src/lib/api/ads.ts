/**
 * API-хуки для Ads-страницы.
 *
 * Эндпоинты:
 *   GET  /api/dashboard/ads                         → AdSnapshot[] + X-Total-Count
 *   GET  /api/ads/{fb_ad_id}/timeline               → AdTimeline
 *   POST /api/dashboard/disable-tasks               → TaskQueueRow
 *   POST /api/dashboard/disable-tasks/bulk          → BulkDisableResult
 *   GET  /api/dashboard/disable-tasks               → TaskQueueRow[]
 *   GET  /api/dashboard/enable-tasks                → EnableTaskRow[]
 */

import { useQuery, useMutation, useQueryClient } from "@tanstack/react-query";
import { apiGet, apiGetWithCount, apiSend } from "./client";
import type { AdSnapshot, AdTimeline, TaskQueueRow } from "@fb/shared";
import type { components } from "@fb/shared/api/generated";

type EnableTaskRow = components["schemas"]["EnableTaskRowOut"];
type BulkDisableResult = components["schemas"]["BulkDisableResultOut"];

// ─── Ads list (общий для Dashboard + AdsPage) ─────────────────────────────────

interface AdsParams {
  /** M1: имя совпадает с бэк-параметром /dashboard/ads (CSV alert_state'ов). */
  alert_state?: string;
  limit?: number;
  offset?: number;
  include_inactive?: boolean;
}

export function useAds(params?: AdsParams) {
  return useQuery<{ data: AdSnapshot[]; total: number | null }>({
    queryKey: ["ads", params],
    queryFn: ({ signal }) =>
      apiGetWithCount<AdSnapshot[]>("/dashboard/ads", params as Record<string, string | number | boolean | null | undefined>, signal),
    staleTime: 10_000,
  });
}

// ─── Ad timeline ──────────────────────────────────────────────────────────────

interface TimelineParams {
  from_iso?: string;
  to_iso?: string;
  include_metrics?: boolean;
  include_alerts?: boolean;
  include_tasks?: boolean;
}

export function useAdTimeline(fbAdId: string, params?: TimelineParams) {
  return useQuery<AdTimeline>({
    queryKey: ["ads", fbAdId, "timeline", params],
    queryFn: ({ signal }) =>
      apiGet<AdTimeline>(`/ads/${fbAdId}/timeline`, params as Record<string, string | number | boolean | null | undefined>, signal),
    enabled: !!fbAdId,
    staleTime: 15_000,
  });
}

// ─── Disable tasks ────────────────────────────────────────────────────────────

interface DisableTasksParams {
  status?: string;
  fb_ad_id?: string;
  limit?: number;
  offset?: number;
}

export function useDisableTasks(params?: DisableTasksParams) {
  return useQuery<TaskQueueRow[]>({
    queryKey: ["tasks", "disable", params],
    queryFn: ({ signal }) =>
      apiGet<TaskQueueRow[]>("/dashboard/disable-tasks", params as Record<string, string | number | boolean | null | undefined>, signal),
    staleTime: 10_000,
  });
}

// ─── Enable tasks ─────────────────────────────────────────────────────────────

interface EnableTasksParams {
  status?: string;
  fb_ad_id?: string;
  limit?: number;
  offset?: number;
}

export function useEnableTasks(params?: EnableTasksParams) {
  return useQuery<EnableTaskRow[]>({
    queryKey: ["tasks", "enable", params],
    queryFn: ({ signal }) =>
      apiGet<EnableTaskRow[]>("/dashboard/enable-tasks", params as Record<string, string | number | boolean | null | undefined>, signal),
    staleTime: 10_000,
  });
}

// ─── Bulk disable ─────────────────────────────────────────────────────────────

interface BulkDisableIn {
  fb_ad_ids: string[];
  /** Client-side токен против двойного submit (общий для всего batch). Обязателен (min_length=1). */
  idempotency_token: string;
  reason?: string;
}

export function useBulkDisable() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (body: BulkDisableIn) =>
      apiSend<BulkDisableResult>("POST", "/dashboard/disable-tasks/bulk", body),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ["tasks", "disable"] });
      qc.invalidateQueries({ queryKey: ["dashboard"] });
      qc.invalidateQueries({ queryKey: ["ads"] });
    },
  });
}

// ─── Hard-delete объявлений из каталога (необратимо) ──────────────────────────

export interface BulkDeleteAdsResult {
  deleted: string[];
  count: number;
}

/** Hard-delete выбранных объявлений из fb_ads (POST /dashboard/ads/bulk-delete). */
export function useDeleteAds() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (fb_ad_ids: string[]) =>
      apiSend<BulkDeleteAdsResult>("POST", "/dashboard/ads/bulk-delete", { fb_ad_ids }),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ["ads"] });
      qc.invalidateQueries({ queryKey: ["dashboard"] });
    },
  });
}

