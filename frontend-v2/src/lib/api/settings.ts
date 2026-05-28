/**
 * Hooks для /settings-страницы.
 */

import { useQuery, useMutation, useQueryClient } from "@tanstack/react-query";
import { apiClient } from "./client";
import type {
  HealthDetails,
  ObserverSettings,
  ObserverStatus,
  ScanRun,
  TelegramSettings,
  VisionSettings,
} from "@/lib/types/api";

const KEYS = {
  observer: ["settings", "observer"] as const,
  observerStatus: ["observer", "status"] as const,
  scanRuns: (limit?: number, filter?: string) =>
    ["observer", "scan-runs", limit, filter] as const,
  telegram: ["settings", "telegram"] as const,
  vision: ["settings", "vision"] as const,
  health: ["health", "details"] as const,
};

export function useObserverSettings() {
  return useQuery({
    queryKey: KEYS.observer,
    queryFn: () => apiClient.get<ObserverSettings>("/settings/observer"),
  });
}

export function useObserverStatus() {
  return useQuery({
    queryKey: KEYS.observerStatus,
    queryFn: () => apiClient.get<ObserverStatus>("/observer/status"),
    refetchInterval: 15_000,
  });
}

export function useScanRuns(limit = 50, filter: "all" | "errors" | "slow" | "with_alerts" = "all") {
  return useQuery({
    queryKey: KEYS.scanRuns(limit, filter),
    queryFn: () => apiClient.get<ScanRun[]>("/observer/scan-runs", { limit, filter }),
  });
}

export function useUpdateObserver() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (data: Partial<ObserverSettings>) =>
      apiClient.put<ObserverSettings>("/settings/observer", data),
    onSuccess: () => qc.invalidateQueries({ queryKey: ["settings", "observer"] }),
  });
}

export function useTelegramSettings() {
  return useQuery({
    queryKey: KEYS.telegram,
    queryFn: () => apiClient.get<TelegramSettings>("/settings/telegram"),
  });
}

export function useVisionSettings() {
  return useQuery({
    queryKey: KEYS.vision,
    queryFn: () => apiClient.get<VisionSettings>("/settings/vision"),
  });
}

export function useHealthDetails() {
  return useQuery({
    queryKey: KEYS.health,
    queryFn: () => apiClient.get<HealthDetails>("/health/details"),
    refetchInterval: 30_000,
  });
}

export function useRestartObserver() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: () => apiClient.post<void>("/observer/restart"),
    onSuccess: () => qc.invalidateQueries({ queryKey: ["observer"] }),
  });
}

export const settingsKeys = KEYS;
