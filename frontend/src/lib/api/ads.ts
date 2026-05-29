/**
 * Hooks для /ads-страницы.
 * Используются: AdsPage, AdDrawer, BulkActionBar.
 */

import { useQuery, useMutation, useQueryClient } from "@tanstack/react-query";
import { apiClient } from "./client";
import type { AdSnapshot, AlertEvent, TaskQueueRow } from "@/lib/types/api";

const KEYS = {
  ads: (params?: Record<string, unknown>) => ["ads", params] as const,
  timeline: (fb_ad_id: string) => ["ads", "timeline", fb_ad_id] as const,
};

export function useAds(params: {
  alert_state?: string;
  include_inactive?: boolean;
  limit?: number;
  offset?: number;
}) {
  return useQuery({
    queryKey: KEYS.ads(params),
    queryFn: () => apiClient.get<AdSnapshot[]>("/dashboard/ads", params),
  });
}

export interface AdTimeline {
  metrics: Array<Record<string, unknown>>;
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
