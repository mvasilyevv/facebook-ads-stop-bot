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
  /** Где взят токен: "db" | "env" | null (нет нигде). */
  token_source?: string | null;
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
    // settings-вкладки показывают свою ошибку (try/catch+toast) → глушим глобальный onError.
    meta: { suppressGlobalError: true },
    mutationFn: (data: Partial<ObserverConfig>) =>
      apiSend<ObserverConfig>("PUT", "/settings/observer", data),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ["settings", "observer"] });
    },
  });
}

// ─── Cabinet autostart (расписание авто-включения кабинета) ────────────────────

/** Конфиг автостарта: в HH:MM UTC включаются объявления отслеживаемых кампаний. */
export interface CabinetAutostart {
  enabled: boolean;
  hour_utc: number;
  minute_utc: number;
}

export function useCabinetAutostart() {
  return useQuery<CabinetAutostart>({
    queryKey: ["settings", "cabinet-autostart"],
    queryFn: ({ signal }) =>
      apiGet<CabinetAutostart>("/settings/cabinet-autostart", undefined, signal),
    staleTime: 30_000,
  });
}

export function useUpdateCabinetAutostart() {
  const qc = useQueryClient();
  return useMutation({
    meta: { suppressGlobalError: true },
    mutationFn: (data: CabinetAutostart) =>
      apiSend<CabinetAutostart>("PUT", "/settings/cabinet-autostart", data),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ["settings", "cabinet-autostart"] });
    },
  });
}

export function useScanNow() {
  const qc = useQueryClient();
  return useMutation({
    // settings-вкладки показывают свою ошибку (try/catch+toast) → глушим глобальный onError.
    meta: { suppressGlobalError: true },
    mutationFn: () => apiSend<{ ok: boolean }>("POST", "/settings/observer/scan-now"),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ["observer"] });
    },
  });
}

/** Рестарт observer-воркера (POST /observer/restart — pubsub-сигнал graceful stop). */
export function useRestartObserver() {
  const qc = useQueryClient();
  return useMutation({
    // settings-вкладки показывают свою ошибку (try/catch+toast) → глушим глобальный onError.
    meta: { suppressGlobalError: true },
    mutationFn: () => apiSend<{ status: string; channel: string }>("POST", "/observer/restart"),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ["observer"] });
    },
  });
}

/** Переключение only is_scanning_enabled (PATCH /settings/observer/scanning). */
export function useToggleScanning() {
  const qc = useQueryClient();
  return useMutation({
    // settings-вкладки показывают свою ошибку (try/catch+toast) → глушим глобальный onError.
    meta: { suppressGlobalError: true },
    mutationFn: (enabled: boolean) =>
      apiSend<ObserverConfig>("PATCH", "/settings/observer/scanning", { enabled }),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ["settings", "observer"] });
      qc.invalidateQueries({ queryKey: ["observer"] });
      qc.invalidateQueries({ queryKey: ["dashboard"] });
    },
  });
}

/**
 * Точечное обновление owner_campaign_tag (PATCH /settings/observer/owner-tag).
 * Анти лост-апдейт (аудит 2026-07-12, C-1): full-PUT из кэша молча откатывал
 * is_scanning_enabled — тег сохраняем только точечным PATCH.
 */
export function useUpdateOwnerTag() {
  const qc = useQueryClient();
  return useMutation({
    // settings-вкладки показывают свою ошибку (try/catch+toast) → глушим глобальный onError.
    meta: { suppressGlobalError: true },
    mutationFn: (ownerCampaignTag: string | null) =>
      apiSend<ObserverConfig>("PATCH", "/settings/observer/owner-tag", {
        owner_campaign_tag: ownerCampaignTag,
      }),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ["settings", "observer"] });
      qc.invalidateQueries({ queryKey: ["settings", "observer", "campaigns"] });
    },
  });
}

/** Переключение only auto_enable_recommendations (PATCH /settings/observer/auto-enable). */
export function useToggleAutoEnable() {
  const qc = useQueryClient();
  return useMutation({
    // settings-вкладки показывают свою ошибку (try/catch+toast) → глушим глобальный onError.
    meta: { suppressGlobalError: true },
    mutationFn: (enabled: boolean) =>
      apiSend<ObserverConfig>("PATCH", "/settings/observer/auto-enable", { enabled }),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ["settings", "observer"] });
    },
  });
}

export interface AutoEnableExclusion {
  fb_ad_id: string;
  internal_id: string;
  ad_name?: string | null;
  disabled_at: string;
  reason?: string | null;
}

export function useAutoEnableExclusions() {
  return useQuery<AutoEnableExclusion[]>({
    queryKey: ["settings", "auto-enable-exclusions"],
    queryFn: ({ signal }) =>
      apiGet<AutoEnableExclusion[]>("/dashboard/auto-enable-disabled", undefined, signal),
    staleTime: 30_000,
  });
}

export function useRemoveAutoEnableExclusion() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (fbAdId: string) =>
      apiSend<null>("DELETE", `/dashboard/auto-enable-disabled/${encodeURIComponent(fbAdId)}`),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ["settings", "auto-enable-exclusions"] });
    },
  });
}

// ─── Отслеживаемые кампании (allowlist) ─────────────────────────────────────────

/** Кампания-кандидат для allowlist: id (fb_campaign_id), имя, выбрана ли сейчас. */
export interface CampaignOption {
  id: string;
  name: string;
  selected: boolean;
}

/**
 * Список накопленных observer'ом кампаний по owner_tag (GET /settings/observer/campaigns).
 * includeStale=false (дефолт): бэк прячет кампании с датой в имени старше 14 дней,
 * кроме уже выбранных в allowlist (решение владельца 03.07 — старьё мешает выбирать).
 */
export function useObserverCampaigns(includeStale = false) {
  return useQuery<CampaignOption[]>({
    queryKey: ["settings", "observer", "campaigns", { includeStale }],
    queryFn: ({ signal }) =>
      apiGet<CampaignOption[]>(
        "/settings/observer/campaigns",
        { include_stale: includeStale },
        signal,
      ),
    staleTime: 30_000,
  });
}

/** Live-резолв всех кампаний владельца через browser-agent (POST /campaigns/refresh). */
export function useRefreshObserverCampaigns() {
  const qc = useQueryClient();
  return useMutation({
    // settings-вкладки показывают свою ошибку (try/catch+toast) → глушим глобальный onError.
    meta: { suppressGlobalError: true },
    mutationFn: (includeStale: boolean = false) =>
      apiSend<CampaignOption[]>(
        "POST",
        `/settings/observer/campaigns/refresh?include_stale=${includeStale}`,
      ),
    onSuccess: (data, includeStale) => {
      qc.setQueryData(["settings", "observer", "campaigns", { includeStale }], data);
    },
  });
}

/** Сохранить allowlist отслеживаемых кампаний (PATCH /settings/observer/campaigns). */
export function useSetCampaignAllowlist() {
  const qc = useQueryClient();
  return useMutation({
    // settings-вкладки показывают свою ошибку (try/catch+toast) → глушим глобальный onError.
    meta: { suppressGlobalError: true },
    mutationFn: (campaign_ids: string[]) =>
      apiSend<ObserverConfig>("PATCH", "/settings/observer/campaigns", { campaign_ids }),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ["settings", "observer"] });
      qc.invalidateQueries({ queryKey: ["settings", "observer", "campaigns"] });
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

export interface TelegramOwnerInvite {
  code: string;
  expires_at: string;
  role: "owner";
  auth_deep_link: string | null;
  activation_command: string;
}

export function useCreateTelegramOwnerInvite() {
  const qc = useQueryClient();
  return useMutation({
    meta: { suppressGlobalError: true },
    mutationFn: () => apiSend<TelegramOwnerInvite>("POST", "/settings/telegram/owner-invite"),
    onSuccess: (invite) => {
      // Показываем готовую ссылку сразу, не ожидая повторного GET.
      qc.setQueryData<TelegramSettings>(["settings", "telegram"], (current) =>
        current
          ? {
              ...current,
              auth_deep_link: invite.auth_deep_link,
              activation_command: invite.activation_command,
              auth_invite_expires_at: invite.expires_at,
            }
          : current,
      );
      qc.invalidateQueries({ queryKey: ["settings", "telegram"] });
    },
  });
}

export function useUpdateTelegramToken() {
  const qc = useQueryClient();
  return useMutation({
    // settings-вкладки показывают свою ошибку (try/catch+toast) → глушим глобальный onError.
    meta: { suppressGlobalError: true },
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
    // settings-вкладки показывают свою ошибку (try/catch+toast) → глушим глобальный onError.
    meta: { suppressGlobalError: true },
    // L3: бэк-маршрут — DELETE /settings/telegram (очистка token+chat_id).
    // /settings/telegram/token не существует (был 405).
    mutationFn: () => apiSend<TelegramSettings>("DELETE", "/settings/telegram"),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ["settings", "telegram"] });
    },
  });
}

// ─── Vision settings ──────────────────────────────────────────────────────────

export function useVisionSettings() {
  return useQuery<VisionSettingsResponse>({
    queryKey: ["settings", "vision"],
    queryFn: ({ signal }) => apiGet<VisionSettingsResponse>("/settings/vision", undefined, signal),
    staleTime: 20_000,
  });
}

export function useUpdateVisionSettings() {
  const qc = useQueryClient();
  return useMutation({
    // settings-вкладки показывают свою ошибку (try/catch+toast) → глушим глобальный onError.
    meta: { suppressGlobalError: true },
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
    // settings-вкладки показывают свою ошибку (try/catch+toast) → глушим глобальный onError.
    meta: { suppressGlobalError: true },
    mutationFn: () => apiSend<{ ok: boolean }>("POST", "/settings/vision/reconnect"),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ["settings", "vision"] });
    },
  });
}
