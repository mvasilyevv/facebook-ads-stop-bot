// Регистрация всех 23 шагов phase 3 в реестре.
import { registerStep } from '../registry.js';
import { SetConversionLocationStep } from './set_conversion_location.js';
import { SetPixelEventStep } from './set_pixel_event.js';
import { SetOptimizationGoalStep } from './set_optimization_goal.js';
import { SetAttributionStep } from './set_attribution.js';
import { SetCtaStep } from './set_cta.js';
import { SetGeoStep } from './set_geo.js';
import { SetAgeStep } from './set_age.js';
import { SetBudgetStep } from './set_budget.js';
import { SetScheduleStartStep } from './set_schedule_start.js';
import { SetTrackingUrlStep } from './set_tracking_url.js';
import { FillTextsStep } from './fill_texts.js';
import { UploadCreativesStep } from './upload_creatives.js';
import { CreateCampaignStep } from './create_campaign.js';
import { CreateAdsetStep } from './create_adset.js';
import { DuplicateAdsetStep } from './duplicate_adset.js';
import { DuplicateAdStep } from './duplicate_ad.js';
import { RenameAdsetStep } from './rename_adset.js';
import { RenameAdStep } from './rename_ad.js';
import { ReattachCreativeStep } from './reattach_creative.js';
import { SwitchToAdsetStep } from './switch_to_adset.js';
import { ClickNextStep } from './click_next.js';
import { SaveDraftStep } from './save_draft.js';
import { UnknownStep } from './unknown.js';

const STEPS = [
  new SetConversionLocationStep(),
  new SetPixelEventStep(),
  new SetOptimizationGoalStep(),
  new SetAttributionStep(),
  new SetCtaStep(),
  new SetGeoStep(),
  new SetAgeStep(),
  new SetBudgetStep(),
  new SetScheduleStartStep(),
  new SetTrackingUrlStep(),
  new FillTextsStep(),
  new UploadCreativesStep(),
  new CreateCampaignStep(),
  new CreateAdsetStep(),
  new DuplicateAdsetStep(),
  new DuplicateAdStep(),
  new RenameAdsetStep(),
  new RenameAdStep(),
  new ReattachCreativeStep(),
  new SwitchToAdsetStep(),
  new ClickNextStep(),
  new SaveDraftStep(),
  new UnknownStep(),
];

for (const s of STEPS) registerStep(s);

export { STEPS };
