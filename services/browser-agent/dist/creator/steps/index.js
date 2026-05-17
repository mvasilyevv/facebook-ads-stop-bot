"use strict";
Object.defineProperty(exports, "__esModule", { value: true });
exports.STEPS = void 0;
// Регистрация всех 23 шагов phase 3 в реестре.
const registry_js_1 = require("../registry.js");
const set_conversion_location_js_1 = require("./set_conversion_location.js");
const set_pixel_event_js_1 = require("./set_pixel_event.js");
const set_optimization_goal_js_1 = require("./set_optimization_goal.js");
const set_attribution_js_1 = require("./set_attribution.js");
const set_cta_js_1 = require("./set_cta.js");
const set_geo_js_1 = require("./set_geo.js");
const set_age_js_1 = require("./set_age.js");
const set_budget_js_1 = require("./set_budget.js");
const set_schedule_start_js_1 = require("./set_schedule_start.js");
const set_tracking_url_js_1 = require("./set_tracking_url.js");
const fill_texts_js_1 = require("./fill_texts.js");
const upload_creatives_js_1 = require("./upload_creatives.js");
const create_campaign_js_1 = require("./create_campaign.js");
const create_adset_js_1 = require("./create_adset.js");
const duplicate_adset_js_1 = require("./duplicate_adset.js");
const duplicate_ad_js_1 = require("./duplicate_ad.js");
const rename_adset_js_1 = require("./rename_adset.js");
const rename_ad_js_1 = require("./rename_ad.js");
const reattach_creative_js_1 = require("./reattach_creative.js");
const switch_to_adset_js_1 = require("./switch_to_adset.js");
const click_next_js_1 = require("./click_next.js");
const save_draft_js_1 = require("./save_draft.js");
const unknown_js_1 = require("./unknown.js");
const STEPS = [
    new set_conversion_location_js_1.SetConversionLocationStep(),
    new set_pixel_event_js_1.SetPixelEventStep(),
    new set_optimization_goal_js_1.SetOptimizationGoalStep(),
    new set_attribution_js_1.SetAttributionStep(),
    new set_cta_js_1.SetCtaStep(),
    new set_geo_js_1.SetGeoStep(),
    new set_age_js_1.SetAgeStep(),
    new set_budget_js_1.SetBudgetStep(),
    new set_schedule_start_js_1.SetScheduleStartStep(),
    new set_tracking_url_js_1.SetTrackingUrlStep(),
    new fill_texts_js_1.FillTextsStep(),
    new upload_creatives_js_1.UploadCreativesStep(),
    new create_campaign_js_1.CreateCampaignStep(),
    new create_adset_js_1.CreateAdsetStep(),
    new duplicate_adset_js_1.DuplicateAdsetStep(),
    new duplicate_ad_js_1.DuplicateAdStep(),
    new rename_adset_js_1.RenameAdsetStep(),
    new rename_ad_js_1.RenameAdStep(),
    new reattach_creative_js_1.ReattachCreativeStep(),
    new switch_to_adset_js_1.SwitchToAdsetStep(),
    new click_next_js_1.ClickNextStep(),
    new save_draft_js_1.SaveDraftStep(),
    new unknown_js_1.UnknownStep(),
];
exports.STEPS = STEPS;
for (const s of STEPS)
    (0, registry_js_1.registerStep)(s);
//# sourceMappingURL=index.js.map