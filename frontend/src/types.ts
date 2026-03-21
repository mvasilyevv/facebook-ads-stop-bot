export type HealthResponse = {
  status: string;
  service: string;
  environment: string;
  database_status?: string;
  timestamp: string;
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
  resolved_cpa_usd?: string | number | null;
  created_at: string;
};

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

export type ApiErrorResponse = {
  detail?: string;
  errors_count?: number;
  message?: string;
};
