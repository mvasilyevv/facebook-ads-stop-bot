/** Generated OpenAPI hooks for observer, Telegram and Vision settings. */
import { useMutation, useQueryClient } from "@tanstack/react-query";
import type { components } from "@fb/shared/api/generated";
import { createOperatorDisplayPreferenceHooks, dataOrThrow } from "@fb/operator-api";
import { generatedApi, generatedFetchApi } from "./generatedClient";

export type ObserverConfig = components["schemas"]["ObserverSettingsResponse"];
export type TelegramSettings = components["schemas"]["TelegramSettingsResponse"];
export type TelegramNotificationDiagnostics =
  components["schemas"]["TelegramNotificationDiagnosticsResponse"];
export type TelegramRecipient = components["schemas"]["TelegramRecipientResponse"];
export type TelegramRecipientPreferences =
  components["schemas"]["TelegramRecipientPreferenceResponse"];
export type TelegramRecipientPreferenceRequest =
  components["schemas"]["TelegramRecipientPreferenceRequest"];
export type VisionSettingsResponse = components["schemas"]["VisionSettingsResponse"];
export type CampaignOption = components["schemas"]["CampaignOption"];
export type TelegramOwnerInvite = components["schemas"]["TelegramInviteResponse"];

const observerKey = ["get", "/api/settings/observer"] as const;
const telegramKey = ["get", "/api/settings/telegram"] as const;
const telegramRecipientsKey = ["get", "/api/settings/telegram/recipients"] as const;
const visionKey = ["get", "/api/settings/vision"] as const;
const visionProfilesKey = ["get", "/api/settings/vision/profiles"] as const;

export const { useOperatorDisplayPreference, useUpdateOperatorDisplayPreference } =
  createOperatorDisplayPreferenceHooks(generatedApi);

export function useObserverSettings() {
  return generatedApi.useQuery("get", "/api/settings/observer", {}, { staleTime: 60_000 });
}

export function useUpdateObserverInterval() {
  const qc = useQueryClient();
  return useMutation({
    meta: { suppressGlobalError: true },
    mutationFn: (default_interval_seconds: number) =>
      dataOrThrow(
        generatedFetchApi.PATCH("/api/settings/observer/interval", {
          body: { default_interval_seconds },
        }),
      ),
    onSuccess: () => void qc.invalidateQueries({ queryKey: observerKey }),
  });
}

function invalidateObserver(qc: ReturnType<typeof useQueryClient>) {
  return () => void qc.invalidateQueries({ queryKey: observerKey });
}
export function useToggleScanning() {
  const qc = useQueryClient();
  return useMutation({
    meta: { suppressGlobalError: true },
    mutationFn: (enabled: boolean) =>
      dataOrThrow(
        generatedFetchApi.PATCH("/api/settings/observer/scanning", { body: { enabled } }),
      ),
    onSuccess: invalidateObserver(qc),
  });
}
export function useUpdateOwnerTag() {
  const qc = useQueryClient();
  return useMutation({
    meta: { suppressGlobalError: true },
    mutationFn: (owner_campaign_tag: string | null) =>
      dataOrThrow(
        generatedFetchApi.PATCH("/api/settings/observer/owner-tag", {
          body: { owner_campaign_tag },
        }),
      ),
    onSuccess: invalidateObserver(qc),
  });
}
export function useObserverCampaigns(includeStale = false) {
  return generatedApi.useQuery(
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
        generatedFetchApi.POST("/api/settings/observer/campaigns/refresh", {
          params: { query: { include_stale: includeStale } },
        }),
      ),
    onSuccess: () =>
      void qc.invalidateQueries({ queryKey: ["get", "/api/settings/observer/campaigns"] }),
  });
}
export function useSetCampaignAllowlist() {
  const qc = useQueryClient();
  return useMutation({
    meta: { suppressGlobalError: true },
    mutationFn: (campaign_ids: string[]) =>
      dataOrThrow(
        generatedFetchApi.PATCH("/api/settings/observer/campaigns", { body: { campaign_ids } }),
      ),
    onSuccess: invalidateObserver(qc),
  });
}

export function useUpdateAdsManagerColumns() {
  const qc = useQueryClient();
  return useMutation({
    meta: { suppressGlobalError: true },
    mutationFn: (column_ids: string[] | null) =>
      dataOrThrow(
        generatedFetchApi.PATCH("/api/settings/observer/ads-manager-columns", {
          body: { column_ids },
        }),
      ),
    onSuccess: invalidateObserver(qc),
  });
}

export function useScanObserverNow() {
  return useMutation({
    meta: { suppressGlobalError: true },
    mutationFn: () => dataOrThrow(generatedFetchApi.POST("/api/settings/observer/scan-now")),
  });
}

export function useTelegramSettings() {
  return generatedApi.useQuery("get", "/api/settings/telegram", {}, { staleTime: 30_000 });
}
export function useTelegramNotificationDiagnostics() {
  return generatedApi.useQuery(
    "get",
    "/api/settings/telegram/diagnostics",
    {},
    { staleTime: 15_000, refetchInterval: 30_000 },
  );
}
export function useTelegramRecipients() {
  return generatedApi.useQuery(
    "get",
    "/api/settings/telegram/recipients",
    {},
    { staleTime: 30_000 },
  );
}
export function useTelegramRecipientPreferences(recipientId: string | null) {
  return generatedApi.useQuery(
    "get",
    "/api/settings/telegram/recipients/{recipient_id}/preferences",
    { params: { path: { recipient_id: recipientId ?? "" } } },
    { enabled: Boolean(recipientId), staleTime: 30_000 },
  );
}
export function useCreateTelegramRecipientInvite() {
  return useMutation({
    meta: { suppressGlobalError: true },
    mutationFn: () =>
      dataOrThrow(generatedFetchApi.POST("/api/settings/telegram/recipients/invite")),
  });
}
export function useUpdateTelegramWebAppUrl() {
  const qc = useQueryClient();
  return useMutation({
    meta: { suppressGlobalError: true },
    mutationFn: (web_app_url: string | null) =>
      dataOrThrow(
        generatedFetchApi.PUT("/api/settings/telegram/web-app-url", {
          body: { web_app_url },
        }),
      ),
    onSuccess: () => void qc.invalidateQueries({ queryKey: telegramKey }),
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
        generatedFetchApi.PUT("/api/settings/telegram/recipients/{recipient_id}/preferences", {
          params: { path: { recipient_id: recipientId } },
          body,
        }),
      ),
    onSuccess: () =>
      void qc.invalidateQueries({
        queryKey: ["get", "/api/settings/telegram/recipients/{recipient_id}/preferences"],
      }),
  });
}
export function useDeleteTelegramRecipient() {
  const qc = useQueryClient();
  return useMutation({
    meta: { suppressGlobalError: true },
    mutationFn: (recipientId: string) =>
      dataOrThrow(
        generatedFetchApi.DELETE("/api/settings/telegram/recipients/{recipient_id}", {
          params: { path: { recipient_id: recipientId } },
        }),
      ),
    onSuccess: () => void qc.invalidateQueries({ queryKey: telegramRecipientsKey }),
  });
}
export function useCreateTelegramOwnerInvite() {
  const qc = useQueryClient();
  return useMutation({
    meta: { suppressGlobalError: true },
    mutationFn: () => dataOrThrow(generatedFetchApi.POST("/api/settings/telegram/owner-invite")),
    onSuccess: () => void qc.invalidateQueries({ queryKey: telegramKey }),
  });
}
export function useUpdateTelegramToken() {
  const qc = useQueryClient();
  return useMutation({
    meta: { suppressGlobalError: true },
    mutationFn: (bot_token: string) =>
      dataOrThrow(generatedFetchApi.PUT("/api/settings/telegram/token", { body: { bot_token } })),
    onSuccess: () => void qc.invalidateQueries({ queryKey: telegramKey }),
  });
}
export function useDeleteTelegramToken() {
  const qc = useQueryClient();
  return useMutation({
    meta: { suppressGlobalError: true },
    mutationFn: () => dataOrThrow(generatedFetchApi.DELETE("/api/settings/telegram")),
    onSuccess: () => void qc.invalidateQueries({ queryKey: telegramKey }),
  });
}

export function useVisionSettings() {
  return generatedApi.useQuery("get", "/api/settings/vision", {}, { staleTime: 20_000 });
}
/**
 * Список профилей Vision. Не кэшируется: имена и сами идентификаторы живут в
 * облаке и меняются там, поэтому список читается заново при каждом открытии.
 */
export function useVisionProfiles() {
  return generatedApi.useQuery(
    "get",
    "/api/settings/vision/profiles",
    {},
    { staleTime: 0, gcTime: 0, refetchOnMount: "always" },
  );
}
export function useUpdateVisionSettings() {
  const qc = useQueryClient();
  return useMutation({
    meta: { suppressGlobalError: true },
    mutationFn: (body: components["schemas"]["VisionSettingsUpdateRequest"]) =>
      dataOrThrow(generatedFetchApi.PUT("/api/settings/vision", { body })),
    onSuccess: () => {
      void qc.invalidateQueries({ queryKey: visionKey });
      // Смена токена или папки меняет и видимый список профилей.
      void qc.invalidateQueries({ queryKey: visionProfilesKey });
    },
  });
}
export function useReconnectVision() {
  const qc = useQueryClient();
  return useMutation({
    meta: { suppressGlobalError: true },
    mutationFn: () => dataOrThrow(generatedFetchApi.POST("/api/vision/reconnect")),
    onSuccess: () => void qc.invalidateQueries({ queryKey: visionKey }),
  });
}
