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
  HealthDetails,
  HistorySummary,
  ObserverConfig,
  ObserverStatus,
  Offer,
  StatsPeriod,
  StatsToday,
} from "@fb/shared";
import type { components } from "@fb/shared/api/generated";
import { getStoredToken, loginToBackend, logout } from "./auth";

type RulePreviewOut = components["schemas"]["RulePreviewOut"];

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
  creative_thumb_url: string | null;
  creative_image_url: string | null;
  metrics: TmaAdMetrics;
  recent_alerts: TmaRecentAlert[];
}

export interface TmaDisableResponse {
  ok: boolean;
  task_id: number | null;
  channel: string;
  detail: string;
}

export interface TmaClaimResponse {
  ok: boolean;
  alert_state: string;
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
  offers: ["offers"] as const,
  healthDetails: ["health", "details"] as const,
  historySummary: (days: number) => ["history", "summary", days] as const,
  observerSettings: ["settings", "observer"] as const,
  cabinetAutostart: ["tma", "cabinet-autostart"] as const,
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
 * заглушку (без фейка). bucket=hour, cabinet_day=true — окно с 00:00 кабинета.
 */
export function useSpendSeries(hours = 24) {
  return useQuery({
    queryKey: ["dashboard", "chart-data", hours] as const,
    queryFn: async () => {
      const data = await fetchJson<ChartDataPoint[] | { items: ChartDataPoint[] }>(
        `/dashboard/chart-data?hours=${hours}&bucket=hour&cabinet_day=true`,
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

// ─── Офферы ──────────────────────────────────────────────────────────────

export function useOffers() {
  return useQuery({
    queryKey: QK.offers,
    queryFn: () => fetchJson<Offer[]>("/offers"),
  });
}

// ─── Таймзона рекламного кабинета ──────────────────────────────────────────

/**
 * Ответ GET /campaigns/ad-account-timezone. Тип локальный (не из @fb/shared/api
 * generated — gen:api требует живого бэка). `tz_offset_hours` может быть < 0
 * (напр. -7 для America/Hermosillo). Деньги: это часы для start_time кабинета.
 */
export interface AdAccountTimezoneResponse {
  tz_offset_hours: number;
  tz_offset_str: string;
  timezone_name: string;
}

/**
 * Фетчит TZ кабинета (зафиксирована при создании, неизменна) по act_id.
 * `enabled` гейтит запрос (вызываем только после blur с непустым act_id).
 * 503 — browser-agent/Vision недоступны, 422 — Meta-ошибка/кабинет не найден.
 */
export function useAdAccountTimezone(actId: string, enabled: boolean) {
  const trimmed = actId.trim();
  return useQuery({
    queryKey: ["campaigns", "ad-account-timezone", trimmed] as const,
    queryFn: () =>
      fetchJson<AdAccountTimezoneResponse>(
        `/campaigns/ad-account-timezone?act_id=${encodeURIComponent(trimmed)}`,
      ),
    enabled: enabled && trimmed.length > 0,
    retry: false,
    staleTime: 60 * 60 * 1000, // TZ статична — кэшируем час
  });
}

// ─── Страницы рекламного кабинета (для выбора page_id) ─────────────────────

/**
 * Ответ GET /campaigns/ad-account-pages. Тип локальный (не из @fb/shared/api
 * generated — gen:api требует живого бэка). `pages` может быть пустым массивом
 * (нет привязанных страниц или нет прав) — тогда фронт оставляет ручной ввод.
 */
export interface AdAccountPagesResponse {
  pages: { id: string; name: string }[];
}

/**
 * Фетчит страницы (promote_pages) кабинета по act_id для дропдауна page_id.
 * `enabled` гейтит запрос (вызываем только после blur с непустым act_id) — тот же
 * триггер, что у useAdAccountTimezone. 503 — browser-agent/Vision недоступны,
 * 422 — Meta-ошибка/кабинет не найден. При ошибке/пустом массиве фронт даёт
 * ручной ввод page_id.
 */
export function useAdAccountPages(actId: string, enabled: boolean) {
  const trimmed = actId.trim();
  return useQuery({
    queryKey: ["campaigns", "ad-account-pages", trimmed] as const,
    queryFn: () =>
      fetchJson<AdAccountPagesResponse>(
        `/campaigns/ad-account-pages?act_id=${encodeURIComponent(trimmed)}`,
      ),
    enabled: enabled && trimmed.length > 0,
    retry: false,
    staleTime: 60 * 60 * 1000, // список страниц меняется редко — кэшируем час
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

// ─── Статистика залива ─────────────────────────────────────────────────────

/** Экспортируем типы контракта — StatsPeriodDays используют компоненты экрана /stats. */
export type StatsPeriodDays = 7 | 30;

/**
 * Воронка текущих суток кабинета (GET /stats/today): тоталы + производные +
 * честные почасовые дельты + блок трекера. Умеренный refetch — не money-critical.
 */
export function useStatsToday() {
  return useQuery({
    queryKey: ["stats", "today"] as const,
    queryFn: () => fetchJson<StatsToday>("/stats/today"),
    refetchInterval: 45_000,
  });
}

/**
 * Воронка за период (GET /stats/period): 7 или 30 дней от текущего момента.
 * from_iso считается на клиенте (now - days*86400000), to_iso — сейчас.
 */
export function useStatsPeriod(days: StatsPeriodDays) {
  const to = new Date().toISOString();
  const from = new Date(Date.now() - days * 86_400_000).toISOString();
  return useQuery({
    queryKey: ["stats", "period", days] as const,
    queryFn: () =>
      fetchJson<StatsPeriod>(
        `/stats/period?from_iso=${encodeURIComponent(from)}&to_iso=${encodeURIComponent(to)}`,
      ),
    refetchInterval: 60_000,
  });
}

// ─── Observer settings ────────────────────────────────────────────────────

export function useObserverSettings() {
  return useQuery({
    queryKey: QK.observerSettings,
    queryFn: () => fetchJson<ObserverConfig>("/settings/observer"),
  });
}

/**
 * Статус observer-воркера из Redis observer:runtime (GET /observer/status).
 * MID-22 аудита 02.07: даёт `extra.next_scan_at`/`extra.scan_mode` — тот же
 * источник, что использует web (useObserverStatus в frontend/src/lib/api/settings.ts),
 * для РЕАЛЬНОГО адаптивного отсчёта вместо локального таймера по default_interval_seconds.
 */
export function useObserverStatus() {
  return useQuery({
    queryKey: ["observer", "status"] as const,
    queryFn: () => fetchJson<ObserverStatus>("/observer/status"),
    staleTime: 10_000,
    refetchInterval: 20_000,
  });
}

/** Конфиг автостарта кабинета (owner-gated на запись через /tma/cabinet-autostart). */
export interface CabinetAutostart {
  enabled: boolean;
  hour_utc: number;
  minute_utc: number;
}

export function useCabinetAutostart() {
  return useQuery({
    queryKey: QK.cabinetAutostart,
    queryFn: () => fetchJson<CabinetAutostart>("/tma/cabinet-autostart"),
    staleTime: 30_000,
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

/**
 * OfferExt — локальное расширение @fb/shared Offer (OfferOut) money-полями,
 * которых пока нет в generated-типах (gen:api НЕ запускаем). Бэк OfferOut уже
 * отдаёт их — читаем из useOffers().data через этот тип:
 *   - ad_account_ids / pixel_id — уже в generated, дублируем для строгости.
 *   - countries (ISO-2 upper, дефолт []) — НОВОЕ.
 */
export type OfferExt = Offer & {
  ad_account_ids?: string[];
  pixel_id?: string | null;
  countries?: string[];
};

/** Тип для создания оффера — минимальный набор полей. */
export interface OfferCreatePayload {
  code: string;
  name: string;
  vertical?: string | null;
  /** Мульти-кабинет: кабинеты оффера (числовые ID без act_), минимум 1 — бэк отдаёт 422 без них. */
  ad_account_ids: string[];
  /** Гео оффера (ISO-2 upper). Дефолт [] — не задано. */
  countries?: string[];
}

/** Тип для обновления оффера. */
export interface OfferUpdatePayload {
  name?: string | null;
  vertical?: string | null;
  is_active?: boolean | null;
  /** Мульти-кабинет: undefined — не трогать, список — заменить (минимум 1). */
  ad_account_ids?: string[];
  /** Гео оффера (ISO-2 upper): undefined — не трогать, список — заменить. */
  countries?: string[];
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

/**
 * GET /offers/rules/preview — рассчитывает $-пороги (CPC/CPL/CPR + spend-диапазоны)
 * из CPA и процентов чувствительности. Портировано из frontend/src/lib/api/offers.ts
 * (MID-21 аудита 02.07, паритет с web): тот же RuleContext, что и у observer'а, —
 * значения в preview ТОЧНО совпадают с реальными стоп-порогами.
 * enabled только при cpa > 0; placeholderData держит прошлый результат, чтобы
 * блок не мигал при движении ползунка.
 */
export function useRulesPreview(params: {
  cpa: number | null;
  stop_percent_of_rule: number;
  warning_percent_of_stop: number;
}) {
  const enabled = params.cpa != null && params.cpa > 0;
  const qs = new URLSearchParams({
    cpa: String(params.cpa ?? ""),
    stop_percent_of_rule: String(params.stop_percent_of_rule),
    warning_percent_of_stop: String(params.warning_percent_of_stop),
  });
  return useQuery({
    queryKey: ["offers", "rules", "preview", params] as const,
    queryFn: () => fetchJson<RulePreviewOut>(`/offers/rules/preview?${qs.toString()}`),
    enabled,
    staleTime: 60_000,
    placeholderData: (prev) => prev,
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

// ─── Campaign creation API ────────────────────────────────────────────────

import type {
  CampaignPreset,
  PresetCreatePayload,
  UploadConceptsResponse,
  CampaignConfig,
  ValidatePlanResponse,
  LaunchPayload,
  LaunchResponse,
  CampaignRunSummary,
  CampaignRunDetail,
} from "./campaignTypes";
import { TERMINAL_STATUSES } from "./campaignTypes";

export type {
  CampaignPreset,
  PresetCreatePayload,
  UploadConceptsResponse,
  UploadedConcept,
  CampaignConfig,
  CampaignSpec,
  ValidatePlanResponse,
  CampaignPlan,
  AdsetPlan,
  LaunchPayload,
  LaunchResponse,
  CampaignRunSummary,
  CampaignRunDetail,
} from "./campaignTypes";
export type { WizardStep, CampaignRunStatus } from "./campaignTypes";
export { RUN_STATUS_LABEL, TERMINAL_STATUSES, WIZARD_STEPS, WIZARD_STEP_LABEL } from "./campaignTypes";

export const QK_CAMPAIGN = {
  presets: ["campaigns", "presets"] as const,
  runs: ["campaigns", "runs"] as const,
  run: (id: string) => ["campaigns", "run", id] as const,
} as const;

/** Список пресетов (GET /api/tools/campaigns/presets). */
export function useCampaignPresets() {
  return useQuery({
    queryKey: QK_CAMPAIGN.presets,
    queryFn: () => fetchJson<CampaignPreset[]>("/tools/campaigns/presets"),
    staleTime: 30_000,
  });
}

/** Создать пресет (POST /api/tools/campaigns/presets). */
export function useCreatePreset() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (payload: PresetCreatePayload) =>
      fetchJson<CampaignPreset>("/tools/campaigns/presets", {
        method: "POST",
        body: JSON.stringify(payload),
      }),
    onSuccess: () => {
      void qc.invalidateQueries({ queryKey: QK_CAMPAIGN.presets });
    },
  });
}

/** Обновить пресет (PUT /api/tools/campaigns/presets/{id}). */
export function useUpdatePreset() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: ({ id, payload }: { id: string; payload: Partial<PresetCreatePayload> }) =>
      fetchJson<CampaignPreset>(`/tools/campaigns/presets/${encodeURIComponent(id)}`, {
        method: "PUT",
        body: JSON.stringify(payload),
      }),
    onSuccess: () => {
      void qc.invalidateQueries({ queryKey: QK_CAMPAIGN.presets });
    },
  });
}

/** Удалить пресет (DELETE /api/tools/campaigns/presets/{id}). */
export function useDeletePreset() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: ({ id }: { id: string }) =>
      fetchJson(`/tools/campaigns/presets/${encodeURIComponent(id)}`, { method: "DELETE" }),
    onSuccess: () => {
      void qc.invalidateQueries({ queryKey: QK_CAMPAIGN.presets });
    },
  });
}

/**
 * Загрузить концепты (POST /api/tools/campaigns/upload, multipart).
 * Принимает FormData напрямую — fetchJson с кастомными заголовками.
 */
export function useUploadConcepts() {
  return useMutation({
    mutationFn: async (formData: FormData): Promise<UploadConceptsResponse> => {
      const token = getStoredToken();
      const headers: Record<string, string> = {};
      if (token) headers["Authorization"] = `Bearer ${token}`;
      const resp = await fetch(`${API_BASE}/tools/campaigns/upload`, {
        method: "POST",
        headers,
        body: formData,
      });
      if (!resp.ok) {
        const err = await resp.json().catch(() => ({ detail: `Ошибка ${resp.status}` }));
        throw new Error((err as { detail?: string }).detail ?? `Ошибка ${resp.status}`);
      }
      return resp.json() as Promise<UploadConceptsResponse>;
    },
  });
}

/** Dry-run валидация конфига (POST /api/tools/campaigns/validate). */
export function useValidateCampaign() {
  return useMutation({
    mutationFn: (config: CampaignConfig) =>
      fetchJson<ValidatePlanResponse>("/tools/campaigns/validate", {
        method: "POST",
        body: JSON.stringify({ config }),
      }),
  });
}

/** Запустить залив (POST /api/tools/campaigns/launch). */
export function useLaunchCampaign() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (payload: LaunchPayload) =>
      fetchJson<LaunchResponse>("/tools/campaigns/launch", {
        method: "POST",
        body: JSON.stringify(payload),
      }),
    onSuccess: () => {
      void qc.invalidateQueries({ queryKey: QK_CAMPAIGN.runs });
    },
  });
}

/** Список запусков (GET /api/tools/campaigns/runs). */
export function useCampaignRuns() {
  return useQuery({
    queryKey: QK_CAMPAIGN.runs,
    queryFn: () => fetchJson<CampaignRunSummary[]>("/tools/campaigns/runs"),
    refetchInterval: 15_000,
  });
}

/** Детали одного запуска (GET /api/tools/campaigns/runs/{id}). */
export function useCampaignRun(id: string, enabled = true) {
  return useQuery({
    queryKey: QK_CAMPAIGN.run(id),
    queryFn: () => fetchJson<CampaignRunDetail>(`/tools/campaigns/runs/${encodeURIComponent(id)}`),
    enabled: enabled && !!id,
    refetchInterval: (query) => {
      // Продолжаем поллинг пока статус не финальный
      const status = (query.state.data as CampaignRunDetail | undefined)?.status;
      if (!status) return 3_000;
      return TERMINAL_STATUSES.has(status as import("./campaignTypes").CampaignRunStatus) ? false : 3_000;
    },
  });
}

/** Отмена запуска (POST /api/tools/campaigns/runs/{id}/cancel). */
export function useCancelRun() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: ({ id }: { id: string }) =>
      fetchJson(`/tools/campaigns/runs/${encodeURIComponent(id)}/cancel`, { method: "POST" }),
    onSuccess: (_data, { id }) => {
      void qc.invalidateQueries({ queryKey: QK_CAMPAIGN.run(id) });
      void qc.invalidateQueries({ queryKey: QK_CAMPAIGN.runs });
    },
  });
}

/** Клон запуска (POST /api/tools/campaigns/runs/{id}/clone). */
export function useCloneRun() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: ({ id }: { id: string }) =>
      fetchJson<CampaignRunDetail>(`/tools/campaigns/runs/${encodeURIComponent(id)}/clone`, {
        method: "POST",
      }),
    onSuccess: () => {
      void qc.invalidateQueries({ queryKey: QK_CAMPAIGN.runs });
    },
  });
}

/** Cleanup (POST /api/tools/campaigns/runs/{id}/cleanup). */
export function useCleanupRun() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: ({ id }: { id: string }) =>
      fetchJson<{ run_id: string; meta_ids: Record<string, unknown>; detail: string }>(
        `/tools/campaigns/runs/${encodeURIComponent(id)}/cleanup`,
        { method: "POST" },
      ),
    onSuccess: (_data, { id }) => {
      void qc.invalidateQueries({ queryKey: QK_CAMPAIGN.run(id) });
      void qc.invalidateQueries({ queryKey: QK_CAMPAIGN.runs });
    },
  });
}
