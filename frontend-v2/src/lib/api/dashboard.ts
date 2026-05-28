/**
 * TanStack Query wrappers для dashboard endpoint'ов.
 * Соответствие apps/api/routers/v1/dashboard*.py.
 */

import { useQuery, useMutation, useQueryClient } from "@tanstack/react-query";
import { apiClient } from "./client";
import type {
  DashboardStats,
  DashboardBatch,
  AdSnapshot,
  AlertEvent,
  Incident,
  SpendPoint,
  ChartBucket,
} from "@/lib/types/api";

const KEYS = {
  stats: ["dashboard", "stats"] as const,
  batch: ["dashboard", "batch"] as const,
  ads: (params?: Record<string, unknown>) => ["dashboard", "ads", params] as const,
  alerts: (params?: Record<string, unknown>) => ["dashboard", "alerts", params] as const,
  incidents: (stage?: string) => ["dashboard", "incidents", stage] as const,
  spendHistory: (params?: Record<string, unknown>) =>
    ["dashboard", "spend-history", params] as const,
  chartData: (params?: Record<string, unknown>) => ["dashboard", "chart-data", params] as const,
};

export function useDashboardStats() {
  return useQuery({
    queryKey: KEYS.stats,
    queryFn: () => apiClient.get<DashboardStats>("/dashboard/stats"),
    refetchInterval: 30_000,
  });
}

export function useDashboardBatch() {
  return useQuery({
    queryKey: KEYS.batch,
    queryFn: () => apiClient.get<DashboardBatch>("/dashboard/batch"),
    refetchInterval: 60_000,
  });
}

export function useAdSnapshots(params: {
  alert_state?: string;
  fb_ad_ids?: string;
  include_inactive?: boolean;
  limit?: number;
  offset?: number;
}) {
  return useQuery({
    queryKey: KEYS.ads(params),
    queryFn: () => apiClient.get<AdSnapshot[]>("/dashboard/ads", params),
  });
}

export function useAlertEvents(params: {
  stage?: string;
  fb_ad_id?: string;
  from_iso?: string;
  to_iso?: string;
  limit?: number;
}) {
  return useQuery({
    queryKey: KEYS.alerts(params),
    queryFn: () => apiClient.get<AlertEvent[]>("/dashboard/alerts", params),
  });
}

export function useIncidents(stage: "warning" | "stop" | "all" = "all", limit = 100) {
  return useQuery({
    queryKey: KEYS.incidents(stage),
    queryFn: () => apiClient.get<Incident[]>("/dashboard/incidents", { stage, limit }),
  });
}

export function useSpendHistory(params: { hours?: number; fb_ad_id?: string }) {
  return useQuery({
    queryKey: KEYS.spendHistory(params),
    queryFn: () => apiClient.get<SpendPoint[]>("/dashboard/spend-history", params),
  });
}

export function useChartData(params: { hours?: number; bucket?: "hour" | "day" }) {
  return useQuery({
    queryKey: KEYS.chartData(params),
    queryFn: () => apiClient.get<ChartBucket[]>("/dashboard/chart-data", params),
  });
}

export function useTriggerScanNow() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: () => apiClient.post<void>("/settings/observer/scan-now"),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: KEYS.stats });
      qc.invalidateQueries({ queryKey: KEYS.batch });
    },
  });
}

export const dashboardKeys = KEYS;
