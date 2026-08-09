import { useCallback, useEffect } from "react";
import {
  campaignWizardFromDraft,
  campaignWizardToDraft,
  type CampaignDraftWireState,
} from "@fb/features/campaigns";
import { GeneratedApiError } from "@fb/operator-api";

import {
  type CampaignDraftDocument,
  useCampaignDraft,
  useDeleteCampaignDraft,
  useSaveCampaignDraft,
} from "@/lib/api/campaigns";
import { getWizardFeatureState, useWizardStore } from "@/stores/campaignWizard";

const DRAFT_SAVE_DELAY_MS = 600;

/**
 * Owns the one server-side owner draft. The Zustand store is only a live view;
 * it is never a persistence authority.
 */
export function useCampaignDraftSync() {
  const query = useCampaignDraft();
  const saveMutation = useSaveCampaignDraft();
  const deleteMutation = useDeleteCampaignDraft();

  const hydrated = useWizardStore((state) => state.draftHydrated);
  const draftVersion = useWizardStore((state) => state.draftVersion);
  const savedVersion = useWizardStore((state) => state.draftSavedVersion);
  const revision = useWizardStore((state) => state.draftRevision);
  const syncState = useWizardStore((state) => state.draftSyncState);

  const hydrateDocument = useCallback((document: CampaignDraftDocument | null) => {
    const store = useWizardStore.getState();
    if (!document) {
      store.hydrateDraft(null, 0, null);
      return;
    }
    store.hydrateDraft(
      campaignWizardFromDraft(document.state as CampaignDraftWireState),
      document.revision,
      document.updated_at,
    );
  }, []);

  useEffect(() => {
    if (hydrated || !query.isSuccess) return;
    hydrateDocument(query.data.draft);
  }, [hydrateDocument, hydrated, query.data, query.isSuccess]);

  useEffect(() => {
    if (
      !hydrated ||
      draftVersion === savedVersion ||
      syncState === "conflict" ||
      syncState === "error" ||
      saveMutation.isPending
    ) {
      return;
    }

    const versionToSave = draftVersion;
    const expectedRevision = revision;
    const timer = window.setTimeout(() => {
      const store = useWizardStore.getState();
      store.markDraftSaving();
      void saveMutation
        .mutateAsync({
          expected_revision: expectedRevision,
          state: campaignWizardToDraft(getWizardFeatureState()),
        })
        .then((document) => {
          useWizardStore
            .getState()
            .markDraftSaved(document.revision, document.updated_at, versionToSave);
        })
        .catch((error: unknown) => {
          useWizardStore
            .getState()
            .markDraftError(
              error instanceof GeneratedApiError && error.status === 409 ? "conflict" : "error",
            );
        });
    }, DRAFT_SAVE_DELAY_MS);

    return () => window.clearTimeout(timer);
  }, [
    draftVersion,
    hydrated,
    revision,
    saveMutation,
    saveMutation.isPending,
    savedVersion,
    syncState,
  ]);

  const reloadServerDraft = useCallback(async () => {
    const result = await query.refetch();
    if (result.data) hydrateDocument(result.data.draft);
  }, [hydrateDocument, query]);

  const deleteServerDraft = useCallback(async () => {
    const currentRevision = useWizardStore.getState().draftRevision;
    await deleteMutation.mutateAsync(currentRevision);
    useWizardStore.getState().reset();
  }, [deleteMutation]);

  return {
    isHydrating: query.isPending && !hydrated,
    isHydrationError: query.isError && !hydrated,
    hydrationError: query.error,
    syncState,
    reloadServerDraft,
    deleteServerDraft,
    deletePending: deleteMutation.isPending,
  };
}
