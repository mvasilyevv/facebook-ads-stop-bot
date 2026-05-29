/**
 * Hooks для /settings-страницы.
 * Содержит как query-хуки, так и мутации для Observer / Telegram / Vision / Health.
 */

import { useQuery, useMutation, useQueryClient } from "@tanstack/react-query";
import { apiClient } from "./client";
import type {
  HealthDetails,
  ObserverSettings,
  ObserverStatus,
  ScanRun,
  TelegramSettings,
  TelegramRecipient,
  TelegramInviteResponse,
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

/** POST /settings/observer/scan-now — запустить скан немедленно. */
export function useTriggerScanNowSettings() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: () => apiClient.post<void>("/settings/observer/scan-now"),
    onSuccess: () => qc.invalidateQueries({ queryKey: ["observer"] }),
  });
}

/** PATCH /settings/observer/scanning — переключить is_scanning. */
export function useToggleScanning() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (enabled: boolean) =>
      apiClient.patch<ObserverSettings>("/settings/observer/scanning", { enabled }),
    onSuccess: () => qc.invalidateQueries({ queryKey: KEYS.observer }),
  });
}

/** PATCH /settings/observer/auto-enable — переключить auto_enable_recommendations. */
export function useToggleAutoEnable() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (enabled: boolean) =>
      apiClient.patch<ObserverSettings>("/settings/observer/auto-enable", { enabled }),
    onSuccess: () => qc.invalidateQueries({ queryKey: KEYS.observer }),
  });
}

/** Получить список recipients Telegram. */
export function useTelegramRecipients() {
  return useQuery({
    queryKey: [...KEYS.telegram, "recipients"] as const,
    queryFn: () => apiClient.get<TelegramRecipient[]>("/settings/telegram/recipients"),
  });
}

/** PUT /settings/telegram/token — установить токен бота. */
export function useSetTelegramToken() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (token: string) =>
      apiClient.put<void>("/settings/telegram/token", { token }),
    onSuccess: () => qc.invalidateQueries({ queryKey: KEYS.telegram }),
  });
}

/** DELETE /settings/telegram/token — удалить токен бота. */
export function useDeleteTelegramToken() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: () => apiClient.delete<void>("/settings/telegram/token"),
    onSuccess: () => qc.invalidateQueries({ queryKey: KEYS.telegram }),
  });
}

/** DELETE /settings/telegram/recipients/{id} — удалить получателя. */
export function useDeleteTelegramRecipient() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (id: string) => apiClient.delete<void>(`/settings/telegram/recipients/${id}`),
    onSuccess: () => qc.invalidateQueries({ queryKey: [...KEYS.telegram] }),
  });
}

/** POST /settings/telegram/recipients/invite — сгенерировать инвайт-код. */
export function useCreateTelegramInvite() {
  return useMutation({
    mutationFn: () => apiClient.post<TelegramInviteResponse>("/settings/telegram/recipients/invite"),
  });
}

/** PUT /settings/vision — обновить Vision token/profile. */
export function useUpdateVision() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (data: { vision_token?: string; profile_id?: string }) =>
      apiClient.put<VisionSettings>("/settings/vision", data),
    onSuccess: () => qc.invalidateQueries({ queryKey: KEYS.vision }),
  });
}

/** POST /vision/reconnect — переподключить Vision. */
export function useVisionReconnect() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: () => apiClient.post<void>("/vision/reconnect"),
    onSuccess: () => qc.invalidateQueries({ queryKey: KEYS.vision }),
  });
}

/** POST /disable-worker/restart — перезапустить disable-worker. */
export function useRestartDisableWorker() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: () => apiClient.post<void>("/disable-worker/restart"),
    onSuccess: () => qc.invalidateQueries({ queryKey: KEYS.health }),
  });
}

export const settingsKeys = KEYS;
