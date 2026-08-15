import { chromium } from 'playwright';

export type BrowserPageRole = 'scan' | 'control' | 'interactive';

export interface VisionProfile {
  folder_id: string;
  profile_id: string;
  port: number | null;
}

export interface BrowserSession {
  id: string;
  visionApiUrl: string;
  visionXToken: string;
  visionProfileId: string;
  visionFolderId: string;
  cdpPort: number;
  playwright: typeof chromium | null;
  browser: import('playwright').Browser | null;
  primaryPage: import('playwright').Page | null;
  /**
   * Dedicated pages used by the operator control plane. A cabinet key is the
   * exact numeric Meta account id (without ``act_``). A role page is never
   * assigned until the current Ads Manager URL confirms that account id.
   *
   * Scan and control pages MUST never contain the same Page instance. A scan
   * reload is therefore unable to destroy the execution context of a money
   * mutation already running on the control page.
   */
  scanPages: Map<string, import('playwright').Page>;
  controlPages: Map<string, import('playwright').Page>;
  /**
   * Non-money Graph work and media uploads. This page is deliberately not the
   * control page: a 120 second upload must never occupy the page/lock used by
   * an auto-pause status mutation.
   */
  interactivePages: Map<string, import('playwright').Page>;
  humanProfile: HumanProfile;
  connectedAt: Date;
  status: 'connected' | 'disconnected' | 'error';
  /** Последний подтверждённый URL кабинета для восстановления только role-page
   *  внутри уже живого browser/CDP соединения. */
  lastAdsManagerUrl?: string | null;
  /** Число подряд идущих сетевых сбоев fetch внутри Vision-страницы
   *  (Failed to fetch / code -2). Используется только для bounded page reload. */
  netFailureStreak?: number;
  /** Когда reload'или role/account page в последний раз (cooldown). */
  lastHealAt?: Date | null;
}

export interface HumanProfile {
  speedFactor: number;
  jitterFactor: number;
  pauseFactor: number;
  overshootChance: number;
  idleChance: number;
  idleDurationMin: number;
  idleDurationMax: number;
  bezierStepsMin: number;
  bezierStepsMax: number;
}

export interface ScrollMetrics {
  found: boolean;
  scrollTop: number;
  maxScrollTop: number;
  atBottom: boolean;
  /** Нужно для виртуальной таблицы Ads Manager: видимые строки могут смениться при scrollTop = 0. */
  moved: boolean;
}

export interface ScannedAdRow {
  fb_ad_id: string;
  campaign_id: string;
  adset_id: string;
  campaign_name: string;
  adset_name: string;
  ad_name: string;
  delivery_status: string;
  moderation_reason: string | null;
  spend: string;
  budget: string;
  reach: number;
  impressions: number;
  clicks: number;
  cpc: string | null;
  ctr: string | null;
  outbound_clicks: number;
  outbound_ctr: string | null;
  landing_page_views: number;
  cost_per_landing_page_view: string | null;
  cost_per_result: string | null;
  cpm: string | null;
  frequency: string | null;
  leads: number;
  cost_per_lead: string | null;
  registrations: number;
  cost_per_registration: string | null;
  deposits: number;
  resolved_offer_code: string | null;
  creative_thumb_url: string;
  creative_image_url: string;
  adset_pixel_id: string;
  adset_daily_budget: string;
  adset_lifetime_budget: string;
  adset_budget_remaining: string;
  adset_learning_stage: string;
  /**
   * Browser-local provenance for values that cannot be represented as
   * nullable by the current protobuf contract. An empty list means every
   * required atomic metric was present and every populated numeric value was
   * valid. This field is consumed before protobuf serialization: any issue
   * makes the whole cabinet scan partial and therefore unable to reach money
   * writers or rule evaluation.
   */
  metric_issues: string[];
}
