import type { components } from "@fb/shared/api/generated";
import type { DataState } from "@fb/shared";

export type CampaignConfig = components["schemas"]["CampaignConfigIn"];
export type CampaignPreset = components["schemas"]["PresetOut"];
export type CampaignPresetInput = components["schemas"]["PresetIn"];
export type ValidatePlan = components["schemas"]["ValidatePlanOut"];
export type CampaignGender = "male" | "female";
export type CampaignPlacement = "facebook" | "instagram" | "messenger" | "audience_network";

export type CampaignBidStrategy = NonNullable<CampaignConfig["bid_strategy"]>;

/**
 * Подписи — как в Ads Manager, чтобы выбор здесь и там читался одинаково.
 *
 * Порядок не алфавитный, а по частоте в кабинетах: замер 17.08 по трём
 * кабинетам дал 41 живую кампанию из 55 на «Максимальном количестве» и 12 на
 * «Цели по цене за результат».
 */
export const CAMPAIGN_BID_STRATEGY_OPTIONS: ReadonlyArray<{
  value: CampaignBidStrategy;
  label: string;
  /** Требует ли стратегия ставку (bid_amount). */
  needsBid: boolean;
}> = [
  { value: "LOWEST_COST_WITHOUT_CAP", label: "Максимальное количество", needsBid: false },
  { value: "COST_CAP", label: "Цель по цене за результат", needsBid: true },
  { value: "LOWEST_COST_WITH_BID_CAP", label: "Предел ставки", needsBid: true },
  { value: "LOWEST_COST_WITH_MIN_ROAS", label: "Цель по ROAS", needsBid: false },
];

export const CAMPAIGN_GENDER_OPTIONS: ReadonlyArray<{
  value: CampaignGender;
  label: string;
}> = [
  { value: "female", label: "Женщины" },
  { value: "male", label: "Мужчины" },
];

export const CAMPAIGN_PLACEMENT_OPTIONS: ReadonlyArray<{
  value: CampaignPlacement;
  label: string;
}> = [
  { value: "facebook", label: "Facebook" },
  { value: "instagram", label: "Instagram" },
  { value: "messenger", label: "Messenger" },
  { value: "audience_network", label: "Audience Network" },
];

export function toggleCampaignTag<T extends string>(values: T[], value: T): T[] {
  return values.includes(value)
    ? values.filter((candidate) => candidate !== value)
    : [...values, value];
}

export type CampaignWizardStep = 1 | 2 | 3 | 4 | 5 | 6 | 7;
export type CampaignStartMode = "new" | "preset";

export interface CampaignWizardStart {
  mode: CampaignStartMode;
  preset_id?: string | null;
}

export interface CampaignWizardIdentity {
  act_id: string;
  ad_account_ids: string[];
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
  /** Отображаемая ссылка под заголовком (link_data.caption у Meta). */
  display_link: string;
  cta: string;
  text_optimizations: "OPT_OUT";
  start_date: string;
  budget_level: "campaign" | "adset";
  daily_budget: string;
  bid_amount: string;
  bid_strategy: CampaignBidStrategy;
  countries: string[];
  age_min: number;
  age_max: number;
  advantage_audience: boolean;
  genders: CampaignGender[];
  placements: CampaignPlacement[];
  click_through_days: 1 | 7 | 28;
  view_through_days: 1 | 7 | 28;
  naming_template: string;
  url_tags_template: string;
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
  ad_account_ids: [],
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
  display_link: "",
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
  genders: [],
  placements: [],
  click_through_days: 1,
  view_through_days: 1,
  naming_template: "",
  url_tags_template: "",
  ad_text_mode: "none",
  ad_text_primary: "",
};

export function createCampaignWizardState(): CampaignWizardState {
  return {
    currentStep: 1,
    start: { mode: "new" },
    identity: { ...DEFAULT_IDENTITY, ad_account_ids: [] },
    goal: { ...DEFAULT_GOAL, countries: [], genders: [], placements: [] },
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
    // Кабинеты, оффер и подтверждённый account context принадлежат запуску:
    // шаблон их не подменяет, иначе выбор кабинетов слетал бы при применении.
    identity: state.identity,
    goal: {
      ...state.goal,
      objective: "OUTCOME_SALES",
      optimization_goal: "OFFSITE_CONVERSIONS",
      custom_event_type: "PURCHASE",
      text_optimizations: "OPT_OUT",
      countries: [...preset.countries],
      age_min: preset.age_min,
      age_max: preset.age_max,
      genders: [...preset.genders],
      placements: [...preset.placements],
      budget_level: preset.budget_level,
      daily_budget: preset.daily_budget ?? state.goal.daily_budget,
      naming_template: preset.naming_template ?? "",
      url_tags_template: preset.url_tags_template ?? "",
    },
  };
}

export function campaignWizardToDraft(state: CampaignWizardState): CampaignDraftWireState {
  return {
    current_step: state.currentStep,
    start: { ...state.start },
    identity: {
      ...state.identity,
      ad_account_ids: [...(state.identity.ad_account_ids ?? [])],
    },
    goal: {
      ...state.goal,
      countries: [...state.goal.countries],
      genders: [...state.goal.genders],
      placements: [...state.goal.placements],
    },
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

export function campaignWizardFromDraft(draft: CampaignDraftWireState): CampaignWizardState {
  return cloneCampaignWizardState({
    currentStep: draft.current_step,
    start: draft.start,
    identity: draft.identity,
    goal: draft.goal,
    structure: draft.structure,
    creatives: draft.creatives,
  });
}

function cloneCampaignWizardState(state: CampaignWizardState): CampaignWizardState {
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
  const draftAccounts = draft.identity.ad_account_ids ?? [];
  const selectedAccounts =
    draftAccounts.length > 0 ? draftAccounts : draft.identity.act_id ? [draft.identity.act_id] : [];
  // Черновики, созданные до появления таргета шаблонов, этих ключей не имеют.
  // Поднимаем их к тем же явным значениям, что и у нового визарда.
  const goal = draft.goal as CampaignWizardGoal & {
    genders?: CampaignGender[];
    placements?: CampaignPlacement[];
    naming_template?: string;
    url_tags_template?: string;
  };
  return {
    currentStep: draft.current_step,
    start: { ...draft.start },
    identity: {
      ...draft.identity,
      ad_account_ids: [...selectedAccounts],
    },
    goal: {
      ...goal,
      countries: [...goal.countries],
      genders: [...(goal.genders ?? [])],
      placements: [...(goal.placements ?? [])],
      naming_template: goal.naming_template ?? "",
      url_tags_template: goal.url_tags_template ?? "",
    },
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
  if ((values.ad_account_ids ?? []).length === 0) {
    errors.ad_account_ids = "Выберите хотя бы один кабинет оффера";
  }
  if (!values.act_id.trim()) errors.act_id = "Не выбран основной кабинет";
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
    errors.account_context_state = "Нужен свежий подтверждённый USD-снимок кабинета";
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
  if (!MAJOR_AMOUNT_PATTERN.test(value)) return "Используйте положительное число с точкой";
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
      (wholeUnits === maxWhole && fraction.slice(0, exponent).replaceAll("0", "") !== "");
    if (aboveCap) return `Максимум ${maxWhole.toLocaleString("ru-RU")} в день`;
  }
  return null;
}

export function validateCampaignGoal(
  values: CampaignWizardGoal,
  currencyExponent: number | null,
): Partial<Record<keyof CampaignWizardGoal, string>> {
  const errors: Partial<Record<keyof CampaignWizardGoal, string>> = {};
  if (!values.destination_link.trim()) errors.destination_link = "Укажите трекинг-ссылку";
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
  if (values.countries.length === 0) errors.countries = "Укажите хотя бы одну страну";
  if (values.age_min < 18 || values.age_max > 65 || values.age_min > values.age_max) {
    errors.age_min = "Возраст должен быть в диапазоне 18–65 без пересечения";
  }
  if (values.start_date && !/^\d{4}-\d{2}-\d{2}$/.test(values.start_date)) {
    errors.start_date = "Некорректная дата";
  }
  return errors;
}

export function validateCampaignStructure(campaigns: CampaignWizardCampaign[]): string | null {
  if (campaigns.length === 0) return "Добавьте хотя бы одну кампанию";
  if (campaigns.some((campaign) => campaign.adset_count < 1)) {
    return "Число adset'ов должно быть ≥ 1";
  }
  if (new Set(campaigns.map((campaign) => campaign.key)).size !== campaigns.length) {
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
  const invalid = [...new Set(tokens.filter((token) => !/^[A-Z]{2}$/.test(token)))];
  return {
    countries: [...new Set(tokens.filter((token) => /^[A-Z]{2}$/.test(token)))],
    invalid,
  };
}

export function validateCampaignCreatives(values: CampaignWizardCreatives): string | null {
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
  if (step === 2) return validateCampaignIdentity(state.identity) as Record<string, string>;
  if (step === 3) {
    return validateCampaignGoal(state.goal, state.identity.currency_exponent) as Record<
      string,
      string
    >;
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

export function buildCampaignConfig(state: CampaignWizardState): CampaignConfig {
  if (!state.creatives.upload_id) throw new Error("Сначала загрузите и распределите креативы");
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
  const emptyCampaign = campaigns.find((campaign) => campaign.concept_refs.length === 0);
  if (emptyCampaign) {
    throw new Error(`Для кампании ${emptyCampaign.key} не назначен ни один креатив`);
  }

  const config: CampaignConfig = {
    act_id: state.identity.ad_account_ids?.[0] ?? state.identity.act_id,
    page_id: state.identity.page_id,
    pixel_id: state.identity.pixel_id,
    offer_code: state.identity.offer_code,
    byer_tag: state.identity.byer_tag || null,
    objective: state.goal.objective,
    optimization_goal: state.goal.optimization_goal,
    custom_event_type: state.goal.custom_event_type,
    special_ad_categories: ["NONE"],
    destination_link: state.goal.destination_link,
    display_link: state.goal.display_link,
    cta: state.goal.cta,
    text_optimizations: state.goal.text_optimizations,
    start_date: state.goal.start_date || null,
    ad_text: {
      mode: state.goal.ad_text_mode,
      primary: state.goal.ad_text_mode === "text" ? state.goal.ad_text_primary : "",
    },
    budget_level: state.goal.budget_level,
    daily_budget: state.goal.daily_budget,
    bid_amount: state.goal.bid_amount || null,
    bid_strategy: state.goal.bid_strategy,
    countries: state.goal.countries,
    age_min: state.goal.age_min,
    age_max: state.goal.age_max,
    advantage_audience: state.goal.advantage_audience,
    genders: state.goal.genders,
    placements: state.goal.placements,
    click_through_days: state.goal.click_through_days,
    view_through_days: state.goal.view_through_days,
    naming_template: state.goal.naming_template || null,
    url_tags: state.goal.url_tags_template || null,
    campaigns,
    copies_per_concept: state.creatives.copies_per_concept ?? undefined,
    creo_root: state.creatives.upload_id,
  };
  return config;
}

export type CampaignLaunchObservedState =
  | "queued"
  | "uniquifying"
  | "uploading"
  | "creating"
  | "succeeded"
  | "failed"
  | "cancelled"
  | "rejected"
  | "unknown";

export type CampaignLaunchAggregateState =
  | "working"
  | "succeeded"
  | "partial"
  | "failed"
  | "unknown";

/**
 * Одна кампания залива: то, что оператор видит как отдельный прогон.
 *
 * Единица знаменателя «3 из 4» — кампания, а не кабинет: после разреза плана
 * каждая кампания получает свою задачу и свой исход. Кабинет, отвергнутый до
 * разбора плана, кампаний не имеет и остаётся одной отвергнутой единицей —
 * иначе запрос, где не поставлено ничего, показал бы пустой знаменатель.
 */
export interface CampaignLaunchUnit {
  accountId: string;
  campaignKey: string | null;
  runId: string | null;
  status: string;
  error: string | null;
}

/**
 * Разложить receipt залива в плоский список кампаний по кабинетам.
 *
 * Тип входа берётся из сгенерированного контракта, а не описывается здесь
 * заново: своя копия формы разошлась бы с сервером молча — переименованное
 * поле прошло бы и `gen:api`, и `typecheck`, а оба фронта прочитали бы
 * `undefined`.
 */
export function campaignLaunchUnits(
  accounts: components["schemas"]["LaunchAccountOut"][] | null | undefined,
): CampaignLaunchUnit[] {
  return (accounts ?? []).flatMap((account): CampaignLaunchUnit[] => {
    const campaigns = account.campaigns ?? [];
    if (campaigns.length === 0) {
      return [
        {
          accountId: account.account_id,
          campaignKey: null,
          runId: account.run_id ?? null,
          status: account.status,
          error: account.error ?? null,
        },
      ];
    }
    return campaigns.map((campaign) => ({
      accountId: account.account_id,
      campaignKey: campaign.campaign_key,
      runId: campaign.run_id ?? null,
      status: campaign.status,
      error: campaign.error ?? null,
    }));
  });
}

/** Green is possible only when every launched campaign is confirmed succeeded. */
export function aggregateCampaignLaunchState(
  states: CampaignLaunchObservedState[],
): CampaignLaunchAggregateState {
  if (states.length === 0) return "working";
  const working = states.some((state) =>
    ["queued", "uniquifying", "uploading", "creating"].includes(state),
  );
  if (working) return "working";
  const succeeded = states.filter((state) => state === "succeeded").length;
  if (succeeded === states.length) return "succeeded";
  if (succeeded > 0) return "partial";
  if (states.includes("unknown")) return "unknown";
  return "failed";
}

export interface CampaignPresetDraft {
  name: string;
  countries: string[];
  age_min: number;
  age_max: number;
  genders: CampaignGender[];
  placements: CampaignPlacement[];
  custom_event_type: "PURCHASE";
  budget_level: "campaign" | "adset";
  daily_budget: string;
  /** Ставка — часть заготовки: COST_CAP без неё не собирается. */
  bid_strategy: CampaignBidStrategy;
  bid_amount: string;
  display_link: string;
  naming_template: string;
  url_tags_template: string;
}

export function createCampaignPresetDraft(
  source?: CampaignPreset | CampaignWizardState,
): CampaignPresetDraft {
  if (source && "currentStep" in source) {
    return {
      name: "",
      countries: [...source.goal.countries],
      age_min: source.goal.age_min,
      age_max: source.goal.age_max,
      genders: [...source.goal.genders],
      placements: [...source.goal.placements],
      custom_event_type: "PURCHASE",
      budget_level: source.goal.budget_level,
      daily_budget: source.goal.daily_budget,
      bid_strategy: source.goal.bid_strategy,
      bid_amount: source.goal.bid_amount,
      display_link: source.goal.display_link,
      naming_template: source.goal.naming_template,
      url_tags_template: source.goal.url_tags_template,
    };
  }
  if (source) {
    return {
      name: source.name,
      countries: [...source.countries],
      age_min: source.age_min,
      age_max: source.age_max,
      genders: [...source.genders],
      placements: [...source.placements],
      custom_event_type: "PURCHASE",
      budget_level: source.budget_level,
      daily_budget: source.daily_budget ?? "",
      bid_strategy: source.bid_strategy,
      bid_amount: source.bid_amount,
      display_link: source.display_link,
      naming_template: source.naming_template ?? "",
      url_tags_template: source.url_tags_template ?? "",
    };
  }
  return {
    name: "",
    countries: [],
    age_min: 21,
    age_max: 65,
    genders: [],
    placements: [],
    custom_event_type: "PURCHASE",
    budget_level: "campaign",
    daily_budget: "",
    bid_strategy: "COST_CAP",
    bid_amount: "",
    display_link: "",
    naming_template: "",
    url_tags_template: "",
  };
}

export function validateCampaignPresetDraft(
  draft: CampaignPresetDraft,
): Partial<Record<keyof CampaignPresetDraft, string>> {
  const errors: Partial<Record<keyof CampaignPresetDraft, string>> = {};
  if (!draft.name.trim()) errors.name = "Укажите название пресета";
  if (draft.countries.length === 0) errors.countries = "Добавьте хотя бы одну страну";
  if (draft.age_min < 18 || draft.age_max > 65 || draft.age_min > draft.age_max) {
    errors.age_min = "Возраст должен быть в диапазоне 18–65";
  }
  const budgetError = validateMajorAmount(draft.daily_budget, {
    exponent: 2,
    label: "дневной бюджет",
    maxWhole: 100_000n,
  });
  if (budgetError) errors.daily_budget = budgetError;
  return errors;
}

export function campaignPresetPayload(draft: CampaignPresetDraft): CampaignPresetInput {
  return {
    ...draft,
    name: draft.name.trim(),
    countries: [...draft.countries],
    genders: [...draft.genders],
    placements: [...draft.placements],
    naming_template: draft.naming_template.trim() || null,
    url_tags_template: draft.url_tags_template.trim() || null,
  };
}

export function campaignPresetsDataState(input: {
  isPending: boolean;
  isError: boolean;
  count: number;
}): DataState {
  if (input.isError) return "unavailable";
  if (input.isPending) return "stale";
  return input.count === 0 ? "empty" : "ready";
}
