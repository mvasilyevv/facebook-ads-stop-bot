import {
  useMutation,
  useQueries,
  useQueryClient,
  type Query,
} from "@tanstack/react-query";
import type { components } from "@fb/shared/api/generated";
import { dataOrThrow, noContentOrThrow } from "@fb/operator-api";

import { tmaApi, tmaFetchApi } from "./auth";

export type CampaignDraftDocument = components["schemas"]["CampaignDraftDocument"];
export type CampaignDraftPutIn = components["schemas"]["CampaignDraftPutIn"];
export type CampaignConfig = components["schemas"]["CampaignConfigIn"];
export type CampaignPreset = components["schemas"]["PresetOut"];
export type CampaignPresetInput = components["schemas"]["PresetIn"];
export type UploadConceptsOut = components["schemas"]["UploadConceptsOut"];
export type ValidatePlan = components["schemas"]["ValidatePlanOut"];
export type LaunchIn = components["schemas"]["LaunchIn"];
export type LaunchOut = components["schemas"]["LaunchOut"];
export type RunDetailOut = components["schemas"]["RunDetailOut"];

export function useCampaignPresets() {
  return tmaApi.useQuery("get", "/api/tools/campaigns/presets", {}, { staleTime: 30_000 });
}

function invalidateCampaignPresets(queryClient: ReturnType<typeof useQueryClient>) {
  return () =>
    void queryClient.invalidateQueries({
      queryKey: ["get", "/api/tools/campaigns/presets"],
    });
}

export function useCreateCampaignPreset() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: (body: CampaignPresetInput) =>
      dataOrThrow(tmaFetchApi.POST("/api/tools/campaigns/presets", { body })),
    onSuccess: invalidateCampaignPresets(queryClient),
  });
}

export function useUpdateCampaignPreset(presetId: string) {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: (body: CampaignPresetInput) =>
      dataOrThrow(
        tmaFetchApi.PUT("/api/tools/campaigns/presets/{preset_id}", {
          params: { path: { preset_id: presetId } },
          body,
        }),
      ),
    onSuccess: invalidateCampaignPresets(queryClient),
  });
}

export function useDeleteCampaignPreset() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: (presetId: string) =>
      noContentOrThrow(
        tmaFetchApi.DELETE("/api/tools/campaigns/presets/{preset_id}", {
          params: { path: { preset_id: presetId } },
        }),
      ),
    onSuccess: invalidateCampaignPresets(queryClient),
  });
}

export function useCampaignDraft() {
  return tmaApi.useQuery("get", "/api/tools/campaigns/draft", {}, { staleTime: 0, retry: false });
}

export function useSaveCampaignDraft() {
  return useMutation({
    mutationFn: (body: CampaignDraftPutIn) =>
      dataOrThrow(tmaFetchApi.PUT("/api/tools/campaigns/draft", { body })),
  });
}

export function useDeleteCampaignDraft() {
  return useMutation({
    mutationFn: (expectedRevision: number) =>
      noContentOrThrow(
        tmaFetchApi.DELETE("/api/tools/campaigns/draft", {
          params: { query: { expected_revision: expectedRevision } },
        }),
      ),
  });
}

export function useCampaignAccountContext() {
  return useMutation({
    mutationFn: (actId: string) =>
      dataOrThrow(
        tmaFetchApi.GET("/api/campaigns/ad-account-context", {
          params: { query: { act_id: actId } },
        }),
      ),
  });
}

export function useCampaignAccountPages() {
  return useMutation({
    mutationFn: (actId: string) =>
      dataOrThrow(
        tmaFetchApi.GET("/api/campaigns/ad-account-pages", {
          params: { query: { act_id: actId } },
        }),
      ),
  });
}

export function useCampaignAccountPixels() {
  return useMutation({
    mutationFn: (actId: string) =>
      dataOrThrow(
        tmaFetchApi.GET("/api/campaigns/ad-account-pixels", {
          params: { query: { act_id: actId } },
        }),
      ),
  });
}

export async function uploadCampaignConcepts(
  files: File[],
  uploadId?: string | null,
): Promise<UploadConceptsOut> {
  const result = await tmaFetchApi.POST("/api/tools/campaigns/upload", {
    body: { files: files as unknown as string[], upload_id: uploadId },
    bodySerializer: () => {
      const form = new FormData();
      files.forEach((file) => form.append("files", file));
      if (uploadId) form.append("upload_id", uploadId);
      return form;
    },
  });
  return dataOrThrow(Promise.resolve(result));
}

export function useUploadCampaignConcepts() {
  return useMutation({
    mutationFn: ({ files, uploadId }: { files: File[]; uploadId?: string | null }) =>
      uploadCampaignConcepts(files, uploadId),
  });
}

export function useValidateCampaignConfig() {
  return useMutation({
    mutationFn: (config: CampaignConfig) =>
      dataOrThrow(tmaFetchApi.POST("/api/tools/campaigns/validate", { body: { config } })),
  });
}

export function useLaunchCampaign() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: (body: LaunchIn) =>
      dataOrThrow(tmaFetchApi.POST("/api/tools/campaigns/launch", { body })),
    onSuccess: () =>
      void queryClient.invalidateQueries({
        queryKey: ["get", "/api/tools/campaigns/runs"],
      }),
  });
}

export function useCampaignRunDetails(runIds: string[]) {
  return useQueries({
    queries: runIds.map((runId) => ({
      queryKey: ["get", "/api/tools/campaigns/runs/{run_id}", runId],
      queryFn: () =>
        dataOrThrow(
          tmaFetchApi.GET("/api/tools/campaigns/runs/{run_id}", {
            params: { path: { run_id: runId } },
          }),
        ),
      staleTime: 2_000,
      refetchInterval: (query: Query<RunDetailOut>) =>
        query.state.data &&
        ["succeeded", "failed", "cancelled"].includes(query.state.data.status)
          ? false
          : 2_000,
    })),
  });
}
