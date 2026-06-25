/**
 * API-хуки для сервиса создания кампаний.
 *
 * Эндпоинты:
 *   GET/POST/PUT/DELETE /api/tools/campaigns/presets[/{id}]
 *   POST   /api/tools/campaigns/upload
 *   POST   /api/tools/campaigns/validate
 *   POST   /api/tools/campaigns/launch
 *   GET    /api/tools/campaigns/runs
 *   GET    /api/tools/campaigns/runs/{id}
 *   POST   /api/tools/campaigns/runs/{id}/clone
 *   POST   /api/tools/campaigns/runs/{id}/cancel
 *   POST   /api/tools/campaigns/runs/{id}/cleanup
 */

import { useQuery, useMutation, useQueryClient } from "@tanstack/react-query";
import { useAuthStore } from "@/stores/auth";
import { apiGet, apiGetWithCount, apiSend } from "./client";

// ─── Типы (сматчены с schemas/campaigns_create.py) ───────────────────────────

export interface PresetOut {
  id: string;
  name: string;
  act_id: string;
  page_id: string;
  pixel_id: string;
  tz_offset: number;
  offer_code: string | null;
  byer_tag: string | null;
  objective: string;
  optimization_goal: string;
  custom_event_type: string;
  special_ad_categories: string[];
  cta: string;
  text_optimizations: string;
  click_through_days: number;
  view_through_days: number;
  url_tags_template: string | null;
  naming_template: string | null;
  extra: Record<string, unknown>;
  created_at: string;
  updated_at: string;
}

export interface PresetIn {
  name: string;
  act_id: string;
  page_id: string;
  pixel_id: string;
  tz_offset?: number;
  offer_code?: string | null;
  byer_tag?: string | null;
  objective?: string;
  optimization_goal?: string;
  custom_event_type?: string;
  special_ad_categories?: string[];
  cta?: string;
  text_optimizations?: string;
  click_through_days?: number;
  view_through_days?: number;
  url_tags_template?: string | null;
  naming_template?: string | null;
  extra?: Record<string, unknown>;
}

export interface UploadedConceptOut {
  ref: string;
  original_name: string;
  size_bytes: number;
  content_type: string | null;
}

export interface UploadConceptsOut {
  upload_id: string;
  upload_dir: string;
  concepts: UploadedConceptOut[];
  total_bytes: number;
}

// CampaignConfig — полный конфиг залива (контракт с бэком CampaignConfig pydantic)
export interface CampaignStructure {
  /** Ключ кампании (уникальный в рамках конфига, напр. "static1" или "video1") */
  key: string;
  /** Тип медиа (image / video) */
  kind: "image" | "video";
  /** Число adset'ов в этой кампании */
  adset_count: number;
  /** Привязанные ref концептов (из upload_id) */
  concept_refs: string[];
}

export interface AdTextConfig {
  mode: "none" | "text";
  primary?: string;
}

export interface CampaignConfig {
  /** ID рекламного кабинета (act_XXXXX) */
  act_id: string;
  /** FB Page ID */
  page_id: string;
  /** FB Pixel ID */
  pixel_id: string;
  /** Timezone offset в часах */
  tz_offset?: number;
  /** Код оффера */
  offer_code: string;
  /** Тег байера */
  byer_tag?: string | null;
  /** Цель (OUTCOME_SALES) */
  objective?: string;
  /** Цель оптимизации (OFFSITE_CONVERSIONS) */
  optimization_goal?: string;
  /** Custom event type (PURCHASE) */
  custom_event_type?: string;
  /** Спецкатегории */
  special_ad_categories?: string[];
  /** Целевая ссылка (трекинг) */
  destination_link: string;
  /** CTA (PLAY_GAME) */
  cta?: string;
  /** Text optimizations (OPT_OUT) */
  text_optimizations?: string;
  /** Дата старта (YYYY-MM-DD, дефолт сегодня+1) */
  start_date: string;
  /** Текст объявления */
  ad_text?: AdTextConfig;
  /** CBO или ABO */
  budget_level?: "campaign" | "adset";
  /** Бюджет в центах */
  daily_budget_cents: number;
  /** Целевой CPA в центах (bid_amount для COST_CAP) */
  bid_amount_cents?: number;
  /** Стратегия ставки */
  bid_strategy?: string;
  /** Страны (+ AQ авто) */
  countries: string[];
  /** Мин. возраст */
  age_min?: number;
  /** Макс. возраст */
  age_max?: number;
  /** Advantage+ audience */
  advantage_audience?: boolean;
  /** Click-through attribution (дни) */
  click_through_days?: number;
  /** View-through attribution (дни) */
  view_through_days?: number;
  /** URL теги (sub2…sub7) */
  url_tags?: string | null;
  /** Шаблон нейминга */
  naming_template?: string | null;
  /** Структура кампаний */
  campaigns: CampaignStructure[];
  /** Число копий на концепт (дефолт = adset_count) */
  copies_per_concept?: number;
  /** upload_id из /upload */
  creo_root?: string | null;
  /** Статус при создании */
  launch_state?: "campaign_paused" | "all_paused";
}

export interface AdsetPlanOut {
  name: string;
  status: string;
  ad_count: number;
}

export interface CampaignPlanOut {
  key: string;
  name: string;
  kind: string;
  status: string;
  adsets: AdsetPlanOut[];
}

export interface ValidatePlanOut {
  offer_code: string;
  launch_state: string;
  copies_per_concept: number;
  campaign_count: number;
  adset_count: number;
  ad_count: number;
  campaigns: CampaignPlanOut[];
}

export interface LaunchIn {
  config: CampaignConfig;
  preset_id?: string | null;
  idempotency_key?: string | null;
}

export interface LaunchOut {
  run_id: string;
  task_id: number | null;
  status: string;
  idempotency_key: string;
}

export interface RunSummaryOut {
  id: string;
  preset_id: string | null;
  status: RunStatus;
  offer_code: string | null;
  idempotency_key: string | null;
  error: string | null;
  created_at: string;
  updated_at: string;
}

export interface RunDetailOut {
  id: string;
  preset_id: string | null;
  status: RunStatus;
  config: Record<string, unknown>;
  progress: Record<string, unknown>;
  created_meta_ids: Record<string, unknown>;
  error: string | null;
  idempotency_key: string | null;
  created_at: string;
  updated_at: string;
}

export interface CleanupOut {
  run_id: string;
  meta_ids: Record<string, unknown>;
  detail: string;
}

/** Статусы run в жизненном цикле залива. Определяем локально (TODO: консолидировать в @fb/shared). */
export type RunStatus =
  | "queued"
  | "uniquifying"
  | "uploading"
  | "creating"
  | "succeeded"
  | "failed"
  | "cancelled";

/** Терминальные статусы (poll может остановиться). */
export const TERMINAL_RUN_STATUSES: RunStatus[] = ["succeeded", "failed", "cancelled"];

/** Статусы, в которых можно отменить. */
export const CANCELLABLE_RUN_STATUSES: RunStatus[] = ["queued", "uniquifying", "uploading"];

/** Лейблы статусов на русском. */
export const RUN_STATUS_LABELS: Record<RunStatus, string> = {
  queued: "В очереди",
  uniquifying: "Уникализация",
  uploading: "Загрузка",
  creating: "Создание",
  succeeded: "Готово",
  failed: "Ошибка",
  cancelled: "Отменено",
};

// ─── Ad Account Timezone (авто-подхват) ──────────────────────────────────────

/**
 * Ответ GET /campaigns/ad-account-timezone. Определяем тип ЛОКАЛЬНО (не из generated):
 * эндпоинт читается без живого бэка при gen:api.
 */
export interface AdAccountTimezoneOut {
  /** Смещение в часах от UTC; МОЖЕТ быть отрицательным (напр. -7 для America/Hermosillo). */
  tz_offset_hours: number;
  /** Готовая строка вида "±HH:00" для start_time (напр. "-07:00", "+03:00"). */
  tz_offset_str: string;
  /** Имя таймзоны кабинета (напр. "America/New_York"). */
  timezone_name: string;
}

/**
 * Подтягивает таймзону рекламного кабинета по act_id.
 * TZ кабинета зафиксирована при создании и неизменна — тянем её из Graph через бэк.
 * act_id принимается с префиксом act_ или без (бэк нормализует).
 */
export function useAdAccountTimezone() {
  return useMutation<AdAccountTimezoneOut, Error, string>({
    mutationFn: (actId: string) =>
      apiGet<AdAccountTimezoneOut>("/campaigns/ad-account-timezone", { act_id: actId }),
  });
}

// ─── Ad Account Pages (дропдаун страниц) ─────────────────────────────────────

/**
 * Ответ GET /campaigns/ad-account-pages. Тип определяем ЛОКАЛЬНО (не из generated):
 * эндпоинт читается без живого бэка при gen:api. Массив может быть пустым.
 */
export interface AdAccountPagesOut {
  pages: { id: string; name: string }[];
}

/**
 * Подтягивает список FB-страниц (promote_pages), привязанных к кабинету, по act_id.
 * Нужен для дропдауна выбора page_id в визарде — байер выбирает страницу из списка,
 * а не вводит ID руками. На ошибке/пустом массиве фронт откатывается на ручной ввод.
 * act_id принимается с префиксом act_ или без (бэк нормализует).
 */
export function useAdAccountPages() {
  return useMutation<AdAccountPagesOut, Error, string>({
    mutationFn: (actId: string) =>
      apiGet<AdAccountPagesOut>("/campaigns/ad-account-pages", { act_id: actId }),
  });
}

// ─── Presets ──────────────────────────────────────────────────────────────────

export function usePresets() {
  return useQuery<PresetOut[]>({
    queryKey: ["campaigns", "presets"],
    queryFn: ({ signal }) => apiGet<PresetOut[]>("/tools/campaigns/presets", undefined, signal),
    staleTime: 30_000,
  });
}

export function useCreatePreset() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (data: PresetIn) => apiSend<PresetOut>("POST", "/tools/campaigns/presets", data),
    onSuccess: () => qc.invalidateQueries({ queryKey: ["campaigns", "presets"] }),
  });
}

export function useUpdatePreset(presetId: string) {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (data: PresetIn) =>
      apiSend<PresetOut>("PUT", `/tools/campaigns/presets/${presetId}`, data),
    onSuccess: () => qc.invalidateQueries({ queryKey: ["campaigns", "presets"] }),
  });
}

export function useDeletePreset() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (presetId: string) =>
      apiSend<null>("DELETE", `/tools/campaigns/presets/${presetId}`),
    onSuccess: () => qc.invalidateQueries({ queryKey: ["campaigns", "presets"] }),
  });
}

// ─── Upload ───────────────────────────────────────────────────────────────────

/**
 * Загружает файлы концептов через multipart/form-data.
 * Возвращает upload_id + список refs.
 */
export async function uploadConcepts(files: File[]): Promise<UploadConceptsOut> {
  const fd = new FormData();
  for (const f of files) {
    fd.append("files", f);
  }
  // multipart нельзя гнать через apiSend (он шлёт JSON) — fetch напрямую c BASE=/api.
  // X-API-Key берём из Zustand-стора тем же ESM-путём, что и client.ts (HIGH-5:
  // прежний CommonJS require в ESM падал ReferenceError → apiKey=null → 401).
  const apiKey = useAuthStore.getState().apiKey;

  const headers: Record<string, string> = {};
  if (apiKey) headers["X-API-Key"] = apiKey;

  const resp = await fetch("/api/tools/campaigns/upload", {
    method: "POST",
    headers,
    body: fd,
    cache: "no-store",
  });
  if (!resp.ok) {
    const text = await resp.text().catch(() => "");
    throw new Error(`Upload failed ${resp.status}: ${text}`);
  }
  return resp.json() as Promise<UploadConceptsOut>;
}

// ─── Validate ─────────────────────────────────────────────────────────────────

export function useValidateConfig() {
  return useMutation({
    mutationFn: (config: CampaignConfig) =>
      apiSend<ValidatePlanOut>("POST", "/tools/campaigns/validate", { config }),
  });
}

// ─── Launch ───────────────────────────────────────────────────────────────────

export function useLaunchCampaign() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (body: LaunchIn) => apiSend<LaunchOut>("POST", "/tools/campaigns/launch", body),
    onSuccess: () => qc.invalidateQueries({ queryKey: ["campaigns", "runs"] }),
  });
}

// ─── Runs ─────────────────────────────────────────────────────────────────────

export function useRuns(params?: { status?: string; limit?: number; offset?: number }) {
  return useQuery<{ data: RunSummaryOut[]; total: number | null }>({
    queryKey: ["campaigns", "runs", params],
    queryFn: ({ signal }) =>
      apiGetWithCount<RunSummaryOut[]>(
        "/tools/campaigns/runs",
        {
          ...(params?.status ? { status: params.status } : {}),
          limit: params?.limit ?? 50,
          offset: params?.offset ?? 0,
        },
        signal,
      ),
    staleTime: 15_000,
  });
}

export function useRunDetail(runId: string | null, options?: { refetchInterval?: number | false }) {
  return useQuery<RunDetailOut>({
    queryKey: ["campaigns", "runs", runId],
    queryFn: ({ signal }) =>
      apiGet<RunDetailOut>(`/tools/campaigns/runs/${runId}`, undefined, signal),
    enabled: !!runId,
    staleTime: 5_000,
    refetchInterval: options?.refetchInterval,
  });
}

export function useCloneRun() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (runId: string) =>
      apiSend<LaunchOut>("POST", `/tools/campaigns/runs/${runId}/clone`),
    onSuccess: () => qc.invalidateQueries({ queryKey: ["campaigns", "runs"] }),
  });
}

export function useCancelRun() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (runId: string) =>
      apiSend<RunSummaryOut>("POST", `/tools/campaigns/runs/${runId}/cancel`),
    onSuccess: () => qc.invalidateQueries({ queryKey: ["campaigns", "runs"] }),
  });
}

export function useCleanupRun() {
  return useMutation({
    mutationFn: (runId: string) =>
      apiSend<CleanupOut>("POST", `/tools/campaigns/runs/${runId}/cleanup`),
  });
}
