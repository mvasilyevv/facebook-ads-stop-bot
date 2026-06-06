/**
 * API-хуки для настроек: observer, telegram, health.
 *
 * Эндпоинты:
 *   GET  /api/settings/observer          → ObserverConfig
 *   PUT  /api/settings/observer          → ObserverConfig
 *   POST /api/settings/observer/scan-now → ScanNowResponse
 *   GET  /api/health/details             → HealthDetails
 *   GET  /api/observer/status            → ObserverStatus
 */

import { useQuery, useMutation, useQueryClient } from "@tanstack/react-query";
import { apiGet, apiSend } from "./client";
import type { HealthDetails, ObserverConfig, ObserverStatus } from "@fb/shared";

// ─── Health ──────────────────────────────────────────────────────────────────

export function useHealthDetails() {
  return useQuery<HealthDetails>({
    queryKey: ["health", "details"],
    queryFn: ({ signal }) => apiGet<HealthDetails>("/health/details", undefined, signal),
    staleTime: 15_000,
    refetchInterval: 30_000,
  });
}

// ─── Observer status ──────────────────────────────────────────────────────────

export function useObserverStatus() {
  return useQuery<ObserverStatus>({
    queryKey: ["observer", "status"],
    queryFn: ({ signal }) => apiGet<ObserverStatus>("/observer/status", undefined, signal),
    staleTime: 10_000,
    refetchInterval: 20_000,
  });
}

// ─── Observer settings ────────────────────────────────────────────────────────

export function useObserverSettings() {
  return useQuery<ObserverConfig>({
    queryKey: ["settings", "observer"],
    queryFn: ({ signal }) => apiGet<ObserverConfig>("/settings/observer", undefined, signal),
    staleTime: 60_000,
  });
}

export function useUpdateObserverSettings() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (data: Partial<ObserverConfig>) =>
      apiSend<ObserverConfig>("PUT", "/settings/observer", data),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ["settings", "observer"] });
    },
  });
}

export function useScanNow() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: () => apiSend<{ ok: boolean }>("POST", "/settings/observer/scan-now"),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ["observer"] });
    },
  });
}
