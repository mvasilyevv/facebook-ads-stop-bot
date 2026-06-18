/**
 * API-хуки для Dashboard.
 *
 * Эндпоинты:
 *   GET /api/dashboard/batch           → DashboardBatch (stats + incidents + alerts + tasks)
 *   GET /api/dashboard/stats           → DashboardStats
 *   GET /api/dashboard/ads             → AdSnapshot[] + X-Total-Count
 *   GET /api/dashboard/alerts          → AlertEvent[]
 *   GET /api/dashboard/incidents       → Incident[]
 *   GET /api/dashboard/spend-history   → SpendPoint[]
 *   GET /api/dashboard/chart-data      → ChartBucket[]
 *   GET /api/dashboard/performance     → DashboardPerformance
 */

import { useQuery } from "@tanstack/react-query";
import { apiGet, apiGetWithCount } from "./client";
import type {
  AdSnapshot,
  AlertEvent,
  ChartBucket,
  DashboardBatch,
  DashboardPerformance,
  DashboardStats,
  Incident,
  SpendPoint,
} from "@fb/shared";

// ─── Batch (главный агрегат для DashboardPage) ────────────────────────────────

export function useDashboardBatch() {
  return useQuery<DashboardBatch>({
    queryKey: ["dashboard", "batch"],
    queryFn: ({ signal }) => apiGet<DashboardBatch>("/dashboard/batch", undefined, signal),
    staleTime: 10_000,
    refetchInterval: 30_000,
  });
}

// ─── Stats (скалярные KPI) ────────────────────────────────────────────────────

export function useDashboardStats() {
  return useQuery<DashboardStats>({
    queryKey: ["dashboard", "stats"],
    queryFn: ({ signal }) => apiGet<DashboardStats>("/dashboard/stats", undefined, signal),
    staleTime: 10_000,
    refetchInterval: 30_000,
  });
}

// ─── Объявления ──────────────────────────────────────────────────────────────

interface AdsParams {
  /** M1: имя совпадает с бэк-параметром /dashboard/ads (CSV alert_state'ов). */
  alert_state?: string;
  limit?: number;
  offset?: number;
  include_inactive?: boolean;
}

export function useDashboardAds(params?: AdsParams) {
  return useQuery<{ data: AdSnapshot[]; total: number | null }>({
    queryKey: ["dashboard", "ads", params],
    queryFn: ({ signal }) =>
      apiGetWithCount<AdSnapshot[]>("/dashboard/ads", params as Record<string, string | number | boolean | null | undefined>, signal),
    staleTime: 10_000,
  });
}

// ─── Алерты ──────────────────────────────────────────────────────────────────

interface AlertsParams {
  hours?: number;
  limit?: number;
}

export function useDashboardAlerts(params?: AlertsParams) {
  return useQuery<AlertEvent[]>({
    queryKey: ["dashboard", "alerts", params],
    queryFn: ({ signal }) =>
      apiGet<AlertEvent[]>("/dashboard/alerts", params as Record<string, string | number | boolean | null | undefined>, signal),
    staleTime: 10_000,
  });
}

// ─── Инциденты ────────────────────────────────────────────────────────────────

export function useDashboardIncidents() {
  return useQuery<Incident[]>({
    queryKey: ["dashboard", "incidents"],
    queryFn: ({ signal }) => apiGet<Incident[]>("/dashboard/incidents", undefined, signal),
    staleTime: 10_000,
    refetchInterval: 30_000,
  });
}

// ─── Chart data ───────────────────────────────────────────────────────────────

interface ChartParams {
  hours?: number;
  bucket?: "hour" | "day";
}

export function useChartData(params?: ChartParams) {
  return useQuery<ChartBucket[]>({
    queryKey: ["dashboard", "chart-data", params],
    queryFn: ({ signal }) =>
      apiGet<ChartBucket[]>("/dashboard/chart-data", params as Record<string, string | number | boolean | null | undefined>, signal),
    staleTime: 30_000,
  });
}

// ─── Spend history ────────────────────────────────────────────────────────────

interface SpendHistoryParams {
  hours?: number;
  fb_ad_id?: string;
}

export function useSpendHistory(params?: SpendHistoryParams) {
  return useQuery<SpendPoint[]>({
    queryKey: ["dashboard", "spend-history", params],
    queryFn: ({ signal }) =>
      apiGet<SpendPoint[]>("/dashboard/spend-history", params as Record<string, string | number | boolean | null | undefined>, signal),
    staleTime: 30_000,
    enabled: !!params?.fb_ad_id || params?.hours !== undefined,
  });
}

// ─── Performance ──────────────────────────────────────────────────────────────

interface PerformanceParams {
  days?: number;
  limit_campaigns?: number;
  limit_offers?: number;
  limit_rules?: number;
}

export function useDashboardPerformance(params?: PerformanceParams) {
  return useQuery<DashboardPerformance>({
    queryKey: ["dashboard", "performance", params],
    queryFn: ({ signal }) =>
      apiGet<DashboardPerformance>("/dashboard/performance", params as Record<string, string | number | boolean | null | undefined>, signal),
    staleTime: 60_000,
  });
}
