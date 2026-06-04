/**
 * Hooks для /ads-страницы.
 * Используются: AdsPage, AdDrawer, BulkActionBar.
 */

import { useQuery, useMutation, useQueryClient } from "@tanstack/react-query";
import { apiClient, apiGetWithCount } from "./client";
import type { AdSnapshot, AlertEvent, TaskQueueRow } from "@/lib/types/api";

const KEYS = {
  ads: (params?: Record<string, unknown>) => ["ads", params] as const,
  timeline: (fb_ad_id: string) => ["ads", "timeline", fb_ad_id] as const,
};

/** Результат useAds: страница ads + общее число (из X-Total-Count) для пагинации. */
export interface AdsResult {
  items: AdSnapshot[];
  total: number | null;
}

export function useAds(params: {
  alert_state?: string;
  include_inactive?: boolean;
  limit?: number;
  offset?: number;
}) {
  return useQuery<AdsResult>({
    queryKey: KEYS.ads(params),
    queryFn: async () => {
      const { data, total } = await apiGetWithCount<AdSnapshot[]>("/dashboard/ads", params);
      return { items: data, total };
    },
    // Автообновление как на дашборде: список ads сам подтягивает свежие статусы/метрики
    // (например авто-стоп объявления), чтобы снимок не «залипал» без ручного рефреша.
    refetchInterval: 30_000,
    refetchOnWindowFocus: true,
  });
}

export interface AdTimelineMetric {
  cycle_ts: string;
  spend: number | string | null;
  impressions: number | null;
  clicks: number | null;
  leads: number | null;
  deposits: number | null;
}

export interface AdTimeline {
  fb_ad_id: string;
  ad_name: string;
  campaign_name?: string | null;
  adset_name?: string | null;
  offer_code?: string | null;
  from_iso: string;
  to_iso: string;
  metrics: AdTimelineMetric[];
  alerts: AlertEvent[];
  tasks: TaskQueueRow[];
}

export function useAdTimeline(fb_ad_id: string | null) {
  return useQuery({
    queryKey: KEYS.timeline(fb_ad_id ?? ""),
    queryFn: () => apiClient.get<AdTimeline>(`/ads/${fb_ad_id}/timeline`),
    enabled: !!fb_ad_id,
  });
}

export function useCreateDisableTask() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (fb_ad_id: string) =>
      apiClient.post<TaskQueueRow>("/dashboard/disable-tasks", { fb_ad_id }),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ["ads"] });
      qc.invalidateQueries({ queryKey: ["dashboard"] });
    },
  });
}

export const adsKeys = KEYS;
