import { chromium } from 'playwright';

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
  humanProfile: HumanProfile;
  connectedAt: Date;
  status: 'connected' | 'disconnected' | 'error';
  /** Последний known-good URL вкладки Ads Manager кабинета — чтобы переоткрыть её,
   *  если вкладку закрыли (self-heal). Заполняется при успешном доступе к primary-странице. */
  lastAdsManagerUrl?: string | null;
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
  campaign_name: string;
  adset_name: string;
  ad_name: string;
  delivery_status: string;
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
}
