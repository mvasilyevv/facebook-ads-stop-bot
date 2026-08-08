/** Generated OpenAPI hooks for observer, Telegram and Vision settings. */
import { useMutation, useQueryClient } from "@tanstack/react-query";
import type { components } from "@fb/shared/api/generated";
import { dataOrThrow, noContentOrThrow } from "@fb/operator-api";
import { generatedApi, generatedFetchApi } from "./generatedClient";

export type ObserverConfig = components["schemas"]["ObserverSettingsResponse"];
export type TelegramSettings = components["schemas"]["TelegramSettingsResponse"];
export type TelegramNotificationDiagnostics = components["schemas"]["TelegramNotificationDiagnosticsResponse"];
export type VisionSettingsResponse = components["schemas"]["VisionSettingsResponse"];
export type CabinetAutostart = components["schemas"]["CabinetAutostartResponse"];
export type AutoEnableExclusion = components["schemas"]["AutoEnableExclusionResponse"];
export type CampaignOption = components["schemas"]["CampaignOption"];
export type TelegramOwnerInvite = components["schemas"]["TelegramInviteResponse"];

const observerKey = ["get", "/api/settings/observer"] as const;
const telegramKey = ["get", "/api/settings/telegram"] as const;
const visionKey = ["get", "/api/settings/vision"] as const;

export function useObserverSettings() {
  return generatedApi.useQuery("get", "/api/settings/observer", {}, { staleTime: 60_000 });
}

export function useUpdateObserverSettings() {
  const qc = useQueryClient();
  return useMutation({
    meta: { suppressGlobalError: true },
    mutationFn: (body: components["schemas"]["ObserverSettingsPutRequest"]) =>
      dataOrThrow(generatedFetchApi.PUT("/api/settings/observer", { body })),
    onSuccess: () => void qc.invalidateQueries({ queryKey: observerKey }),
  });
}

export function useCabinetAutostart() {
  return generatedApi.useQuery("get", "/api/settings/cabinet-autostart", {}, { staleTime: 30_000 });
}

export function useUpdateCabinetAutostart() {
  const qc = useQueryClient();
  return useMutation({
    meta: { suppressGlobalError: true },
    mutationFn: (body: components["schemas"]["CabinetAutostartPutRequest"]) =>
      dataOrThrow(generatedFetchApi.PUT("/api/settings/cabinet-autostart", { body })),
    onSuccess: () => void qc.invalidateQueries({ queryKey: ["get", "/api/settings/cabinet-autostart"] }),
  });
}

function invalidateObserver(qc: ReturnType<typeof useQueryClient>) {
  return () => void qc.invalidateQueries({ queryKey: observerKey });
}
export function useToggleScanning() {
  const qc = useQueryClient();
  return useMutation({
    meta: { suppressGlobalError: true },
    mutationFn: (enabled: boolean) => dataOrThrow(generatedFetchApi.PATCH("/api/settings/observer/scanning", { body: { enabled } })),
    onSuccess: invalidateObserver(qc),
  });
}
export function useUpdateOwnerTag() {
  const qc = useQueryClient();
  return useMutation({ meta: { suppressGlobalError: true }, mutationFn: (owner_campaign_tag: string | null) => dataOrThrow(generatedFetchApi.PATCH("/api/settings/observer/owner-tag", { body: { owner_campaign_tag } })), onSuccess: invalidateObserver(qc) });
}
export function useToggleAutoEnable() {
  const qc = useQueryClient();
  return useMutation({ meta: { suppressGlobalError: true }, mutationFn: (enabled: boolean) => dataOrThrow(generatedFetchApi.PATCH("/api/settings/observer/auto-enable", { body: { enabled } })), onSuccess: invalidateObserver(qc) });
}

export function useAutoEnableExclusions() {
  return generatedApi.useQuery("get", "/api/settings/observer/auto-enable-exclusions", {}, { staleTime: 30_000 });
}
export function useRemoveAutoEnableExclusion() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (fbAdId: string) =>
      noContentOrThrow(
        generatedFetchApi.DELETE(
          "/api/settings/observer/auto-enable-exclusions/{fb_ad_id}",
          { params: { path: { fb_ad_id: fbAdId } } },
        ),
      ),
    onSuccess: () => void qc.invalidateQueries({ queryKey: ["get", "/api/settings/observer/auto-enable-exclusions"] }),
  });
}

export function useObserverCampaigns(includeStale = false) {
  return generatedApi.useQuery("get", "/api/settings/observer/campaigns", { params: { query: { include_stale: includeStale } } }, { staleTime: 30_000 });
}
export function useRefreshObserverCampaigns() {
  const qc = useQueryClient();
  return useMutation({
    meta: { suppressGlobalError: true },
    mutationFn: (includeStale: boolean = false) => dataOrThrow(generatedFetchApi.POST("/api/settings/observer/campaigns/refresh", { params: { query: { include_stale: includeStale } } })),
    onSuccess: () => void qc.invalidateQueries({ queryKey: ["get", "/api/settings/observer/campaigns"] }),
  });
}
export function useSetCampaignAllowlist() {
  const qc = useQueryClient();
  return useMutation({ meta: { suppressGlobalError: true }, mutationFn: (campaign_ids: string[]) => dataOrThrow(generatedFetchApi.PATCH("/api/settings/observer/campaigns", { body: { campaign_ids } })), onSuccess: invalidateObserver(qc) });
}

export function useTelegramSettings() {
  return generatedApi.useQuery("get", "/api/settings/telegram", {}, { staleTime: 30_000 });
}
export function useTelegramNotificationDiagnostics() {
  return generatedApi.useQuery("get", "/api/settings/telegram/diagnostics", {}, { staleTime: 15_000, refetchInterval: 30_000 });
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
    mutationFn: (bot_token: string) => dataOrThrow(generatedFetchApi.PUT("/api/settings/telegram/token", { body: { bot_token } })),
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
export function useUpdateVisionSettings() {
  const qc = useQueryClient();
  return useMutation({
    meta: { suppressGlobalError: true },
    mutationFn: (body: components["schemas"]["VisionSettingsUpdateRequest"]) => dataOrThrow(generatedFetchApi.PUT("/api/settings/vision", { body })),
    onSuccess: () => void qc.invalidateQueries({ queryKey: visionKey }),
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
