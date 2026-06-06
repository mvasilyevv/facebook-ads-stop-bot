/**
 * API-хуки для настроек: observer, telegram, vision, health.
 *
 * Эндпоинты:
 *   GET  /api/settings/observer          → ObserverConfig
 *   PUT  /api/settings/observer          → ObserverConfig
 *   POST /api/settings/observer/scan-now → ScanNowResponse
 *   GET  /api/health/details             → HealthDetails
 *   GET  /api/observer/status            → ObserverStatus
 *   GET  /api/settings/telegram          → TelegramSettings
 *   PUT  /api/settings/telegram/token    → TelegramSettings
 *   GET  /api/settings/vision            → VisionSettingsResponse
 *   PUT  /api/settings/vision            → VisionSettingsResponse
 *   POST /api/settings/vision/reconnect  → { ok: boolean }
 */

import { useQuery, useMutation, useQueryClient } from "@tanstack/react-query";
import { apiGet, apiSend } from "./client";
import type { HealthDetails, ObserverConfig, ObserverStatus, TelegramSettings } from "@fb/shared";

/** Ответ настроек Vision — не вынесен в @fb/shared, описываем здесь. */
export interface VisionSettingsResponse {
  has_token: boolean;
  profile_id?: string | null;
  auto_restart_on_missing_cdp: boolean;
  runtime_status?: string | null;
  runtime_status_message?: string | null;
  cdp_ready: boolean;
  cdp_port?: number | null;
}

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

// ─── Telegram settings ────────────────────────────────────────────────────────

export function useTelegramSettings() {
  return useQuery<TelegramSettings>({
    queryKey: ["settings", "telegram"],
    queryFn: ({ signal }) => apiGet<TelegramSettings>("/settings/telegram", undefined, signal),
    staleTime: 30_000,
  });
}

export function useUpdateTelegramToken() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (bot_token: string) =>
      apiSend<TelegramSettings>("PUT", "/settings/telegram/token", { bot_token }),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ["settings", "telegram"] });
    },
  });
}

export function useDeleteTelegramToken() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: () => apiSend<TelegramSettings>("DELETE", "/settings/telegram/token"),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ["settings", "telegram"] });
    },
  });
}

// ─── Vision settings ──────────────────────────────────────────────────────────

export function useVisionSettings() {
  return useQuery<VisionSettingsResponse>({
    queryKey: ["settings", "vision"],
    queryFn: ({ signal }) =>
      apiGet<VisionSettingsResponse>("/settings/vision", undefined, signal),
    staleTime: 20_000,
  });
}

export function useUpdateVisionSettings() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (data: { x_token?: string; profile_id?: string }) =>
      apiSend<VisionSettingsResponse>("PUT", "/settings/vision", data),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ["settings", "vision"] });
    },
  });
}

export function useReconnectVision() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: () => apiSend<{ ok: boolean }>("POST", "/settings/vision/reconnect"),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ["settings", "vision"] });
    },
  });
}
