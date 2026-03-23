export type HealthResponse = {
  status: string;
  service: string;
  environment: string;
  database_status?: string;
  timestamp: string;
};

export type ProfileItem = {
  profile_id: string;
  display_name: string;
  browser_host_id: string;
  is_active: boolean;
  scan_suspended: boolean;
  last_launch_at?: string | null;
};

export type ProfileLaunchItem = {
  id: string;
  profile_id: string;
  display_name: string;
  browser_host_id: string;
  name: string;
  is_active: boolean;
  started_at: string;
  ended_at?: string | null;
  created_at: string;
  updated_at: string;
};

export type ProfileLaunchActionResponse = {
  message: string;
  launch: ProfileLaunchItem;
  cleared_control_flags: number;
  cleared_cooldowns: number;
};

export type ProfileLaunchSummary = {
  total_ads: number;
  active_ads: number;
  paused_ads: number;
  attention_ads: number;
  spend_total: string | number;
  scans_count: number;
  last_scan_at?: string | null;
};

export type ProfileLaunchTrendPoint = {
  timestamp: string;
  value: string | number;
};

export type ProfileLaunchDashboard = {
  launch: ProfileLaunchItem;
  previous_launch?: ProfileLaunchItem | null;
  current: ProfileLaunchSummary;
  previous?: ProfileLaunchSummary | null;
  spend_series: ProfileLaunchTrendPoint[];
  attention_series: ProfileLaunchTrendPoint[];
  action_series: ProfileLaunchTrendPoint[];
};

export type AdSummary = {
  fb_ad_id: string;
  campaign_name: string;
  adset_name: string;
  ad_name: string;
  delivery_status: string;
  tracking_mode: string;
  scope_presence: string;
  last_seen_at?: string | null;
  last_decision: string;
  last_decision_reason?: string | null;
  last_decision_at?: string | null;
  last_execution_state?: DecisionExecutionState | null;
  last_action_source?: string | null;
  last_action_at?: string | null;
  last_action_message?: string | null;
  resolved_cpa_usd?: string | number | null;
  spend?: string | number | null;
  clicks?: number | null;
  cpc?: string | number | null;
  leads?: number | null;
  cost_per_lead?: string | number | null;
  registrations?: number | null;
  cost_per_registration?: string | number | null;
  deposits?: number | null;
};

export type AdDetail = AdSummary & {
  campaign_scope_key: string;
  adset_scope_key: string;
  last_scan_run_id?: string | null;
  created_at: string;
  updated_at: string;
};

export type DecisionItem = {
  id: string;
  scan_run_id: string;
  fb_ad_id: string;
  rule_id?: string | null;
  decision: string;
  reason: string;
  action_executed: boolean;
  action_status?: string | null;
  execution_state?: DecisionExecutionState;
  resolved_cpa_usd?: string | number | null;
  created_at: string;
};

export type DecisionExecutionState =
  | "NOT_REQUIRED"
  | "SKIPPED_BY_MODE"
  | "PENDING"
  | "SUCCEEDED"
  | "FAILED";

export type RuleItem = {
  id: string;
  code: string;
  title: string;
  description?: string | null;
  is_enabled: boolean;
  priority: number;
  cpa_multiplier?: string | number | null;
  updated_at: string;
};

export type OfferItem = {
  id: string;
  code: string;
  name: string;
  is_active: boolean;
  current_cpa_usd?: string | number | null;
  created_at: string;
  updated_at: string;
};

export type OfferRateItem = {
  id: string;
  offer_id: string;
  cpa_usd: string | number;
  effective_from: string;
  effective_to?: string | null;
  note?: string | null;
  created_at: string;
};

export type OfferBindingItem = {
  id: string;
  entity_type: "campaign" | "adset" | "ad";
  entity_id: string;
  offer_id: string;
  offer_code: string;
  priority: number;
  is_active: boolean;
  created_at: string;
  updated_at: string;
};

export type BrowserSessionItem = {
  profile_id: string;
  browser_host_id: string;
  status: string;
  cdp_url?: string | null;
  webdriver_url?: string | null;
  last_started_at?: string | null;
  last_stopped_at?: string | null;
  last_message?: string | null;
};

export type ScanRunItem = {
  id: string;
  browser_host_id: string;
  profile_id: string;
  profile_launch_id?: string | null;
  profile_launch_name?: string | null;
  status: string;
  rows_seen: number;
  rows_parsed: number;
  scope_summary?: Record<string, unknown> | null;
  error_message?: string | null;
  started_at: string;
  finished_at?: string | null;
};

export type BotModeResponse = {
  auto_pause_enabled: boolean;
  auto_resume_enabled: boolean;
  observe_only_enabled: boolean;
  updated_at: string;
};

export type ServiceSettings = {
  auto_pause_enabled: boolean;
  auto_resume_enabled: boolean;
  observe_only_enabled: boolean;
  scan_interval_seconds: number;
  vision_api_token: string;
  telegram_bot_token: string;
  telegram_chat_id: string;
  vision_local_api_url: string;
  vision_cloud_api_url: string;
  updated_at?: string | null;
};

export type ServiceSettingsResponse = {
  auto_pause_enabled: boolean;
  auto_resume_enabled: boolean;
  auto_resume_available: boolean;
  observe_only_enabled: boolean;
  scan_interval_seconds: number;
  vision_local_api_url: string;
  vision_cloud_api_url: string;
  telegram_chat_id: string;
  vision_api_token_masked?: string | null;
  telegram_bot_token_masked?: string | null;
  vision_api_token_configured: boolean;
  telegram_bot_token_configured: boolean;
  updated_at: string;
};

export type ServiceSettingsUpdate = Pick<
  ServiceSettings,
  | "auto_pause_enabled"
  | "auto_resume_enabled"
  | "observe_only_enabled"
  | "scan_interval_seconds"
  | "telegram_chat_id"
  | "vision_local_api_url"
  | "vision_cloud_api_url"
> & {
  vision_api_token?: string | null;
  telegram_bot_token?: string | null;
};

export type SuspendedProfileItem = {
  profile_id: string;
  display_name: string;
  browser_host_id: string;
  reason: string;
  suspended_at: string;
};

export type SuspendedProfileResetResponse = {
  message: string;
  profile: SuspendedProfileItem;
};

export type ApiErrorResponse = {
  detail?: string;
  errors_count?: number;
  message?: string;
};
