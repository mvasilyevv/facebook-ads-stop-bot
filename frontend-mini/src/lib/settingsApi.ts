import { useMutation, useQueryClient } from "@tanstack/react-query";
import type { components } from "@fb/shared/api/generated";
import {
  createOperatorDisplayPreferenceHooks,
  dataOrThrow,
} from "@fb/operator-api";

import { tmaApi, tmaFetchApi } from "./auth";

export type TelegramSettings =
  components["schemas"]["TelegramSettingsResponse"];
export type TelegramNotificationDiagnostics =
  components["schemas"]["TelegramNotificationDiagnosticsResponse"];
export type TelegramRecipient =
  components["schemas"]["TelegramRecipientResponse"];
export type TelegramRecipientPreferenceRequest =
  components["schemas"]["TelegramRecipientPreferenceRequest"];
export type VisionSettings = components["schemas"]["VisionSettingsResponse"];
export type ObserverSettings =
  components["schemas"]["ObserverSettingsResponse"];
export type CampaignOption = components["schemas"]["CampaignOption"];

const SETTINGS_KEYS = {
  observer: ["get", "/api/settings/observer"] as const,
  observerCampaigns: ["get", "/api/settings/observer/campaigns"] as const,
  telegram: ["get", "/api/settings/telegram"] as const,
  telegramRecipients: ["get", "/api/settings/telegram/recipients"] as const,
  telegramRecipientPreferences: [
    "get",
    "/api/settings/telegram/recipients/{recipient_id}/preferences",
  ] as const,
  vision: ["get", "/api/settings/vision"] as const,
};

export const {
  useOperatorDisplayPreference,
  useUpdateOperatorDisplayPreference,
} = createOperatorDisplayPreferenceHooks(tmaApi);

export function useObserverSettings() {
  return tmaApi.useQuery(
    "get",
    "/api/settings/observer",
    {},
    { staleTime: 30_000 },
  );
}

export function useToggleObserverScanning() {
  const qc = useQueryClient();
  return useMutation({
    meta: { suppressGlobalError: true },
    mutationFn: (enabled: boolean) =>
      dataOrThrow(
        tmaFetchApi.PATCH("/api/settings/observer/scanning", {
          body: { enabled },
        }),
      ),
    onSuccess: () =>
      void qc.invalidateQueries({ queryKey: SETTINGS_KEYS.observer }),
  });
}

export function useUpdateObserverInterval() {
  const qc = useQueryClient();
  return useMutation({
    meta: { suppressGlobalError: true },
    mutationFn: (default_interval_seconds: number) =>
      dataOrThrow(
        tmaFetchApi.PATCH("/api/settings/observer/interval", {
          body: { default_interval_seconds },
        }),
      ),
    onSuccess: () =>
      void qc.invalidateQueries({ queryKey: SETTINGS_KEYS.observer }),
  });
}

export function useUpdateObserverOwnerTag() {
  const qc = useQueryClient();
  return useMutation({
    meta: { suppressGlobalError: true },
    mutationFn: (owner_campaign_tag: string | null) =>
      dataOrThrow(
        tmaFetchApi.PATCH("/api/settings/observer/owner-tag", {
          body: { owner_campaign_tag },
        }),
      ),
    onSuccess: () =>
      void qc.invalidateQueries({ queryKey: SETTINGS_KEYS.observer }),
  });
}

export function useObserverCampaigns(includeStale = false) {
  return tmaApi.useQuery(
    "get",
    "/api/settings/observer/campaigns",
    { params: { query: { include_stale: includeStale } } },
    { staleTime: 30_000 },
  );
}

export function useRefreshObserverCampaigns() {
  const qc = useQueryClient();
  return useMutation({
    meta: { suppressGlobalError: true },
    mutationFn: (includeStale: boolean) =>
      dataOrThrow(
        tmaFetchApi.POST("/api/settings/observer/campaigns/refresh", {
          params: { query: { include_stale: includeStale } },
        }),
      ),
    onSuccess: () =>
      void qc.invalidateQueries({ queryKey: SETTINGS_KEYS.observerCampaigns }),
  });
}

export function useSetObserverCampaignAllowlist() {
  const qc = useQueryClient();
  return useMutation({
    meta: { suppressGlobalError: true },
    mutationFn: (campaign_ids: string[]) =>
      dataOrThrow(
        tmaFetchApi.PATCH("/api/settings/observer/campaigns", {
          body: { campaign_ids },
        }),
      ),
    onSuccess: () =>
      void qc.invalidateQueries({ queryKey: SETTINGS_KEYS.observer }),
  });
}

export function useScanObserverNow() {
  return useMutation({
    meta: { suppressGlobalError: true },
    mutationFn: () =>
      dataOrThrow(tmaFetchApi.POST("/api/settings/observer/scan-now")),
  });
}

export function useTelegramSettings() {
  return tmaApi.useQuery(
    "get",
    "/api/settings/telegram",
    {},
    { staleTime: 30_000 },
  );
}

export function useTelegramNotificationDiagnostics() {
  return tmaApi.useQuery(
    "get",
    "/api/settings/telegram/diagnostics",
    {},
    { staleTime: 15_000, refetchInterval: 30_000 },
  );
}

export function useTelegramRecipients() {
  return tmaApi.useQuery(
    "get",
    "/api/settings/telegram/recipients",
    {},
    { staleTime: 30_000 },
  );
}

export function useTelegramRecipientPreferences(recipientId: string | null) {
  return tmaApi.useQuery(
    "get",
    "/api/settings/telegram/recipients/{recipient_id}/preferences",
    { params: { path: { recipient_id: recipientId ?? "" } } },
    { enabled: Boolean(recipientId), staleTime: 30_000 },
  );
}

export function useCreateTelegramOwnerInvite() {
  const qc = useQueryClient();
  return useMutation({
    meta: { suppressGlobalError: true },
    mutationFn: () =>
      dataOrThrow(tmaFetchApi.POST("/api/settings/telegram/owner-invite")),
    onSuccess: () =>
      void qc.invalidateQueries({ queryKey: SETTINGS_KEYS.telegram }),
  });
}

export function useCreateTelegramRecipientInvite() {
  return useMutation({
    meta: { suppressGlobalError: true },
    mutationFn: () =>
      dataOrThrow(tmaFetchApi.POST("/api/settings/telegram/recipients/invite")),
  });
}

export function useUpdateTelegramToken() {
  const qc = useQueryClient();
  return useMutation({
    meta: { suppressGlobalError: true },
    mutationFn: (bot_token: string) =>
      dataOrThrow(
        tmaFetchApi.PUT("/api/settings/telegram/token", {
          body: { bot_token },
        }),
      ),
    onSuccess: () =>
      void qc.invalidateQueries({ queryKey: SETTINGS_KEYS.telegram }),
  });
}

export function useDeleteTelegramToken() {
  const qc = useQueryClient();
  return useMutation({
    meta: { suppressGlobalError: true },
    mutationFn: () => dataOrThrow(tmaFetchApi.DELETE("/api/settings/telegram")),
    onSuccess: () =>
      void qc.invalidateQueries({ queryKey: SETTINGS_KEYS.telegram }),
  });
}

export function useUpdateTelegramWebAppUrl() {
  const qc = useQueryClient();
  return useMutation({
    meta: { suppressGlobalError: true },
    mutationFn: (web_app_url: string | null) =>
      dataOrThrow(
        tmaFetchApi.PUT("/api/settings/telegram/web-app-url", {
          body: { web_app_url },
        }),
      ),
    onSuccess: () =>
      void qc.invalidateQueries({ queryKey: SETTINGS_KEYS.telegram }),
  });
}

export function useUpdateTelegramRecipientPreferences() {
  const qc = useQueryClient();
  return useMutation({
    meta: { suppressGlobalError: true },
    mutationFn: ({
      recipientId,
      body,
    }: {
      recipientId: string;
      body: TelegramRecipientPreferenceRequest;
    }) =>
      dataOrThrow(
        tmaFetchApi.PUT(
          "/api/settings/telegram/recipients/{recipient_id}/preferences",
          {
            params: { path: { recipient_id: recipientId } },
            body,
          },
        ),
      ),
    onSuccess: () =>
      void qc.invalidateQueries({
        queryKey: SETTINGS_KEYS.telegramRecipientPreferences,
      }),
  });
}

export function useDeleteTelegramRecipient() {
  const qc = useQueryClient();
  return useMutation({
    meta: { suppressGlobalError: true },
    mutationFn: (recipientId: string) =>
      dataOrThrow(
        tmaFetchApi.DELETE("/api/settings/telegram/recipients/{recipient_id}", {
          params: { path: { recipient_id: recipientId } },
        }),
      ),
    onSuccess: () =>
      void qc.invalidateQueries({ queryKey: SETTINGS_KEYS.telegramRecipients }),
  });
}

export function useVisionSettings() {
  return tmaApi.useQuery(
    "get",
    "/api/settings/vision",
    {},
    { staleTime: 20_000 },
  );
}

export function useUpdateVisionSettings() {
  const qc = useQueryClient();
  return useMutation({
    meta: { suppressGlobalError: true },
    mutationFn: (body: components["schemas"]["VisionSettingsUpdateRequest"]) =>
      dataOrThrow(tmaFetchApi.PUT("/api/settings/vision", { body })),
    onSuccess: () =>
      void qc.invalidateQueries({ queryKey: SETTINGS_KEYS.vision }),
  });
}

export function useReconnectVision() {
  const qc = useQueryClient();
  return useMutation({
    meta: { suppressGlobalError: true },
    mutationFn: () => dataOrThrow(tmaFetchApi.POST("/api/vision/reconnect")),
    onSuccess: () =>
      void qc.invalidateQueries({ queryKey: SETTINGS_KEYS.vision }),
  });
}
