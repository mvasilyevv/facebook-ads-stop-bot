/**
 * api.ts — fetchJson (Bearer) + TanStack Query хуки для Mini App.
 *
 * Портировано из frontend-mini/src/api.js → TypeScript.
 * Типы берём из @fb/shared — не дублируем.
 * При 401 один раз перевыпускаем токен через loginToBackend.
 */

import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import type {
  AdSnapshot,
  DashboardBatch,
  DashboardStats,
  DraftOut,
  HealthDetails,
  HistorySummary,
  ObserverConfig,
  Offer,
} from "@fb/shared";
import { getStoredToken, loginToBackend, logout } from "./auth";

const API_BASE = (import.meta.env.VITE_API_BASE as string | undefined) ?? "/api";

// ─── Базовый fetchJson ─────────────────────────────────────────────────────

interface FetchOptions extends RequestInit {
  _retry?: boolean;
}

/**
 * Универсальный fetch с Bearer-заголовком.
 * При 401: logout → loginToBackend → повтор (один раз).
 */
export async function fetchJson<T = unknown>(
  path: string,
  opts: FetchOptions = {},
): Promise<T> {
  const { _retry = false, ...rest } = opts;
  const token = getStoredToken();
  const headers: HeadersInit = {
    "Content-Type": "application/json",
    ...(rest.headers as Record<string, string> | undefined),
  };
  if (token) {
    (headers as Record<string, string>)["Authorization"] = `Bearer ${token}`;
  }

  const resp = await fetch(`${API_BASE}${path}`, { ...rest, headers });

  if (resp.status === 401 && !_retry) {
    // Токен истёк — перевыпускаем и повторяем.
    try {
      logout();
      await loginToBackend();
      return fetchJson(path, { ...opts, _retry: true });
    } catch {
      // повторный login не помог — падаем с 401.
    }
  }

  if (!resp.ok) {
    const err = await resp.json().catch(() => ({ detail: `Ошибка ${resp.status}` }));
    throw new Error((err as { detail?: string }).detail ?? `Ошибка ${resp.status}`);
  }

  return resp.json() as Promise<T>;
}

// ─── TMA-специфические типы (из backend schemas/tma.py) ──────────────────

export interface TmaAdMetrics {
  spend: string | null;
  leads: number | null;
  deposits: number | null;
  cpc: string | null;
  ctr: string | null;
  registrations: number | null;
  cost_per_lead: string | null;
}

export interface TmaRecentAlert {
  stage: string;
  created_at: string | null;
  reason_title: string | null;
}

export interface TmaAdDetail {
  fb_ad_id: string;
  ad_name: string | null;
  campaign_name: string | null;
  adset_name: string | null;
  offer_code: string | null;
  /** Всегда UPPERCASE из TMA-API — нормализовать через normalizeAlertState(). */
  state: string;
  snooze_until: string | null;
  account_id: string | null;
  can_open_in_ads_manager: boolean;
  metrics: TmaAdMetrics;
  recent_alerts: TmaRecentAlert[];
}

export interface TmaDisableResponse {
  ok: boolean;
  task_id: number | null;
  channel: string;
  detail: string;
}

export interface TmaSnoozeResponse {
  ok: boolean;
  snoozed_until: string;
}

export interface TmaClaimResponse {
  ok: boolean;
  alert_state: string;
}

export interface TmaDraftActionResponse {
  ok: boolean;
  detail: string;
}

export interface ObserverScanNowResponse {
  ok: boolean;
}

// ─── Query-ключи ─────────────────────────────────────────────────────────

export const QK = {
  dashboardBatch: ["dashboard", "batch"] as const,
  dashboardStats: ["dashboard", "stats"] as const,
  dashboardAds: (filter?: string, search?: string) =>
    ["dashboard", "ads", filter, search] as const,
  tmaAd: (fbAdId: string) => ["tma", "ad", fbAdId] as const,
  tmaDrafts: ["tma", "drafts"] as const,
  tmaDraft: (id: number) => ["tma", "draft", id] as const,
  offers: ["offers"] as const,
  healthDetails: ["health", "details"] as const,
  historySummary: (days: number) => ["history", "summary", days] as const,
  observerSettings: ["settings", "observer"] as const,
} as const;

// ─── Dashboard ────────────────────────────────────────────────────────────

/** Батч-запрос дашборда: stats + incidents + alerts + disable + enable_recommendations. */
export function useDashboardBatch(options?: { refetchInterval?: number }) {
  return useQuery({
    queryKey: QK.dashboardBatch,
    queryFn: () => fetchJson<DashboardBatch>("/dashboard/batch"),
    refetchInterval: options?.refetchInterval ?? 30_000,
  });
}

/** 14 scalar-полей для /dashboard/stats. */
export function useDashboardStats() {
  return useQuery({
    queryKey: QK.dashboardStats,
    queryFn: () => fetchJson<DashboardStats>("/dashboard/stats"),
  });
}

/**
 * Список объявлений для AdsPage.
 * @param alertState — фильтр по alert_state (lowercase canonical), "" = все.
 * @param search — поиск по имени (фильтрация на клиенте).
 */
export function useDashboardAds(alertState = "", _search = "") {
  return useQuery({
    queryKey: QK.dashboardAds(alertState, _search),
    queryFn: async () => {
      const qs = alertState ? `?alert_state=${encodeURIComponent(alertState)}&limit=300` : "?limit=300";
      const data = await fetchJson<AdSnapshot[] | { items: AdSnapshot[] }>(`/dashboard/ads${qs}`);
      // бэк может отдать массив или объект {items: []}
      return Array.isArray(data) ? data : (data as { items: AdSnapshot[] }).items ?? [];
    },
    refetchInterval: 15_000,
  });
}

/** Точка ряда spend × час (GET /dashboard/chart-data). */
export interface ChartDataPoint {
  bucket: string;
  spend: number | string | null;
  ad_count?: number | null;
}

/**
 * Ряд spend по часам для SpendChart (number[]). Пустой ряд → график покажет
 * заглушку (без фейка). bucket=hour за последние `hours` часов.
 */
export function useSpendSeries(hours = 24) {
  return useQuery({
    queryKey: ["dashboard", "chart-data", hours] as const,
    queryFn: async () => {
      const data = await fetchJson<ChartDataPoint[] | { items: ChartDataPoint[] }>(
        `/dashboard/chart-data?hours=${hours}&bucket=hour`,
      );
      const arr = Array.isArray(data) ? data : (data as { items: ChartDataPoint[] }).items ?? [];
      return arr.map((p) => Number(p.spend) || 0);
    },
    refetchInterval: 60_000,
  });
}

// ─── TMA-действия ────────────────────────────────────────────────────────

/** Детальный снимок объявления (TMA endpoint, Bearer-guard). */
export function useTmaAd(fbAdId: string, enabled = true) {
  return useQuery({
    queryKey: QK.tmaAd(fbAdId),
    queryFn: () => fetchJson<TmaAdDetail>(`/tma/ads/${encodeURIComponent(fbAdId)}`),
    enabled: enabled && !!fbAdId,
  });
}

/** Отключить объявление. */
export function useTmaDisable() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: ({ fbAdId, reason }: { fbAdId: string; reason?: string }) =>
      fetchJson<TmaDisableResponse>(`/tma/ads/${encodeURIComponent(fbAdId)}/disable`, {
        method: "POST",
        body: JSON.stringify({ reason: reason ?? null }),
      }),
    onSuccess: (_data, { fbAdId }) => {
      void qc.invalidateQueries({ queryKey: QK.tmaAd(fbAdId) });
      void qc.invalidateQueries({ queryKey: QK.dashboardBatch });
    },
  });
}

/** Снуз объявления на N минут. */
export function useTmaSnooze() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: ({ fbAdId, minutes }: { fbAdId: string; minutes: number }) =>
      fetchJson<TmaSnoozeResponse>(`/tma/ads/${encodeURIComponent(fbAdId)}/snooze`, {
        method: "POST",
        body: JSON.stringify({ minutes }),
      }),
    onSuccess: (_data, { fbAdId }) => {
      void qc.invalidateQueries({ queryKey: QK.tmaAd(fbAdId) });
    },
  });
}

/** Claim (взять под контроль вручную). */
export function useTmaClaim() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: ({ fbAdId }: { fbAdId: string }) =>
      fetchJson<TmaClaimResponse>(`/tma/ads/${encodeURIComponent(fbAdId)}/claim`, {
        method: "POST",
        body: "{}",
      }),
    onSuccess: (_data, { fbAdId }) => {
      void qc.invalidateQueries({ queryKey: QK.tmaAd(fbAdId) });
      void qc.invalidateQueries({ queryKey: QK.dashboardBatch });
    },
  });
}

// ─── Черновики ────────────────────────────────────────────────────────────

/** Список DRAFT-задач. */
export function useTmaDrafts() {
  return useQuery({
    queryKey: QK.tmaDrafts,
    queryFn: () => fetchJson<DraftOut[]>("/tma/draft-tasks?status=DRAFT&limit=50"),
    refetchInterval: 20_000,
  });
}

/** Детали одного черновика. */
export function useTmaDraftDetail(taskId: number, enabled = true) {
  return useQuery({
    queryKey: QK.tmaDraft(taskId),
    queryFn: () => fetchJson<DraftOut>(`/tma/draft-tasks/${taskId}`),
    enabled: enabled && taskId > 0,
  });
}

/** Подтвердить черновик. */
export function useTmaConfirmDraft() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: ({ taskId }: { taskId: number }) =>
      fetchJson<TmaDraftActionResponse>(`/tma/draft-tasks/${taskId}/confirm`, {
        method: "POST",
        body: "{}",
      }),
    onSuccess: () => {
      void qc.invalidateQueries({ queryKey: QK.tmaDrafts });
    },
  });
}

/** Отклонить черновик. */
export function useTmaRejectDraft() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: ({ taskId, reason }: { taskId: number; reason?: string }) =>
      fetchJson<TmaDraftActionResponse>(`/tma/draft-tasks/${taskId}/reject`, {
        method: "POST",
        body: JSON.stringify({ reason: reason ?? null }),
      }),
    onSuccess: () => {
      void qc.invalidateQueries({ queryKey: QK.tmaDrafts });
    },
  });
}

// ─── Офферы ──────────────────────────────────────────────────────────────

export function useOffers() {
  return useQuery({
    queryKey: QK.offers,
    queryFn: () => fetchJson<Offer[]>("/offers"),
  });
}

// ─── Health ───────────────────────────────────────────────────────────────

export function useHealthDetails() {
  return useQuery({
    queryKey: QK.healthDetails,
    queryFn: () => fetchJson<HealthDetails>("/health/details"),
    refetchInterval: 60_000,
  });
}

// ─── История ─────────────────────────────────────────────────────────────

export function useHistorySummary(days = 7) {
  const to = new Date().toISOString();
  const from = new Date(Date.now() - days * 86_400_000).toISOString();
  return useQuery({
    queryKey: QK.historySummary(days),
    queryFn: () =>
      fetchJson<HistorySummary>(`/history/summary?from_iso=${encodeURIComponent(from)}&to_iso=${encodeURIComponent(to)}`),
  });
}

// ─── Observer settings ────────────────────────────────────────────────────

export function useObserverSettings() {
  return useQuery({
    queryKey: QK.observerSettings,
    queryFn: () => fetchJson<ObserverConfig>("/settings/observer"),
  });
}

/** Переключить скан. */
export function useToggleScanning() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: ({ enabled }: { enabled: boolean }) =>
      fetchJson("/settings/observer/scanning", {
        method: "PATCH",
        body: JSON.stringify({ enabled }),
      }),
    onSuccess: () => {
      void qc.invalidateQueries({ queryKey: QK.observerSettings });
    },
  });
}

/** Запустить скан сейчас. */
export function useTriggerScan() {
  return useMutation({
    mutationFn: () =>
      fetchJson<ObserverScanNowResponse>("/observer/scan-now", { method: "POST" }),
  });
}

// ─── Офферы (CRUD) ────────────────────────────────────────────────────────

import type {
  HistoryCampaign,
  HistoryOffer,
  OfferRules,
  TelegramSettings,
} from "@fb/shared";

/** Тип для создания оффера — минимальный набор полей. */
export interface OfferCreatePayload {
  code: string;
  name: string;
  vertical?: string | null;
}

/** Тип для обновления оффера. */
export interface OfferUpdatePayload {
  name?: string | null;
  vertical?: string | null;
  is_active?: boolean | null;
}

export const QK_EXT = {
  offerRules: (offerId: string) => ["offers", "rules", offerId] as const,
  historyCampaigns: (days: number) => ["history", "campaigns", days] as const,
  historyOffers: (days: number) => ["history", "offers", days] as const,
  telegramSettings: ["settings", "telegram"] as const,
  visionSettings: ["settings", "vision"] as const,
  scriptFolders: ["tools", "script-folders"] as const,
} as const;

/** Создать оффер (POST /api/offers). */
export function useCreateOffer() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (payload: OfferCreatePayload) =>
      fetchJson("/offers", {
        method: "POST",
        body: JSON.stringify(payload),
      }),
    onSuccess: () => {
      void qc.invalidateQueries({ queryKey: QK.offers });
    },
  });
}

/** Обновить оффер (PUT /api/offers/{id}). */
export function useUpdateOffer() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: ({ id, payload }: { id: string; payload: OfferUpdatePayload }) =>
      fetchJson(`/offers/${encodeURIComponent(id)}`, {
        method: "PUT",
        body: JSON.stringify(payload),
      }),
    onSuccess: () => {
      void qc.invalidateQueries({ queryKey: QK.offers });
    },
  });
}

/** Удалить оффер (DELETE /api/offers/{id}). */
export function useDeleteOffer() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: ({ id }: { id: string }) =>
      fetchJson(`/offers/${encodeURIComponent(id)}`, { method: "DELETE" }),
    onSuccess: () => {
      void qc.invalidateQueries({ queryKey: QK.offers });
    },
  });
}

/** Правила оффера (GET /api/offers/{id}/rules). */
export function useOfferRules(offerId: string, enabled = true) {
  return useQuery({
    queryKey: QK_EXT.offerRules(offerId),
    queryFn: () => fetchJson<OfferRules>(`/offers/${encodeURIComponent(offerId)}/rules`),
    enabled: enabled && !!offerId,
  });
}

/** Обновить правила оффера (PUT /api/offers/{id}/rules). */
export function useUpdateOfferRules() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: ({ offerId, payload }: { offerId: string; payload: Partial<OfferRules> }) =>
      fetchJson<OfferRules>(`/offers/${encodeURIComponent(offerId)}/rules`, {
        method: "PUT",
        body: JSON.stringify(payload),
      }),
    onSuccess: (_data, { offerId }) => {
      void qc.invalidateQueries({ queryKey: QK_EXT.offerRules(offerId) });
    },
  });
}

// ─── История (расширенные хуки) ───────────────────────────────────────────

/** История по кампаниям за N дней (GET /api/history/campaigns). */
export function useHistoryCampaigns(days = 7) {
  const to = new Date().toISOString();
  const from = new Date(Date.now() - days * 86_400_000).toISOString();
  return useQuery({
    queryKey: QK_EXT.historyCampaigns(days),
    queryFn: () =>
      fetchJson<HistoryCampaign[]>(
        `/history/campaigns?from_iso=${encodeURIComponent(from)}&to_iso=${encodeURIComponent(to)}`,
      ),
  });
}

/** История по офферам за N дней (GET /api/history/offers). */
export function useHistoryOffers(days = 7) {
  const to = new Date().toISOString();
  const from = new Date(Date.now() - days * 86_400_000).toISOString();
  return useQuery({
    queryKey: QK_EXT.historyOffers(days),
    queryFn: () =>
      fetchJson<HistoryOffer[]>(
        `/history/offers?from_iso=${encodeURIComponent(from)}&to_iso=${encodeURIComponent(to)}`,
      ),
  });
}

// ─── Настройки Telegram / Vision ─────────────────────────────────────────

/** Настройки Telegram (GET /api/settings/telegram). */
export function useTelegramSettings() {
  return useQuery({
    queryKey: QK_EXT.telegramSettings,
    queryFn: () => fetchJson<TelegramSettings>("/settings/telegram"),
  });
}

/** Обновить web_app_url (PUT /api/settings/telegram/web-app-url). */
export function useUpdateTelegramWebAppUrl() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: ({ web_app_url }: { web_app_url: string | null }) =>
      fetchJson("/settings/telegram/web-app-url", {
        method: "PUT",
        body: JSON.stringify({ web_app_url }),
      }),
    onSuccess: () => {
      void qc.invalidateQueries({ queryKey: QK_EXT.telegramSettings });
    },
  });
}

/** VisionSettings — тип для ответа бэка. */
export interface VisionSettings {
  has_token: boolean;
  profile_id: string | null;
  auto_restart_on_missing_cdp: boolean;
  runtime_status: string | null;
  runtime_status_message: string | null;
  cdp_ready: boolean;
  cdp_port: number | null;
}

/** Настройки Vision (GET /api/settings/vision). */
export function useVisionSettings() {
  return useQuery({
    queryKey: QK_EXT.visionSettings,
    queryFn: () => fetchJson<VisionSettings>("/settings/vision"),
  });
}

/** Обновить profile_id (PUT /api/settings/vision). */
export function useUpdateVisionSettings() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: ({ profile_id, x_token }: { profile_id?: string | null; x_token?: string | null }) =>
      fetchJson("/settings/vision", {
        method: "PUT",
        body: JSON.stringify({ profile_id, x_token }),
      }),
    onSuccess: () => {
      void qc.invalidateQueries({ queryKey: QK_EXT.visionSettings });
    },
  });
}

// ─── Скрипты / папки с креативами ────────────────────────────────────────

/** Тип папки с креативами. */
export interface ScriptFolder {
  name: string;
  path: string;
  adset_count: number;
  creative_count: number;
  media_type: string;
  updated_at: number;
  is_valid: boolean;
  validation_error: string;
}

/** Тип плана кампании. */
export interface ScriptPlan {
  campaign_name: string;
  offer_code: string;
  offer_country_name: string;
  creative_folder_name: string;
  creative_folder_path: string;
  conversion_event: string;
  cabinet_id: string;
  sub2: string;
  media_type: string;
  adset_count: number;
  ad_count: number;
  adsets: unknown[];
  location_plan: unknown;
  manual_guide: Array<{
    title: string;
    items: Array<{ label: string; value: string; copyable: boolean }>;
  }>;
  safety_notes: string[];
}

/** Тип для запроса плана. */
export interface ScriptPlanRequest {
  offer_code: string;
  offer_country_name: string;
  cabinet_id: string;
  sub2: string;
  folder_name: string;
  generation_date?: string | null;
}

/** Список папок с креативами (GET /api/tools/campaign-create/folders). */
export function useScriptFolders() {
  return useQuery({
    queryKey: QK_EXT.scriptFolders,
    queryFn: () => fetchJson<ScriptFolder[]>("/tools/campaign-create/folders"),
  });
}

/** Построить план кампании (POST /api/tools/campaign-create/plan). */
export function useScriptPlan() {
  return useMutation({
    mutationFn: (payload: ScriptPlanRequest) =>
      fetchJson<ScriptPlan>("/tools/campaign-create/plan", {
        method: "POST",
        body: JSON.stringify(payload),
      }),
  });
}
