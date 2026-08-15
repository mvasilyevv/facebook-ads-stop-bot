/** Runtime shell around the shared, React-free campaign feature model. */

import { create } from "zustand";
import {
  applyCampaignPreset,
  buildCampaignConfig,
  campaignWizardReducer,
  createCampaignWizardState,
  type CampaignConfig,
  type CampaignPreset,
  type CampaignWizardAction,
  type CampaignWizardConcept,
  type CampaignWizardCreatives,
  type CampaignWizardGoal,
  type CampaignWizardIdentity,
  type CampaignWizardStart,
  type CampaignWizardState,
  type CampaignWizardStep,
  type ValidatePlan,
} from "@fb/features/campaigns";
import type { LaunchOut } from "@/lib/api/campaigns";

export type WizardStep = CampaignWizardStep;
export type StartMode = CampaignWizardStart["mode"];
export type WizardStart = CampaignWizardStart;
export type WizardIdentity = CampaignWizardIdentity;
export type WizardGoal = CampaignWizardGoal;
export type WizardStructure = CampaignWizardState["structure"];
export type UploadedConcept = CampaignWizardConcept;
export type WizardCreatives = CampaignWizardCreatives;

export interface WizardPreview {
  plan: ValidatePlan | null;
}

export type DraftSyncState = "loading" | "idle" | "saving" | "saved" | "error" | "conflict";

export interface WizardRuntimeState {
  preview: WizardPreview;
  launchReceipt: LaunchOut | null;
  draftRevision: number;
  draftUpdatedAt: string | null;
  draftVersion: number;
  draftSavedVersion: number;
  draftSyncState: DraftSyncState;
  draftHydrated: boolean;
}

export interface WizardActions {
  goTo: (step: WizardStep) => void;
  goNext: () => void;
  goPrev: () => void;
  setStart: (value: Partial<WizardStart>) => void;
  setIdentity: (value: Partial<WizardIdentity>) => void;
  setGoal: (value: Partial<WizardGoal>) => void;
  setStructure: (value: Partial<WizardStructure>) => void;
  setCreatives: (value: Partial<WizardCreatives>) => void;
  setPreview: (value: Partial<WizardPreview>) => void;
  setLaunchReceipt: (receipt: LaunchOut | null) => void;
  applyPreset: (preset: CampaignPreset) => void;
  hydrateDraft: (
    state: CampaignWizardState | null,
    revision: number,
    updatedAt: string | null,
  ) => void;
  markDraftSaving: () => void;
  markDraftSaved: (revision: number, updatedAt: string, savedVersion: number) => void;
  markDraftError: (kind: "error" | "conflict") => void;
  markDraftCleared: () => void;
  reset: () => void;
  buildConfig: () => CampaignConfig;
}

export type WizardStore = CampaignWizardState & WizardRuntimeState & WizardActions;

function runtimeDefaults(): WizardRuntimeState {
  return {
    preview: { plan: null },
    launchReceipt: null,
    draftRevision: 0,
    draftUpdatedAt: null,
    draftVersion: 0,
    draftSavedVersion: 0,
    draftSyncState: "loading",
    draftHydrated: false,
  };
}

function featureState(state: WizardStore): CampaignWizardState {
  return {
    currentStep: state.currentStep,
    start: state.start,
    identity: state.identity,
    goal: state.goal,
    structure: state.structure,
    creatives: state.creatives,
  };
}

function changed(state: WizardStore, action: CampaignWizardAction): Partial<WizardStore> {
  const next = campaignWizardReducer(featureState(state), action);
  return {
    ...next,
    preview: { plan: null },
    draftVersion: state.draftVersion + 1,
    draftSyncState: state.draftSyncState === "conflict" ? "conflict" : "idle",
  };
}

export const useWizardStore = create<WizardStore>((set, get) => ({
  ...createCampaignWizardState(),
  ...runtimeDefaults(),

  goTo: (step) => set((state) => changed(state, { type: "goTo", step })),
  goNext: () =>
    set((state) =>
      changed(state, {
        type: "goTo",
        step: Math.min(state.currentStep + 1, 7) as WizardStep,
      }),
    ),
  goPrev: () =>
    set((state) =>
      changed(state, {
        type: "goTo",
        step: Math.max(state.currentStep - 1, 1) as WizardStep,
      }),
    ),
  setStart: (value) => set((state) => changed(state, { type: "patchStart", value })),
  setIdentity: (value) => set((state) => changed(state, { type: "patchIdentity", value })),
  setGoal: (value) => set((state) => changed(state, { type: "patchGoal", value })),
  setStructure: (value) =>
    set((state) =>
      changed(state, {
        type: "setCampaigns",
        campaigns: value.campaigns ?? state.structure.campaigns,
      }),
    ),
  setCreatives: (value) => set((state) => changed(state, { type: "patchCreatives", value })),
  setPreview: (value) => set((state) => ({ preview: { ...state.preview, ...value } })),
  setLaunchReceipt: (launchReceipt) => set({ launchReceipt }),
  applyPreset: (preset) =>
    set((state) => ({
      ...applyCampaignPreset(featureState(state), preset),
      preview: { plan: null },
      draftVersion: state.draftVersion + 1,
      draftSyncState: state.draftSyncState === "conflict" ? "conflict" : "idle",
    })),
  hydrateDraft: (state, revision, updatedAt) =>
    set({
      ...(state ?? createCampaignWizardState()),
      preview: { plan: null },
      launchReceipt: null,
      draftRevision: revision,
      draftUpdatedAt: updatedAt,
      draftVersion: 0,
      draftSavedVersion: 0,
      draftSyncState: "saved",
      draftHydrated: true,
    }),
  markDraftSaving: () => set({ draftSyncState: "saving" }),
  markDraftSaved: (revision, updatedAt, savedVersion) =>
    set((state) => ({
      draftRevision: revision,
      draftUpdatedAt: updatedAt,
      draftSavedVersion: Math.max(state.draftSavedVersion, savedVersion),
      draftSyncState: state.draftVersion === savedVersion ? "saved" : "idle",
    })),
  markDraftError: (kind) => set({ draftSyncState: kind }),
  markDraftCleared: () =>
    set((state) => ({
      draftRevision: 0,
      draftUpdatedAt: null,
      draftSavedVersion: state.draftVersion,
      draftSyncState: "saved",
    })),
  reset: () =>
    set({
      ...createCampaignWizardState(),
      ...runtimeDefaults(),
      draftSyncState: "saved",
      draftHydrated: true,
    }),
  buildConfig: () => buildCampaignConfig(featureState(get())),
}));

export function getWizardFeatureState(): CampaignWizardState {
  return featureState(useWizardStore.getState());
}
