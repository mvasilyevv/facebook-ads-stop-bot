/** Generated OpenAPI API layer for the campaign creator. */
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import type { CampaignWizardCampaign } from "@fb/features/campaigns";
import type { components } from "@fb/shared/api/generated";
import { GeneratedApiError, dataOrThrow, noContentOrThrow } from "@fb/operator-api";
import { generatedApi, generatedFetchApi } from "./generatedClient";

export type PresetOut = components["schemas"]["PresetOut"];
export type PresetIn = components["schemas"]["PresetIn"];
export type UploadedConceptOut = components["schemas"]["UploadedConceptOut"];
export type UploadConceptsOut = components["schemas"]["UploadConceptsOut"];
export type CampaignStructure = CampaignWizardCampaign;
export type AdTextConfig = components["schemas"]["AdTextIn"];
export type CampaignConfig = components["schemas"]["CampaignConfigIn"];
export type AdsetPlanOut = components["schemas"]["AdsetPlanOut"];
export type CampaignPlanOut = components["schemas"]["CampaignPlanOut"];
export type ValidatePlanOut = components["schemas"]["ValidatePlanOut"];
export type LaunchIn = components["schemas"]["LaunchIn"];
export type LaunchOut = components["schemas"]["LaunchOut"];
export type RunSummaryOut = components["schemas"]["RunSummaryOut"];
export type RunDetailOut = components["schemas"]["RunDetailOut"];
export type RunCommandOut = components["schemas"]["RunCommandOut"];
export type RunStatus = RunSummaryOut["status"];
export type AdAccountContextOut = components["schemas"]["AdAccountContextResponse"];
export type AdAccountPagesOut = components["schemas"]["AdAccountPagesResponse"];
export type CampaignDraftDocument = components["schemas"]["CampaignDraftDocument"];
export type CampaignDraftEnvelope = components["schemas"]["CampaignDraftEnvelope"];
export type CampaignDraftPutIn = components["schemas"]["CampaignDraftPutIn"];

export const RUN_STATUS_LABELS: Record<RunStatus, string> = {
  queued: "В очереди",
  uniquifying: "Уникализация",
  uploading: "Загрузка",
  creating: "Создание",
  succeeded: "Готово",
  failed: "Ошибка",
  cancelled: "Отменено",
};

/** Retry only the bounded Vision warm-up failure; validation and Meta rejections are final. */
export function shouldRetryVisionMetadata(failureCount: number, error: unknown): boolean {
  return error instanceof GeneratedApiError && error.status === 503 && failureCount < 3;
}

export function visionMetadataRetryDelay(attemptIndex: number): number {
  return Math.min(1_000 * 2 ** attemptIndex, 4_000);
}

export function useCampaignDraft() {
  return generatedApi.useQuery(
    "get",
    "/api/tools/campaigns/draft",
    {},
    { staleTime: 0, retry: false },
  );
}

export function useSaveCampaignDraft() {
  return useMutation({
    mutationFn: (body: CampaignDraftPutIn) =>
      dataOrThrow(generatedFetchApi.PUT("/api/tools/campaigns/draft", { body })),
  });
}

export function useDeleteCampaignDraft() {
  return useMutation({
    mutationFn: (expectedRevision: number) =>
      noContentOrThrow(
        generatedFetchApi.DELETE("/api/tools/campaigns/draft", {
          params: { query: { expected_revision: expectedRevision } },
        }),
      ),
  });
}

export function useAdAccountContext() {
  return useMutation({
    mutationFn: (act_id: string) =>
      dataOrThrow(
        generatedFetchApi.GET("/api/campaigns/ad-account-context", {
          params: { query: { act_id } },
        }),
      ),
    retry: shouldRetryVisionMetadata,
    retryDelay: visionMetadataRetryDelay,
  });
}
export function useAdAccountPages() {
  return useMutation({
    mutationFn: (act_id: string) =>
      dataOrThrow(
        generatedFetchApi.GET("/api/campaigns/ad-account-pages", { params: { query: { act_id } } }),
      ),
    retry: shouldRetryVisionMetadata,
    retryDelay: visionMetadataRetryDelay,
  });
}
export function usePresets() {
  return generatedApi.useQuery("get", "/api/tools/campaigns/presets", {}, { staleTime: 30_000 });
}
function invalidatePresets(qc: ReturnType<typeof useQueryClient>) {
  return () => void qc.invalidateQueries({ queryKey: ["get", "/api/tools/campaigns/presets"] });
}
export function useCreatePreset() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (body: PresetIn) =>
      dataOrThrow(generatedFetchApi.POST("/api/tools/campaigns/presets", { body })),
    onSuccess: invalidatePresets(qc),
  });
}
export function useUpdatePreset(presetId: string) {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (body: PresetIn) =>
      dataOrThrow(
        generatedFetchApi.PUT("/api/tools/campaigns/presets/{preset_id}", {
          params: { path: { preset_id: presetId } },
          body,
        }),
      ),
    onSuccess: invalidatePresets(qc),
  });
}
export function useDeletePreset() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (preset_id: string) =>
      noContentOrThrow(
        generatedFetchApi.DELETE("/api/tools/campaigns/presets/{preset_id}", {
          params: { path: { preset_id } },
        }),
      ),
    onSuccess: invalidatePresets(qc),
  });
}

/** Serialize repeated UploadFile fields while keeping the generated operation contract. */
export async function uploadConcepts(
  files: File[],
  uploadId?: string | null,
): Promise<UploadConceptsOut> {
  const result = await generatedFetchApi.POST("/api/tools/campaigns/upload", {
    // OpenAPI represents `format: binary` as string; bodySerializer provides the
    // browser-native File values without bypassing the generated operation.
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
export function useValidateConfig() {
  return useMutation({
    mutationFn: (config: CampaignConfig) =>
      dataOrThrow(generatedFetchApi.POST("/api/tools/campaigns/validate", { body: { config } })),
  });
}
export function useLaunchCampaign() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (body: LaunchIn) =>
      dataOrThrow(generatedFetchApi.POST("/api/tools/campaigns/launch", { body })),
    onSuccess: () => void qc.invalidateQueries({ queryKey: ["get", "/api/tools/campaigns/runs"] }),
  });
}
export function useRuns(params?: { status?: string; limit?: number; offset?: number }) {
  return useQuery({
    queryKey: ["get", "/api/tools/campaigns/runs", params],
    queryFn: async () => {
      const result = await generatedFetchApi.GET("/api/tools/campaigns/runs", {
        params: {
          query: {
            status: params?.status,
            limit: params?.limit ?? 50,
            offset: params?.offset ?? 0,
          },
        },
      });
      return {
        data: await dataOrThrow(Promise.resolve(result)),
        total: Number(result.response.headers.get("X-Total-Count")) || null,
      };
    },
    staleTime: 15_000,
  });
}
export function useRunDetail(runId: string | null) {
  return generatedApi.useQuery(
    "get",
    "/api/tools/campaigns/runs/{run_id}",
    { params: { path: { run_id: runId ?? "" } } },
    { enabled: !!runId, staleTime: 5_000 },
  );
}
export function useAbortCampaignRun() {
  return generatedApi.useMutation("post", "/api/tools/campaigns/runs/{run_id}/abort");
}

export function useResumeCampaignRun() {
  return generatedApi.useMutation("post", "/api/tools/campaigns/runs/{run_id}/resume");
}
