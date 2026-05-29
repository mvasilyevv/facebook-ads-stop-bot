/**
 * TypeScript-типы для API responses.
 * Источник — apps/api/routers/v1/schemas/*.py.
 *
 * Принцип: каждый endpoint возвращает либо одну Out-схему, либо list[Out].
 * Decimal-поля сериализуются как string (asyncpg + Pydantic).
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

export interface OfferRules {
  offer_id: string;
  spend_no_event_threshold: number | null;
  cpa_threshold: number | null;
  cpm_threshold: number | null;
  ctr_threshold: number | null;
  frequency_threshold: number | null;
  funnel_ratio_threshold: number | null;
}

// ─── Tasks ──────────────────────────────────────────────────────────────────

export interface TaskQueueRow {
  id: string;
  fb_ad_id: string | null;
  ad_name: string | null;
  task_type: string;
  status: string;
  attempt_count: number;
  max_attempts: number;
  requested_by: string | null;
  requested_by_chat_id: number | null;
  created_at: string | null;
  updated_at: string | null;
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
  totals: {
    spend: string;
    impressions: number;
    clicks: number;
    leads: number;
    registrations: number;
    deposits: number;
    active_ads_count: number;
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

export interface ObserverSettings {
  scan_interval_seconds: number;
  cabinet_url: string | null;
  country_code: string | null;
  auto_disable_enabled: boolean;
  auto_enable_recommendations_enabled: boolean;
  is_scanning: boolean;
}

export interface TelegramSettings {
  is_authorized: boolean;
  poller_status: string;
  bot_username: string | null;
  auth_deep_link: string | null;
  recipients_count: number;
}

export interface TelegramRecipient {
  id: string;
  chat_id: number;
  username: string | null;
  role: string;
  created_at: string | null;
  revoked_at: string | null;
}

export interface TelegramInviteResponse {
  invite_code: string;
  expires_at: string | null;
}

export interface VisionSettings {
  vision_token: string | null;
  profile_id: string | null;
  is_connected: boolean;
}

// ─── Observer / Health ──────────────────────────────────────────────────────

export interface ObserverStatus {
  status: "running" | "paused" | "unknown";
  last_cycle_at: string | null;
  cycle_count_today: number;
  active_country: string | null;
  active_campaign: string | null;
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

export interface HealthWorker {
  name: string;
  status: "ONLINE" | "OFFLINE";
  last_heartbeat_at: string | null;
}

export interface HealthDetails {
  overall: "HEALTHY" | "DEGRADED" | "CRITICAL";
  workers: HealthWorker[];
}

// ─── Generic helpers ────────────────────────────────────────────────────────

/**
 * Параметры query-string для list-endpoint'ов.
 * Открытый index signature чтобы можно было передавать конкретные интерфейсы.
 */
export interface QueryParams {
  [key: string]: string | number | boolean | null | undefined;
}
