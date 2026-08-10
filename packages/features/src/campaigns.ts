import type { components } from "@fb/shared/api/generated";

export type CampaignConfig = components["schemas"]["CampaignConfigIn"];
export type CampaignPreset = components["schemas"]["PresetOut"];
export type ValidatePlan = components["schemas"]["ValidatePlanOut"];

export type CampaignWizardStep = 1 | 2 | 3 | 4 | 5 | 6 | 7;
export type CampaignStartMode = "new" | "preset";

export interface CampaignWizardStart {
  mode: CampaignStartMode;
  preset_id?: string | null;
}

export interface CampaignWizardIdentity {
  act_id: string;
  page_id: string;
  pixel_id: string;
  account_context_state: "ready" | "stale" | "unavailable";
  timezone_name: string;
  currency: "" | "USD";
  currency_exponent: 2 | null;
  account_context_observed_at: string | null;
  account_context_issue: string | null;
  offer_code: string;
  byer_tag: string;
}

export interface CampaignWizardGoal {
  objective: "OUTCOME_SALES";
  optimization_goal: "OFFSITE_CONVERSIONS";
  custom_event_type: "PURCHASE";
  destination_link: string;
  cta: string;
  text_optimizations: "OPT_OUT";
  start_date: string;
  budget_level: "campaign" | "adset";
  daily_budget: string;
  bid_amount: string;
  bid_strategy: "COST_CAP";
  countries: string[];
  age_min: number;
  age_max: number;
  advantage_audience: boolean;
  click_through_days: 1 | 7 | 28;
  view_through_days: 1 | 7 | 28;
  ad_text_mode: "none" | "text";
  ad_text_primary: string;
}

export interface CampaignWizardCampaign {
  key: string;
  label?: string | null;
  adset_count: number;
}

export interface CampaignWizardStructure {
  campaigns: CampaignWizardCampaign[];
}

export interface CampaignWizardConcept {
  ref: string;
  original_name: string;
  size_bytes: number;
  content_type?: string | null;
  campaign_keys: string[];
}

export interface CampaignWizardCreatives {
  upload_id: string | null;
  concepts: CampaignWizardConcept[];
  copies_per_concept: number | null;
}

export interface CampaignWizardState {
  currentStep: CampaignWizardStep;
  start: CampaignWizardStart;
  identity: CampaignWizardIdentity;
  goal: CampaignWizardGoal;
  structure: CampaignWizardStructure;
  creatives: CampaignWizardCreatives;
}

/** Wire shape intentionally contains no preview, run/task state or secrets. */
export interface CampaignDraftWireState {
  current_step: CampaignWizardStep;
  start: CampaignWizardStart;
  identity: CampaignWizardIdentity;
  goal: CampaignWizardGoal;
  structure: CampaignWizardStructure;
  creatives: CampaignWizardCreatives;
}

export type CampaignWizardAction =
  | { type: "goTo"; step: CampaignWizardStep }
  | { type: "patchStart"; value: Partial<CampaignWizardStart> }
  | { type: "patchIdentity"; value: Partial<CampaignWizardIdentity> }
  | { type: "patchGoal"; value: Partial<CampaignWizardGoal> }
  | { type: "setCampaigns"; campaigns: CampaignWizardCampaign[] }
  | { type: "patchCreatives"; value: Partial<CampaignWizardCreatives> }
  | { type: "replace"; state: CampaignWizardState }
  | { type: "reset" };

const DEFAULT_IDENTITY: CampaignWizardIdentity = {
  act_id: "",
  page_id: "",
  pixel_id: "",
  account_context_state: "unavailable",
  timezone_name: "",
  currency: "",
  currency_exponent: null,
  account_context_observed_at: null,
  account_context_issue: null,
  offer_code: "",
  byer_tag: "",
};

const DEFAULT_GOAL: CampaignWizardGoal = {
  objective: "OUTCOME_SALES",
  optimization_goal: "OFFSITE_CONVERSIONS",
  custom_event_type: "PURCHASE",
  destination_link: "",
  cta: "PLAY_GAME",
  text_optimizations: "OPT_OUT",
  start_date: "",
  budget_level: "campaign",
  daily_budget: "",
  bid_amount: "",
  bid_strategy: "COST_CAP",
  countries: [],
  age_min: 21,
  age_max: 65,
  advantage_audience: true,
  click_through_days: 1,
  view_through_days: 1,
  ad_text_mode: "none",
  ad_text_primary: "",
};

export function createCampaignWizardState(): CampaignWizardState {
  return {
    currentStep: 1,
    start: { mode: "new" },
    identity: { ...DEFAULT_IDENTITY },
    goal: { ...DEFAULT_GOAL, countries: [] },
    structure: { campaigns: [] },
    creatives: { upload_id: null, concepts: [], copies_per_concept: null },
  };
}

export function campaignWizardReducer(
  state: CampaignWizardState,
  action: CampaignWizardAction,
): CampaignWizardState {
  switch (action.type) {
    case "goTo":
      return { ...state, currentStep: action.step };
    case "patchStart": {
      const start = { ...state.start, ...action.value };
      if (start.mode === "new") start.preset_id = null;
      return { ...state, start };
    }
    case "patchIdentity":
      return { ...state, identity: { ...state.identity, ...action.value } };
    case "patchGoal":
      return { ...state, goal: { ...state.goal, ...action.value } };
    case "setCampaigns":
      return { ...state, structure: { campaigns: action.campaigns } };
    case "patchCreatives":
      return { ...state, creatives: { ...state.creatives, ...action.value } };
    case "replace":
      return cloneCampaignWizardState(action.state);
    case "reset":
      return createCampaignWizardState();
  }
}

export function applyCampaignPreset(
  state: CampaignWizardState,
  preset: CampaignPreset,
): CampaignWizardState {
  return {
    ...state,
    start: { mode: "preset", preset_id: preset.id },
    identity: {
      ...DEFAULT_IDENTITY,
      act_id: preset.act_id,
      page_id: preset.page_id,
      pixel_id: preset.pixel_id,
      offer_code: preset.offer_code ?? "",
      byer_tag: preset.byer_tag ?? "",
    },
    goal: {
      ...state.goal,
      objective: "OUTCOME_SALES",
      optimization_goal: "OFFSITE_CONVERSIONS",
      custom_event_type: "PURCHASE",
      cta: preset.cta,
      text_optimizations: "OPT_OUT",
      click_through_days: asAttributionDays(preset.click_through_days),
      view_through_days: asAttributionDays(preset.view_through_days),
    },
  };
}

function asAttributionDays(value: number): 1 | 7 | 28 {
  return value === 7 || value === 28 ? value : 1;
}

export function campaignWizardToDraft(
  state: CampaignWizardState,
): CampaignDraftWireState {
  return {
    current_step: state.currentStep,
    start: { ...state.start },
    identity: { ...state.identity },
    goal: { ...state.goal, countries: [...state.goal.countries] },
    structure: {
      campaigns: state.structure.campaigns.map((campaign) => ({ ...campaign })),
    },
    creatives: {
      ...state.creatives,
      concepts: state.creatives.concepts.map((concept) => ({
        ...concept,
        campaign_keys: [...concept.campaign_keys],
      })),
    },
  };
}

export function campaignWizardFromDraft(
  draft: CampaignDraftWireState,
): CampaignWizardState {
  return cloneCampaignWizardState({
    currentStep: draft.current_step,
    start: draft.start,
    identity: draft.identity,
    goal: draft.goal,
    structure: draft.structure,
    creatives: draft.creatives,
  });
}

function cloneCampaignWizardState(
  state: CampaignWizardState,
): CampaignWizardState {
  return campaignWizardFromWireWithoutRecursion({
    current_step: state.currentStep,
    start: state.start,
    identity: state.identity,
    goal: state.goal,
    structure: state.structure,
    creatives: state.creatives,
  });
}

function campaignWizardFromWireWithoutRecursion(
  draft: CampaignDraftWireState,
): CampaignWizardState {
  return {
    currentStep: draft.current_step,
    start: { ...draft.start },
    identity: { ...draft.identity },
    goal: { ...draft.goal, countries: [...draft.goal.countries] },
    structure: {
      campaigns: draft.structure.campaigns.map((campaign) => ({ ...campaign })),
    },
    creatives: {
      ...draft.creatives,
      concepts: draft.creatives.concepts.map((concept) => ({
        ...concept,
        campaign_keys: [...concept.campaign_keys],
      })),
    },
  };
}

export function validateCampaignIdentity(
  values: CampaignWizardIdentity,
): Partial<Record<keyof CampaignWizardIdentity, string>> {
  const errors: Partial<Record<keyof CampaignWizardIdentity, string>> = {};
  if (!values.act_id.trim()) errors.act_id = "Обязательное поле";
  if (!values.page_id.trim()) errors.page_id = "Обязательное поле";
  if (!values.pixel_id.trim()) errors.pixel_id = "Обязательное поле";
  if (!values.offer_code.trim()) errors.offer_code = "Обязательное поле";
  if (
    values.account_context_state !== "ready" ||
    !values.timezone_name ||
    values.currency !== "USD" ||
    values.currency_exponent !== 2 ||
    !values.account_context_observed_at
  ) {
    errors.account_context_state =
      "Нужен свежий подтверждённый USD-снимок кабинета";
  }
  return errors;
}

const MAJOR_AMOUNT_PATTERN = /^(?:0|[1-9][0-9]*)(?:\.[0-9]+)?$/;

function validateMajorAmount(
  value: string,
  options: { exponent: number | null; label: string; maxWhole?: bigint },
): string | null {
  const { exponent, label, maxWhole } = options;
  if (!value) return `Укажите ${label}`;
  if (!MAJOR_AMOUNT_PATTERN.test(value))
    return "Используйте положительное число с точкой";
  if (!/[1-9]/.test(value)) return `${label} должен быть больше нуля`;
  if (exponent !== 2) return "Сначала подтвердите USD-контекст кабинета";
  const [wholePart, fraction = ""] = value.split(".");
  const whole = wholePart ?? "0";
  if (fraction.slice(exponent).replaceAll("0", "") !== "") {
    return `Для USD допустимо не более ${exponent} знаков после точки`;
  }
  if (maxWhole != null) {
    const wholeUnits = BigInt(whole);
    const aboveCap =
      wholeUnits > maxWhole ||
      (wholeUnits === maxWhole &&
        fraction.slice(0, exponent).replaceAll("0", "") !== "");
    if (aboveCap) return `Максимум ${maxWhole.toLocaleString("ru-RU")} в день`;
  }
  return null;
}

export function validateCampaignGoal(
  values: CampaignWizardGoal,
  currencyExponent: number | null,
): Partial<Record<keyof CampaignWizardGoal, string>> {
  const errors: Partial<Record<keyof CampaignWizardGoal, string>> = {};
  if (!values.destination_link.trim())
    errors.destination_link = "Укажите трекинг-ссылку";
  const budgetError = validateMajorAmount(values.daily_budget, {
    exponent: currencyExponent,
    label: "дневной бюджет",
    maxWhole: 100_000n,
  });
  if (budgetError) errors.daily_budget = budgetError;
  const bidError = validateMajorAmount(values.bid_amount, {
    exponent: currencyExponent,
    label: "целевой CPA",
  });
  if (bidError) errors.bid_amount = bidError;
  if (values.countries.length === 0)
    errors.countries = "Укажите хотя бы одну страну";
  if (values.start_date && !/^\d{4}-\d{2}-\d{2}$/.test(values.start_date)) {
    errors.start_date = "Некорректная дата";
  }
  return errors;
}

export function validateCampaignStructure(
  campaigns: CampaignWizardCampaign[],
): string | null {
  if (campaigns.length === 0) return "Добавьте хотя бы одну кампанию";
  if (campaigns.some((campaign) => campaign.adset_count < 1)) {
    return "Число adset'ов должно быть ≥ 1";
  }
  if (
    new Set(campaigns.map((campaign) => campaign.key)).size !== campaigns.length
  ) {
    return "Ключи кампаний должны быть уникальными";
  }
  return null;
}

export function nextCampaignKey(campaigns: CampaignWizardCampaign[]): string {
  const used = new Set(campaigns.map((campaign) => campaign.key));
  let index = 1;
  while (used.has(`camp${index}`)) index += 1;
  return `camp${index}`;
}

export function parseCampaignCountryInput(raw: string): {
  countries: string[];
  invalid: string[];
} {
  const tokens = raw
    .split(/[\s,;]+/)
    .map((token) => token.trim().toUpperCase())
    .filter(Boolean);
  const invalid = [
    ...new Set(tokens.filter((token) => !/^[A-Z]{2}$/.test(token))),
  ];
  return {
    countries: [...new Set(tokens.filter((token) => /^[A-Z]{2}$/.test(token)))],
    invalid,
  };
}

export function validateCampaignCreatives(
  values: CampaignWizardCreatives,
): string | null {
  if (values.concepts.length === 0) return "Загрузите хотя бы один концепт";
  if (!values.upload_id) return "Концепты не загружены на сервер";
  return null;
}

export function validateCampaignStep(
  state: CampaignWizardState,
  step: CampaignWizardStep,
): Record<string, string> {
  if (step === 1 && state.start.mode === "preset" && !state.start.preset_id) {
    return { preset_id: "Выберите пресет" };
  }
  if (step === 2)
    return validateCampaignIdentity(state.identity) as Record<string, string>;
  if (step === 3) {
    return validateCampaignGoal(
      state.goal,
      state.identity.currency_exponent,
    ) as Record<string, string>;
  }
  if (step === 4) {
    const error = validateCampaignStructure(state.structure.campaigns);
    return error ? { structure: error } : {};
  }
  if (step === 5) {
    const error = validateCampaignCreatives(state.creatives);
    return error ? { creatives: error } : {};
  }
  return {};
}

export function buildCampaignConfig(
  state: CampaignWizardState,
  preset?: CampaignPreset | null,
): CampaignConfig {
  if (!state.creatives.upload_id)
    throw new Error("Сначала загрузите и распределите креативы");
  if (
    state.identity.account_context_state !== "ready" ||
    state.identity.currency !== "USD" ||
    state.identity.currency_exponent !== 2
  ) {
    throw new Error("USD-контекст кабинета не подтверждён");
  }

  const campaigns = state.structure.campaigns.map((campaign) => ({
    ...campaign,
    concept_refs: state.creatives.concepts
      .filter((concept) => concept.campaign_keys.includes(campaign.key))
      .map((concept) => concept.ref),
  }));
  if (campaigns.length === 0) throw new Error("Добавьте хотя бы одну кампанию");
  const emptyCampaign = campaigns.find(
    (campaign) => campaign.concept_refs.length === 0,
  );
  if (emptyCampaign) {
    throw new Error(
      `Для кампании ${emptyCampaign.key} не назначен ни один креатив`,
    );
  }

  const config: CampaignConfig = {
    act_id: state.identity.act_id,
    page_id: state.identity.page_id,
    pixel_id: state.identity.pixel_id,
    offer_code: state.identity.offer_code,
    byer_tag: state.identity.byer_tag || null,
    objective: state.goal.objective,
    optimization_goal: state.goal.optimization_goal,
    custom_event_type: state.goal.custom_event_type,
    special_ad_categories: ["NONE"],
    destination_link: state.goal.destination_link,
    cta: state.goal.cta,
    text_optimizations: state.goal.text_optimizations,
    start_date: state.goal.start_date || null,
    ad_text: {
      mode: state.goal.ad_text_mode,
      primary:
        state.goal.ad_text_mode === "text" ? state.goal.ad_text_primary : "",
    },
    budget_level: state.goal.budget_level,
    daily_budget: state.goal.daily_budget,
    bid_amount: state.goal.bid_amount || null,
    bid_strategy: state.goal.bid_strategy,
    countries: state.goal.countries,
    age_min: state.goal.age_min,
    age_max: state.goal.age_max,
    advantage_audience: state.goal.advantage_audience,
    click_through_days: state.goal.click_through_days,
    view_through_days: state.goal.view_through_days,
    campaigns,
    copies_per_concept: state.creatives.copies_per_concept ?? undefined,
    creo_root: state.creatives.upload_id,
  };
  if (preset?.url_tags_template) config.url_tags = preset.url_tags_template;
  return config;
}
