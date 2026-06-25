/**
 * Zustand store для состояния визарда создания кампаний.
 *
 * Wizarд состоит из 7 шагов:
 *   1. start       — новый / из пресета / клон
 *   2. identity    — кабинет, страница, пиксель, оффер
 *   3. goal        — цель, бюджет, таргет, атрибуция, назначение
 *   4. structure   — кампании (static/video), число adset N
 *   5. creatives   — загрузка концептов, привязка концепт→кампания, copies
 *   6. preview     — dry-run spec + выбор launch_state
 *   7. launch      — запуск и прогресс
 */

import { create } from "zustand";
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
export type StartMode = "new" | "preset" | "clone";

/** Данные шага 1 — выбор режима. */
export interface WizardStart {
  mode: StartMode;
  /** ID пресета (если mode=preset). */
  preset_id?: string | null;
  /** ID запуска для клонирования (если mode=clone). */
  clone_run_id?: string | null;
}

/** Данные шага 2 — идентичность + оффер. */
export interface WizardIdentity {
  act_id: string;
  page_id: string;
  pixel_id: string;
  /** Смещение TZ кабинета в часах (может быть отрицательным). Авто-подхват по act_id. */
  tz_offset: number;
  /** Имя TZ кабинета для отображения (напр. "America/New_York"). "" — ещё не подтянуто. */
  timezone_name: string;
  offer_code: string;
  byer_tag: string;
}

/** Данные шага 3 — цель / бюджет / таргет / атрибуция / назначение. */
export interface WizardGoal {
  objective: string;
  optimization_goal: string;
  custom_event_type: string;
  destination_link: string;
  cta: string;
  text_optimizations: string;
  start_date: string;
  budget_level: "campaign" | "adset";
  daily_budget_cents: number;
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

/** Данные шага 6 — превью и launch_state. */
export interface WizardPreview {
  launch_state: "campaign_paused" | "all_paused";
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

/** Завтрашняя дата в формате YYYY-MM-DD. */
function tomorrow(): string {
  const d = new Date();
  d.setDate(d.getDate() + 1);
  return d.toISOString().slice(0, 10);
}

const DEFAULT_IDENTITY: WizardIdentity = {
  act_id: "",
  page_id: "",
  pixel_id: "",
  tz_offset: 0,
  timezone_name: "",
  offer_code: "",
  byer_tag: "",
};

const DEFAULT_GOAL: WizardGoal = {
  objective: "OUTCOME_SALES",
  optimization_goal: "OFFSITE_CONVERSIONS",
  custom_event_type: "PURCHASE",
  destination_link: "",
  cta: "PLAY_GAME",
  text_optimizations: "OPT_OUT",
  start_date: tomorrow(),
  budget_level: "campaign",
  daily_budget_cents: 2000_00, // $200 как дефолт
  bid_strategy: "LOWEST_COST_WITHOUT_CAP",
  countries: [],
  age_min: 18,
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
  preview: { launch_state: "campaign_paused", plan: null },
  runId: null,
  loadedPreset: null,
};

// ─── Store ────────────────────────────────────────────────────────────────────

export const useWizardStore = create<WizardState & WizardActions>((set, get) => ({
  ...DEFAULT_STATE,

  goTo: (step) => set({ currentStep: step }),

  goNext: () =>
    set((s) => ({
      currentStep: Math.min(s.currentStep + 1, 7) as WizardStep,
    })),

  goPrev: () =>
    set((s) => ({
      currentStep: Math.max(s.currentStep - 1, 1) as WizardStep,
    })),

  setStart: (v) => set((s) => ({ start: { ...s.start, ...v } })),

  setIdentity: (v) => set((s) => ({ identity: { ...s.identity, ...v } })),

  setGoal: (v) => set((s) => ({ goal: { ...s.goal, ...v } })),

  setStructure: (v) => set((s) => ({ structure: { ...s.structure, ...v } })),

  setCreatives: (v) => set((s) => ({ creatives: { ...s.creatives, ...v } })),

  setPreview: (v) => set((s) => ({ preview: { ...s.preview, ...v } })),

  setRunId: (id) => set({ runId: id }),

  applyPreset: (preset) =>
    set({
      loadedPreset: preset,
      identity: {
        act_id: preset.act_id,
        page_id: preset.page_id,
        pixel_id: preset.pixel_id,
        tz_offset: preset.tz_offset,
        // timezone_name в пресете не хранится — подтянется при blur по act_id.
        timezone_name: "",
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

  reset: () => set({ ...DEFAULT_STATE, goal: { ...DEFAULT_GOAL, start_date: tomorrow() } }),

  buildConfig: () => {
    const { identity, goal, structure, creatives, preview } = get();

    // Тип медиа по расширению — зеркало backend VIDEO_EXTS (core/campaign_builder/config.py).
    // Видео-концепт в image-кампанию (или наоборот) уронит уникализатор уже ПОСЛЕ создания
    // объектов в Meta → орфаны. Фильтруем по kind ДО отправки (как mini StepCreatives).
    const VIDEO_EXTS = [".mp4", ".mov", ".m4v", ".webm", ".avi", ".mkv"];
    const refKind = (ref: string): "video" | "image" =>
      VIDEO_EXTS.some((e) => ref.toLowerCase().endsWith(e)) ? "video" : "image";

    // Для каждой кампании собираем concept_refs из загруженных концептов, привязанных к
    // этой кампании по campaign_keys (пустой campaign_keys = все) И совпадающих по типу.
    const campaignsWithRefs: CampaignConfig["campaigns"] = structure.campaigns.map((block) => {
      const refs = creatives.concepts
        .filter(
          (c) =>
            (c.campaign_keys.length === 0 || c.campaign_keys.includes(block.key)) &&
            refKind(c.ref) === block.kind,
        )
        .map((c) => c.ref);
      return { ...block, concept_refs: refs };
    });

    const config: CampaignConfig = {
      act_id: identity.act_id,
      page_id: identity.page_id,
      pixel_id: identity.pixel_id,
      tz_offset: identity.tz_offset,
      offer_code: identity.offer_code,
      byer_tag: identity.byer_tag || null,
      objective: goal.objective,
      optimization_goal: goal.optimization_goal,
      custom_event_type: goal.custom_event_type,
      special_ad_categories: ["NONE"],
      destination_link: goal.destination_link,
      cta: goal.cta,
      text_optimizations: goal.text_optimizations,
      start_date: goal.start_date,
      ad_text:
        goal.ad_text_mode === "text"
          ? { mode: "text", primary: goal.ad_text_primary }
          : { mode: "none" },
      budget_level: goal.budget_level,
      daily_budget_cents: goal.daily_budget_cents,
      bid_strategy: goal.bid_strategy,
      countries: goal.countries,
      age_min: goal.age_min,
      age_max: goal.age_max,
      advantage_audience: goal.advantage_audience,
      click_through_days: goal.click_through_days,
      view_through_days: goal.view_through_days,
      // url_tags вычисляется бэком по SOP (builder.url_tags_of), кастомный ввод убран
      campaigns: campaignsWithRefs,
      copies_per_concept: creatives.copies_per_concept ?? undefined,
      creo_root: creatives.upload_id,
      launch_state: preview.launch_state,
    };

    return config;
  },
}));
