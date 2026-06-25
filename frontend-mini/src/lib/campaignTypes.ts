/**
 * Локальные типы для сервиса создания кампаний (frontend-mini).
 * Зеркалит schemas/campaigns_create.py. TODO: консолидировать в @fb/shared после gen:api.
 */

// ─── Статусы ──────────────────────────────────────────────────────────────

/** Статус campaign_run (queued → uniquifying → uploading → creating → succeeded | failed). */
export type CampaignRunStatus =
  | "queued"
  | "uniquifying"
  | "uploading"
  | "creating"
  | "succeeded"
  | "failed"
  | "cancelled";

/** Человекочитаемые лейблы статуса. */
export const RUN_STATUS_LABEL: Record<CampaignRunStatus, string> = {
  queued: "В очереди",
  uniquifying: "Уникализация",
  uploading: "Загрузка",
  creating: "Создание",
  succeeded: "Готово",
  failed: "Ошибка",
  cancelled: "Отменён",
};

/** Финальные статусы — поллинг можно остановить. */
export const TERMINAL_STATUSES = new Set<CampaignRunStatus>(["succeeded", "failed", "cancelled"]);

// ─── Пресеты ─────────────────────────────────────────────────────────────

export interface CampaignPreset {
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

export interface PresetCreatePayload {
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

// ─── Загрузка концептов ───────────────────────────────────────────────────

export interface UploadedConcept {
  ref: string;
  original_name: string;
  size_bytes: number;
  content_type: string | null;
}

export interface UploadConceptsResponse {
  upload_id: string;
  upload_dir: string;
  concepts: UploadedConcept[];
  total_bytes: number;
}

// ─── Конфиг кампании (CampaignConfig из builder) ─────────────────────────

/** Описание одного adset в структуре. */
export interface AdsetSpec {
  name?: string | null;
  // расширяется при необходимости
}

/** Описание одной кампании в структуре. */
export interface CampaignSpec {
  key: string;
  /** Необязательная метка для различения кампаний в имени (напр. «CR2 / тест-A»). */
  label?: string | null;
  adset_count: number;
  /** Ссылки на загруженные концепты (refs из /upload). Заполняется на шаге Креативы. */
  concept_refs?: string[];
  adsets?: AdsetSpec[];
}

/** Конфиг залива — единый контракт API↔воркер. */
export interface CampaignConfig {
  act_id: string;
  page_id: string;
  pixel_id: string;
  /** Часовой сдвиг кабинета (часы, м.б. отрицательным). Бэк сам int→`±HH:00`. */
  tz_offset?: number;
  /** IANA-имя TZ кабинета (для показа в UI; в start_time не идёт). */
  timezone_name?: string | null;
  offer_code: string;
  byer_tag?: string | null;
  start_date?: string | null; // YYYY-MM-DD, дефолт today+1
  destination_link: string;
  daily_budget_cents?: number | null; // каноническое имя (выровнено с web)
  budget_level?: "campaign" | "adset";
  countries?: string[];
  age_min?: number;
  age_max?: number;
  launch_state?: "campaign_paused" | "all_paused";
  copies_per_concept?: number | null;
  creo_root?: string | null; // upload_id из /upload
  campaigns: CampaignSpec[];
  // advanced (опционально)
  objective?: string;
  optimization_goal?: string;
  custom_event_type?: string;
  cta?: string;
  text_optimizations?: string;
  click_through_days?: number;
  view_through_days?: number;
  url_tags?: string | null;
  bid_strategy?: string | null;
  /** Целевой CPA (bid_amount) в центах — обязателен для COST_CAP. */
  bid_amount_cents?: number | null;
  advantage_audience?: boolean;
  ad_text?: { mode: "none" | "text"; primary?: string } | null;
}

// ─── Validate ─────────────────────────────────────────────────────────────

export interface AdsetPlan {
  name: string;
  status: string;
  ad_count: number;
}

export interface CampaignPlan {
  key: string;
  name: string;
  status: string;
  adsets: AdsetPlan[];
}

export interface ValidatePlanResponse {
  offer_code: string;
  launch_state: string;
  copies_per_concept: number;
  campaign_count: number;
  adset_count: number;
  ad_count: number;
  campaigns: CampaignPlan[];
}

// ─── Launch ───────────────────────────────────────────────────────────────

export interface LaunchPayload {
  config: CampaignConfig;
  preset_id?: string | null;
  idempotency_key?: string | null;
}

export interface LaunchResponse {
  run_id: string;
  task_id: number | null;
  status: string;
  idempotency_key: string;
}

// ─── Runs ─────────────────────────────────────────────────────────────────

export interface CampaignRunSummary {
  id: string;
  preset_id: string | null;
  status: CampaignRunStatus;
  offer_code: string | null;
  idempotency_key: string | null;
  error: string | null;
  created_at: string;
  updated_at: string;
}

export interface CampaignRunDetail {
  id: string;
  preset_id: string | null;
  status: CampaignRunStatus;
  config: Record<string, unknown>;
  progress: Record<string, unknown>;
  created_meta_ids: Record<string, unknown>;
  error: string | null;
  idempotency_key: string | null;
  created_at: string;
  updated_at: string;
}

// ─── Состояние визарда (Zustand) ──────────────────────────────────────────

export type WizardStep =
  | "start"        // 1. Старт
  | "identity"     // 2. Идентичность + оффер
  | "config"       // 3. Цель / бюджет / таргет
  | "structure"    // 4. Структура (кампании + адсеты)
  | "creatives"    // 5. Загрузка концептов
  | "preview"      // 6. Превью / dry-run
  | "launch";      // 7. Запуск → прогресс

export const WIZARD_STEPS: WizardStep[] = [
  "start", "identity", "config", "structure", "creatives", "preview", "launch",
];

export const WIZARD_STEP_LABEL: Record<WizardStep, string> = {
  start:     "Старт",
  identity:  "Идентичность",
  config:    "Параметры",
  structure: "Структура",
  creatives: "Креативы",
  preview:   "Превью",
  launch:    "Запуск",
};
