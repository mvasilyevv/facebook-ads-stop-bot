import { useCallback, useEffect, useRef, useState } from "react";
import {
  applyCampaignPreset,
  campaignWizardFromDraft,
  campaignWizardReducer,
  campaignWizardToDraft,
  createCampaignWizardState,
  type CampaignDraftWireState,
  type CampaignPreset,
  type CampaignWizardAction,
  type CampaignWizardState,
  type ValidatePlan,
} from "@fb/features/campaigns";
import { GeneratedApiError } from "@fb/operator-api";

import {
  type CampaignDraftDocument,
  useCampaignDraft,
  useDeleteCampaignDraft,
  useSaveCampaignDraft,
} from "@/lib/campaigns";

export type MiniDraftSyncState =
  | "loading"
  | "idle"
  | "saving"
  | "saved"
  | "error"
  | "conflict";

const SAVE_DELAY_MS = 600;

export function useCampaignWizardDraft() {
  const query = useCampaignDraft();
  const saveMutation = useSaveCampaignDraft();
  const deleteMutation = useDeleteCampaignDraft();
  const [state, setState] = useState<CampaignWizardState>(
    createCampaignWizardState,
  );
  const [plan, setPlan] = useState<ValidatePlan | null>(null);
  const [revision, setRevision] = useState(0);
  const [updatedAt, setUpdatedAt] = useState<string | null>(null);
  const [version, setVersion] = useState(0);
  const [savedVersion, setSavedVersion] = useState(0);
  const [syncState, setSyncState] = useState<MiniDraftSyncState>("loading");
  const [hydrated, setHydrated] = useState(false);
  const versionRef = useRef(version);
  useEffect(() => {
    versionRef.current = version;
  }, [version]);

  const hydrate = useCallback((document: CampaignDraftDocument | null) => {
    if (document) {
      setState(
        campaignWizardFromDraft(document.state as CampaignDraftWireState),
      );
      setRevision(document.revision);
      setUpdatedAt(document.updated_at);
    } else {
      setState(createCampaignWizardState());
      setRevision(0);
      setUpdatedAt(null);
    }
    setPlan(null);
    setVersion(0);
    setSavedVersion(0);
    setSyncState("saved");
    setHydrated(true);
  }, []);

  useEffect(() => {
    if (!hydrated && query.isSuccess) hydrate(query.data.draft);
  }, [hydrate, hydrated, query.data, query.isSuccess]);

  const dispatch = useCallback((action: CampaignWizardAction) => {
    setState((current) => campaignWizardReducer(current, action));
    setPlan(null);
    setVersion((current) => current + 1);
    setSyncState((current) => (current === "conflict" ? current : "idle"));
  }, []);

  const applyPreset = useCallback((preset: CampaignPreset) => {
    setState((current) => applyCampaignPreset(current, preset));
    setPlan(null);
    setVersion((current) => current + 1);
    setSyncState((current) => (current === "conflict" ? current : "idle"));
  }, []);

  useEffect(() => {
    if (
      !hydrated ||
      version === savedVersion ||
      syncState === "conflict" ||
      syncState === "error" ||
      saveMutation.isPending
    ) {
      return;
    }
    const versionToSave = version;
    const expectedRevision = revision;
    const timer = window.setTimeout(() => {
      setSyncState("saving");
      void saveMutation
        .mutateAsync({
          expected_revision: expectedRevision,
          state: campaignWizardToDraft(state),
        })
        .then((document) => {
          setRevision(document.revision);
          setUpdatedAt(document.updated_at);
          setSavedVersion((current) => Math.max(current, versionToSave));
          setSyncState(versionRef.current === versionToSave ? "saved" : "idle");
        })
        .catch((error: unknown) => {
          setSyncState(
            error instanceof GeneratedApiError && error.status === 409
              ? "conflict"
              : "error",
          );
        });
    }, SAVE_DELAY_MS);
    return () => window.clearTimeout(timer);
  }, [
    hydrated,
    revision,
    saveMutation,
    saveMutation.isPending,
    savedVersion,
    state,
    syncState,
    version,
  ]);

  const reload = useCallback(async () => {
    const result = await query.refetch();
    if (result.data) hydrate(result.data.draft);
  }, [hydrate, query]);

  const reset = useCallback(async () => {
    await deleteMutation.mutateAsync(revision);
    hydrate(null);
  }, [deleteMutation, hydrate, revision]);

  const markCleared = useCallback(() => {
    setRevision(0);
    setUpdatedAt(null);
    setSavedVersion(version);
    setSyncState("saved");
  }, [version]);

  return {
    state,
    plan,
    setPlan,
    dispatch,
    applyPreset,
    revision,
    updatedAt,
    syncState,
    hydrated,
    isHydrating: query.isPending && !hydrated,
    isHydrationError: query.isError && !hydrated,
    reload,
    reset,
    resetPending: deleteMutation.isPending,
    markCleared,
  };
}
