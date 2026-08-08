/**
 * Zustand store для состояния визарда создания кампаний.
 *
 * Wizarд состоит из 7 шагов:
 *   1. start       — новый / из пресета
 *   2. identity    — кабинет, страница, пиксель, оффер
 *   3. goal        — цель, бюджет, таргет, атрибуция, назначение
 *   4. structure   — кампании (static/video), число adset N
 *   5. creatives   — загрузка концептов, привязка концепт→кампания, copies
 *   6. preview     — dry-run spec; все объекты создаются PAUSED
 *   7. launch      — запуск и прогресс
 */

import { create } from "zustand";
import { createJSONStorage, persist } from "zustand/middleware";
import type {
  CampaignConfig,
  CampaignStructure,
  PresetOut,
  UploadedConceptOut,
  ValidatePlanOut,
} from "@/lib/api/campaigns";

/** Шаги визарда (1-based для UX). */
export type WizardStep = 1 | 2 | 3 | 4 | 5 | 6 | 7;

/** Режим старта. */
export type StartMode = "new" | "preset";

/** Данные шага 1 — выбор режима. */
export interface WizardStart {
  mode: StartMode;
  /** ID пресета (если mode=preset). */
  preset_id?: string | null;
}

/** Данные шага 2 — идентичность + оффер. */
export interface WizardIdentity {
  act_id: string;
  page_id: string;
  pixel_id: string;
  /** Server-owned durable account context; never included as a client override. */
  account_context_state: "ready" | "stale" | "unavailable";
  timezone_name: string;
  currency: string;
  currency_exponent: number | null;
  account_context_observed_at: string | null;
  account_context_issue: string | null;
  offer_code: string;
  byer_tag: string;
}

/** Данные шага 3 — цель / бюджет / таргет / атрибуция / назначение. */
export interface WizardGoal {
  // objective / optimization_goal / custom_event_type / bid_strategy / text_optimizations
  // зашиты по SOP и не редактируются из UI — остаются в сторе для отправки на бэк.
  objective: string;
  optimization_goal: string;
  custom_event_type: string;
  destination_link: string;
  cta: string;
  text_optimizations: string;
  start_date: string;
  budget_level: "campaign" | "adset";
  /** Major-unit decimal strings. Currency precision comes from account context. */
  daily_budget: string;
  bid_amount: string;
  bid_strategy: string;
  countries: string[];
  age_min: number;
  age_max: number;
  advantage_audience: boolean;
  click_through_days: number;
  view_through_days: number;
  ad_text_mode: "none" | "text";
  ad_text_primary: string;
}

/** Данные шага 4 — структура кампаний. */
export interface WizardStructure {
  campaigns: CampaignStructure[];
}

/** Загруженный концепт с привязкой к кампаниям. */
export interface UploadedConcept extends UploadedConceptOut {
  /** К каким кампаниям привязан (по key). Пустой = все. */
  campaign_keys: string[];
}

/** Данные шага 5 — концепты и уникализация. */
export interface WizardCreatives {
  /** ID временной папки на сервере (из /upload). */
  upload_id: string | null;
  /** Загруженные концепты с привязками. */
  concepts: UploadedConcept[];
  /** Число копий на концепт (дефолт = N adset'ов). */
  copies_per_concept: number | null;
}

/** Данные шага 6 — неизменяемый all-paused preview. */
export interface WizardPreview {
  /** Результат dry-run /validate. */
  plan: ValidatePlanOut | null;
}

/** Полное состояние визарда. */
export interface WizardState {
  currentStep: WizardStep;
  start: WizardStart;
  identity: WizardIdentity;
  goal: WizardGoal;
  structure: WizardStructure;
  creatives: WizardCreatives;
  preview: WizardPreview;
  /** run_id после запуска — для поллинга прогресса. */
  runId: string | null;
  /** Загруженный пресет (если mode=preset). */
  loadedPreset: PresetOut | null;
  /** Когда черновик последний раз менялся; null означает чистое начальное состояние. */
  updatedAt: string | null;
}

/** Экшены. */
export interface WizardActions {
  goTo: (step: WizardStep) => void;
  goNext: () => void;
  goPrev: () => void;
  setStart: (v: Partial<WizardStart>) => void;
  setIdentity: (v: Partial<WizardIdentity>) => void;
  setGoal: (v: Partial<WizardGoal>) => void;
  setStructure: (v: Partial<WizardStructure>) => void;
  setCreatives: (v: Partial<WizardCreatives>) => void;
  setPreview: (v: Partial<WizardPreview>) => void;
  setRunId: (id: string | null) => void;
  applyPreset: (preset: PresetOut) => void;
  reset: () => void;
  /** Собирает CampaignConfig из всех шагов. */
  buildConfig: () => CampaignConfig;
}

// ─── Дефолты ─────────────────────────────────────────────────────────────────

const DEFAULT_IDENTITY: WizardIdentity = {
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

const DEFAULT_GOAL: WizardGoal = {
  // Инварианты по SOP — зашиты, в UI не выбираются (read-only блок «Зашито по SOP»).
  objective: "OUTCOME_SALES",
  optimization_goal: "OFFSITE_CONVERSIONS",
  custom_event_type: "PURCHASE",
  bid_strategy: "COST_CAP",
  text_optimizations: "OPT_OUT",
  destination_link: "",
  cta: "PLAY_GAME",
  start_date: "",
  budget_level: "campaign",
  daily_budget: "",
  bid_amount: "",
  countries: [],
  age_min: 21,
  age_max: 65,
  advantage_audience: true,
  click_through_days: 1,
  view_through_days: 1,
  ad_text_mode: "none",
  ad_text_primary: "",
};

const DEFAULT_STATE: WizardState = {
  currentStep: 1,
  start: { mode: "new" },
  identity: DEFAULT_IDENTITY,
  goal: DEFAULT_GOAL,
  structure: { campaigns: [] },
  creatives: { upload_id: null, concepts: [], copies_per_concept: null },
  preview: { plan: null },
  runId: null,
  loadedPreset: null,
  updatedAt: null,
};

// ─── Store ────────────────────────────────────────────────────────────────────

export const useWizardStore = create<WizardState & WizardActions>()(
  persist(
    (set, get) => ({
      ...DEFAULT_STATE,

      goTo: (step) => set({ currentStep: step, updatedAt: new Date().toISOString() }),

      goNext: () =>
        set((s) => ({
          currentStep: Math.min(s.currentStep + 1, 7) as WizardStep,
          updatedAt: new Date().toISOString(),
        })),

      goPrev: () =>
        set((s) => ({
          currentStep: Math.max(s.currentStep - 1, 1) as WizardStep,
          updatedAt: new Date().toISOString(),
        })),

      setStart: (v) =>
        set((s) => {
          const start = { ...s.start, ...v };
          const selectedPresetId = start.mode === "preset" ? start.preset_id : null;
          return {
            start,
            // Смена preset → new (или очистка preset_id) должна разорвать
            // связь с пресетом. Иначе buildConfig продолжал молча добавлять его
            // url_tags_template в уже новый залив.
            loadedPreset:
              selectedPresetId && s.loadedPreset?.id === selectedPresetId ? s.loadedPreset : null,
            updatedAt: new Date().toISOString(),
          };
        }),

      setIdentity: (v) =>
        set((s) => ({ identity: { ...s.identity, ...v }, updatedAt: new Date().toISOString() })),

      setGoal: (v) =>
        set((s) => ({ goal: { ...s.goal, ...v }, updatedAt: new Date().toISOString() })),

      setStructure: (v) =>
        set((s) => ({ structure: { ...s.structure, ...v }, updatedAt: new Date().toISOString() })),

      setCreatives: (v) =>
        set((s) => ({ creatives: { ...s.creatives, ...v }, updatedAt: new Date().toISOString() })),

      setPreview: (v) =>
        set((s) => ({ preview: { ...s.preview, ...v }, updatedAt: new Date().toISOString() })),

      setRunId: (id) => set({ runId: id, updatedAt: new Date().toISOString() }),

      applyPreset: (preset) =>
        set({
          loadedPreset: preset,
          updatedAt: new Date().toISOString(),
          identity: {
            act_id: preset.act_id,
            page_id: preset.page_id,
            pixel_id: preset.pixel_id,
            account_context_state: "unavailable",
            timezone_name: "",
            currency: "",
            currency_exponent: null,
            account_context_observed_at: null,
            account_context_issue: null,
            offer_code: preset.offer_code ?? "",
            byer_tag: preset.byer_tag ?? "",
          },
          goal: {
            ...DEFAULT_GOAL,
            objective: preset.objective,
            optimization_goal: preset.optimization_goal,
            custom_event_type: preset.custom_event_type,
            cta: preset.cta,
            text_optimizations: preset.text_optimizations,
            click_through_days: preset.click_through_days,
            view_through_days: preset.view_through_days,
          },
        }),

      reset: () =>
        set({
          ...DEFAULT_STATE,
          identity: { ...DEFAULT_IDENTITY },
          goal: { ...DEFAULT_GOAL },
        }),

      buildConfig: () => {
        const { identity, goal, structure, creatives, loadedPreset } = get();

        if (!creatives.upload_id) {
          throw new Error("Сначала загрузите и распределите креативы");
        }
        if (identity.account_context_state !== "ready") {
          throw new Error("Контекст кабинета не подтверждён");
        }

        // Для каждой кампании собираем concept_refs из концептов, ЯВНО привязанных к ней
        // (campaign_keys содержит ключ кампании). Пустой campaign_keys = концепт не
        // распределён (в пуле) — он не попадает ни в одну кампанию.
        // Фильтр по типу медиа убран — кампания принимает смешанные фото/видео концепты.
        const campaignsWithRefs: CampaignConfig["campaigns"] = structure.campaigns.map((block) => {
          const refs = creatives.concepts
            .filter((c) => c.campaign_keys.includes(block.key))
            .map((c) => c.ref);
          return { ...block, concept_refs: refs };
        });
        if (campaignsWithRefs.length === 0) {
          throw new Error("Добавьте хотя бы одну кампанию");
        }
        const emptyCampaign = campaignsWithRefs.find(
          (campaign) => campaign.concept_refs.length === 0,
        );
        if (emptyCampaign) {
          throw new Error(`Для кампании ${emptyCampaign.key} не назначен ни один креатив`);
        }

        const config: CampaignConfig = {
          act_id: identity.act_id,
          page_id: identity.page_id,
          pixel_id: identity.pixel_id,
          offer_code: identity.offer_code,
          byer_tag: identity.byer_tag || null,
          objective: goal.objective,
          optimization_goal: goal.optimization_goal,
          custom_event_type: goal.custom_event_type,
          special_ad_categories: ["NONE"],
          destination_link: goal.destination_link,
          cta: goal.cta,
          text_optimizations: goal.text_optimizations,
          start_date: goal.start_date || null,
          ad_text: {
            mode: goal.ad_text_mode,
            primary: goal.ad_text_mode === "text" ? goal.ad_text_primary : "",
          },
          budget_level: goal.budget_level,
          daily_budget: goal.daily_budget,
          bid_amount: goal.bid_amount || null,
          bid_strategy: goal.bid_strategy,
          countries: goal.countries,
          age_min: goal.age_min,
          age_max: goal.age_max,
          advantage_audience: goal.advantage_audience,
          click_through_days: goal.click_through_days,
          view_through_days: goal.view_through_days,
          // Backend гарантирует sub8 и для пользовательского шаблона.
          url_tags: loadedPreset?.url_tags_template ?? undefined,
          campaigns: campaignsWithRefs,
          copies_per_concept: creatives.copies_per_concept ?? undefined,
          creo_root: creatives.upload_id,
        };

        return config;
      },
    }),
    {
      name: "fb-agent-campaign-draft",
      version: 3,
      storage: createJSONStorage(() => window.localStorage),
      partialize: (state) => ({
        currentStep: state.currentStep,
        start: state.start,
        identity: state.identity,
        goal: state.goal,
        structure: state.structure,
        creatives: state.creatives,
        loadedPreset: state.loadedPreset,
        updatedAt: state.updatedAt,
      }),
    },
  ),
);
