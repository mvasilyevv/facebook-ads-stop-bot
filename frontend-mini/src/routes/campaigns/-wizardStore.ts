/**
 * wizardStore — Zustand-хранилище состояния визарда создания кампании.
 * Хранит текущий шаг + черновик конфига. Сбрасывается при старте нового визарда.
 */
import { create } from "zustand";
import type {
  WizardStep,
  CampaignConfig,
  CampaignPreset,
  UploadedConcept,
  ValidatePlanResponse,
} from "@/lib/campaignTypes";

export interface WizardState {
  step: WizardStep;

  // Выбранный пресет (опционально)
  selectedPreset: CampaignPreset | null;

  // Загруженные концепты
  uploadId: string | null;
  concepts: UploadedConcept[];

  // Черновик конфига (заполняется пошагово)
  config: Partial<CampaignConfig>;

  // Результат dry-run validate
  validatePlan: ValidatePlanResponse | null;

  // run_id после запуска
  runId: string | null;

  // Действия
  setStep: (step: WizardStep) => void;
  nextStep: () => void;
  prevStep: () => void;
  setPreset: (preset: CampaignPreset | null) => void;
  setUpload: (uploadId: string, concepts: UploadedConcept[]) => void;
  updateConfig: (patch: Partial<CampaignConfig>) => void;
  setValidatePlan: (plan: ValidatePlanResponse | null) => void;
  setRunId: (id: string) => void;
  reset: () => void;
}

const STEPS: WizardStep[] = [
  "start", "identity", "config", "structure", "creatives", "preview", "launch",
];

const DEFAULT_CONFIG: Partial<CampaignConfig> = {
  objective: "OUTCOME_SALES",
  optimization_goal: "OFFSITE_CONVERSIONS",
  custom_event_type: "PURCHASE",
  cta: "PLAY_GAME",
  text_optimizations: "OPT_OUT",
  click_through_days: 1,
  view_through_days: 1,
  budget_level: "campaign",
  launch_state: "campaign_paused",
  countries: [],
  age_min: 18,
  age_max: 65,
  advantage_audience: true,
  campaigns: [],
};

function nextStepFrom(current: WizardStep): WizardStep {
  const idx = STEPS.indexOf(current);
  return STEPS[Math.min(idx + 1, STEPS.length - 1)] as WizardStep;
}

function prevStepFrom(current: WizardStep): WizardStep {
  const idx = STEPS.indexOf(current);
  return STEPS[Math.max(idx - 1, 0)] as WizardStep;
}

export const useWizardStore = create<WizardState>((set) => ({
  step: "start",
  selectedPreset: null,
  uploadId: null,
  concepts: [],
  config: { ...DEFAULT_CONFIG },
  validatePlan: null,
  runId: null,

  setStep: (step) => set({ step }),
  nextStep: () => set((s) => ({ step: nextStepFrom(s.step) })),
  prevStep: () => set((s) => ({ step: prevStepFrom(s.step) })),
  setPreset: (preset) =>
    set((s) => ({
      selectedPreset: preset,
      config: preset
        ? {
            ...s.config,
            act_id: preset.act_id,
            page_id: preset.page_id,
            pixel_id: preset.pixel_id,
            tz_offset: preset.tz_offset,
            offer_code: preset.offer_code ?? s.config.offer_code,
            byer_tag: preset.byer_tag ?? s.config.byer_tag,
            objective: preset.objective,
            optimization_goal: preset.optimization_goal,
            custom_event_type: preset.custom_event_type,
            cta: preset.cta,
            text_optimizations: preset.text_optimizations,
            click_through_days: preset.click_through_days,
            view_through_days: preset.view_through_days,
          }
        : s.config,
    })),
  setUpload: (uploadId, concepts) => set({ uploadId, concepts }),
  updateConfig: (patch) =>
    set((s) => ({ config: { ...s.config, ...patch } })),
  setValidatePlan: (plan) => set({ validatePlan: plan }),
  setRunId: (id) => set({ runId: id }),
  reset: () =>
    set({
      step: "start",
      selectedPreset: null,
      uploadId: null,
      concepts: [],
      config: { ...DEFAULT_CONFIG },
      validatePlan: null,
      runId: null,
    }),
}));
