/**
 * TypeScript-типы для API responses.
 * Источник — apps/api/routers/v1/schemas/*.py.
 *
 * Принцип: каждый endpoint возвращает либо одну Out-схему, либо list[Out].
 * Decimal-поля сериализуются как string (asyncpg + Pydantic).
 *
 * ВАЖНО: Сгенерированные типы из OpenAPI находятся в api-generated.ts.
 * Этот файл — ручной, используется для удобства в компонентах.
 * При расхождении api-generated.ts является источником истины.
 * Регенерация: make export-openapi && npm run gen:api
 */

// ─── Dashboard ──────────────────────────────────────────────────────────────

export interface DashboardStats {
  total_ads_monitored: number;
  ads_in_normal: number;
  ads_in_warning: number;
  ads_in_stop: number;
  ads_in_claimed: number;
  ads_in_disabled: number;
  active_incidents: number;
  last_scan_at: string | null;
  last_scan_outcome: string | null;
  scans_today: number;
  scans_today_with_errors: number;
  observer_status: "running" | "paused" | "unknown";
  pending_disable_tasks: number;
  pending_enable_tasks: number;
  failed_tasks_24h: number;
}

export interface MetricsBlock {
  cycle_ts: string;
  spend: string | null;
  impressions: number | null;
  clicks: number | null;
  ctr: string | null;
  cpc: string | null;
  cpm: string | null;
  reach: number | null;
  frequency: string | null;
  leads: number | null;
  cost_per_lead: string | null;
  registrations: number | null;
  cost_per_registration: string | null;
  deposits: number | null;
}

export interface AdSnapshot {
  fb_ad_id: string;
  internal_id: string;
  ad_name: string;
  campaign_name: string | null;
  adset_name: string | null;
  offer_code: string | null;
  offer_id: string | null;
  alert_state: string;
  snoozed_until: string | null;
  open_state_token: string | null;
  last_warning_at: string | null;
  last_stop_at: string | null;
  is_active: boolean;
  last_seen_at: string | null;
  delivery_status: string | null;
  meta_ad_status: string | null;
  stop_rule_codes: string[];
  warning_rule_codes: string[];
  metrics: MetricsBlock | null;
}

export interface Incident extends AdSnapshot {
  incident_open_since: string | null;
  incident_duration_seconds: number | null;
  transitions_count: number;
}

export interface AlertEvent {
  id: string;
  fb_ad_id: string | null;
  ad_name: string | null;
  campaign_name: string | null;
  offer_code: string | null;
  stage: "warning" | "stop";
  matched_rule_codes: string[];
  triggered_by_rule_codes: string[] | null;
  created_at: string;
  alert_payload: Record<string, unknown> | null;
}

export interface DashboardBatch {
  stats: DashboardStats;
  recent_incidents: Incident[];
  recent_alerts: AlertEvent[];
  recent_disable_tasks: TaskQueueRow[];
  enable_recommendations_pending: EnableRecommendation[];
}

// ─── Offers ─────────────────────────────────────────────────────────────────

export interface Offer {
  id: string;
  code: string;
  name: string;
  vertical: string | null;
  is_active: boolean;
  created_at: string | null;
  updated_at: string | null;
}

export interface OfferCompareRow {
  offer_id: string;
  offer_code: string;
  offer_name: string;
  days: number;
  spend: string;
  leads: number;
  registrations: number;
  deposits: number;
  active_ads_count: number;
  stop_alerts_count: number;
  cost_per_lead: string | null;
  cost_per_registration: string | null;
  cost_per_deposit: string | null;
}

// DRIFT: threshold-поля — Decimal в БД, сериализуются как string, не number.
// backend OfferRuleOut: все пороги имеют тип string | null (Pydantic Decimal → JSON string).
export interface OfferRules {
  offer_id: string | null; // backend позволяет null
  spend_no_event_threshold: string | null; // было number | null — Decimal сериализуется как string
  cpa_threshold: string | null;
  cpm_threshold: string | null;
  ctr_threshold: string | null;
  frequency_threshold: string | null;
  funnel_ratio_threshold: string | null;
  /** Стоп срабатывает при N% от базового правила. Диапазон 1–100, дефолт 80. */
  stop_percent_of_rule: string | null;
  /** Ворнинг срабатывает при M% от стопа. Диапазон 1–100, дефолт 80. */
  warning_percent_of_stop: string | null;
}

// ─── Tasks ──────────────────────────────────────────────────────────────────

// DRIFT: created_at и updated_at в backend TaskQueueRowOut — required (string, не string | null).
// requested_by в backend — required string (не nullable).
export interface TaskQueueRow {
  id: string;
  fb_ad_id: string | null;
  ad_name: string | null;
  task_type: string;
  status: string;
  attempt_count: number;
  max_attempts: number;
  requested_by: string; // было string | null — в backend required
  requested_by_chat_id: number | null;
  created_at: string; // было string | null — в backend required
  updated_at: string; // было string | null — в backend required
  next_attempt_at: string | null;
  last_error_message: string | null;
}

export interface EnableRecommendation {
  id: string;
  fb_ad_id: string;
  ad_name: string;
  campaign_name: string | null;
  reason: string;
  recommendation_level: string;
  metrics_payload: Record<string, unknown> | null;
  created_at: string | null;
  live_batch_started_at: string | null;
  promoted_to_task_id: string | null;
  promoted_task_status: string | null;
}

// ─── History ────────────────────────────────────────────────────────────────

// Структура соответствует реальному ответу /api/history/summary (вложенные блоки).
export interface HistorySummary {
  from_iso: string;
  to_iso: string;
  // totals: структура соответствует HistoryTotals из OpenAPI-схемы
  totals: {
    spend: string;
    impressions: number;
    clicks: number;
    leads: number;
    registrations: number;
    deposits: number;
    active_ads_count: number; // присутствует в backend-схеме (HistoryTotals)
  };
  alerts: {
    warning_count: number;
    stop_count: number;
    by_rule: Array<{ rule_code: string; count: number }>;
  };
  tasks: {
    disable_completed: number;
    disable_failed: number;
    enable_completed: number;
  };
}

// ─── Timeseries ─────────────────────────────────────────────────────────────

export interface SpendPoint {
  cycle_ts: string;
  fb_ad_id: string | null;
  spend: string | null;
  impressions: number | null;
  clicks: number | null;
  leads: number | null;
  registrations: number | null;
  deposits: number | null;
}

export interface ChartBucket {
  ts: string;
  spend: string | null;
  impressions: number | null;
  clicks: number | null;
  leads: number | null;
  registrations: number | null;
  deposits: number | null;
  active_ads: number | null;
}

// ─── Settings ───────────────────────────────────────────────────────────────

// DRIFT: ручные поля (scan_interval_seconds, cabinet_url и др.) не совпадают с backend.
// Backend (ObserverSettingsResponse) возвращает: is_scanning_enabled, default_interval_seconds,
// auto_enable_recommendations, warning_percent_of_stop (null), cpc/cpl/cpr_warning_percent (null).
// Используй api-generated.ts: components["schemas"]["ObserverSettingsResponse"]
export interface ObserverSettings {
  // Поля совместимые с backend ObserverSettingsResponse
  is_scanning_enabled: boolean;
  default_interval_seconds: number;
  auto_enable_recommendations: boolean;
  // Канал авто-стопа: true — Marketing API (pause_ad, точно по ad_id), false — DOM-клик.
  act_via_api: boolean;
  // Owner-scoping: тег твоих кампаний в общем кабинете (NULL — фильтр выключен).
  owner_campaign_tag: string | null;
  // Allowlist кампаний для am-режима (#3).
  campaign_ids: string[];
  warning_percent_of_stop: null;
  cpc_warning_percent: null;
  cpl_warning_percent: null;
  cpr_warning_percent: null;
  // Устаревшие поля (убрать при миграции на api-generated.ts):
  /** @deprecated используй is_scanning_enabled */
  scan_interval_seconds?: number;
  /** @deprecated поле отсутствует в backend */
  cabinet_url?: string | null;
  /** @deprecated поле отсутствует в backend */
  country_code?: string | null;
  /** @deprecated нет в схеме */
  auto_disable_enabled?: boolean;
  /** @deprecated используй auto_enable_recommendations */
  auto_enable_recommendations_enabled?: boolean;
  /** @deprecated используй is_scanning_enabled */
  is_scanning?: boolean;
}

// DRIFT: ручной тип имеет recipients_count, которого нет в backend TelegramSettingsResponse.
// Backend добавляет: activation_command (string), chat_id (string | null).
// recipients_count — отдельный endpoint GET /settings/telegram/recipients (TelegramRecipientsListResponse.total)
export interface TelegramSettings {
  is_authorized: boolean;
  poller_status: string;
  bot_username: string | null;
  auth_deep_link: string | null;
  activation_command: string; // присутствует в backend (default: "/start auth")
  chat_id: string | null; // присутствует в backend
  /** @deprecated отсутствует в backend TelegramSettingsResponse, приходит из отдельного /recipients endpoint */
  recipients_count?: number;
}

export interface TelegramRecipient {
  id: string;
  chat_id: number;
  username: string | null;
  role: string;
  created_at: string | null;
  revoked_at: string | null;
}

// DRIFT: ручной invite_code → backend возвращает code (не invite_code!)
// backend TelegramInviteResponse: { code: string; expires_at: string }
export interface TelegramInviteResponse {
  code: string; // было invite_code — исправлено под backend-контракт
  expires_at: string; // required в backend (не nullable)
}

// DRIFT: ручной тип полностью расходится с backend VisionSettingsResponse.
// Backend возвращает: has_token (bool), profile_id, auto_restart_on_missing_cdp (bool),
// runtime_status, runtime_status_message, cdp_ready (bool), cdp_port (int | null).
// Поля vision_token и is_connected в backend ОТСУТСТВУЮТ.
export interface VisionSettings {
  has_token: boolean; // было vision_token: string | null — backend возвращает только флаг
  profile_id: string | null;
  auto_restart_on_missing_cdp: boolean;
  runtime_status: string | null;
  runtime_status_message: string | null;
  cdp_ready: boolean;
  cdp_port: number | null;
  /** @deprecated в backend нет — используй has_token */
  vision_token?: string | null;
  /** @deprecated в backend нет — используй cdp_ready || runtime_status */
  is_connected?: boolean;
}

// ─── Observer / Health ──────────────────────────────────────────────────────

// DRIFT: ручной тип имеет last_cycle_at, cycle_count_today, active_country, active_campaign —
// backend ObserverStatusResponse возвращает: status, last_scan_at, interval_seconds, extra (dict).
// Поля cycle_count_today / active_country / active_campaign в backend ОТСУТСТВУЮТ.
// Данные о цикле находятся в extra{} или недоступны напрямую.
export interface ObserverStatus {
  status: string; // "running" | "paused" | "unknown" — backend не enum
  last_scan_at: string | null; // было last_cycle_at
  interval_seconds: number | null;
  extra: Record<string, unknown>;
  /** @deprecated используй last_scan_at */
  last_cycle_at?: string | null;
  /** @deprecated поле отсутствует в backend — смотри extra */
  cycle_count_today?: number;
  /** @deprecated поле отсутствует в backend — смотри extra */
  active_country?: string | null;
  /** @deprecated поле отсутствует в backend — смотри extra */
  active_campaign?: string | null;
}

export interface ScanRun {
  id: string;
  started_at: string;
  finished_at: string | null;
  outcome: string;
  ads_seen: number;
  alerts_created: number;
  errors_count: number;
  duration_ms: number | null;
}

// DRIFT: backend WorkerStatus дополнительно возвращает ttl_seconds и payload.
// HealthDetailsResponse дополнительно содержит observer_runtime (dict | null).
export interface HealthWorker {
  name: string;
  status: "ONLINE" | "OFFLINE";
  last_heartbeat_at: string | null;
  ttl_seconds: number | null; // отсутствовало в ручном типе
  payload: Record<string, unknown> | null; // отсутствовало в ручном типе
}

export interface HealthDetails {
  overall: "HEALTHY" | "DEGRADED" | "CRITICAL";
  workers: HealthWorker[];
  observer_runtime: Record<string, unknown> | null; // отсутствовало в ручном типе
}

// ─── Rule Preview ────────────────────────────────────────────────────────────

export interface RulePreviewCostRule {
  rule: string;
  label: string;
  base: number;
  stop: number;
  warning: number;
}

export interface RulePreviewSpendRange {
  rule: string;
  label: string;
  stop_from: number;
  stop_to: number;
  warning_from: number;
}

export interface RulePreviewOut {
  cpa: number;
  stop_percent_of_rule: number;
  warning_percent_of_stop: number;
  cost_rules: RulePreviewCostRule[];
  spend_ranges: RulePreviewSpendRange[];
  regs_no_dep_stop_count: number;
}

// ─── Generic helpers ────────────────────────────────────────────────────────

/**
 * Параметры query-string для list-endpoint'ов.
 * Открытый index signature чтобы можно было передавать конкретные интерфейсы.
 */
export interface QueryParams {
  [key: string]: string | number | boolean | null | undefined;
}
