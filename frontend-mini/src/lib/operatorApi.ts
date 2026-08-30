import { useInfiniteQuery, type QueryClient } from "@tanstack/react-query";
import type {
  OperatorActionsQuery,
  OperatorAdRow,
  OperatorAdsQuery,
  OperatorIncidentsQuery,
  OperatorSnapshotQuery,
} from "@fb/shared/operator/contracts";
import { actionProjectionFromResponse } from "@fb/shared/operator/viewModel";
import { nextCampaignRunsOffset, parseTotalCountHeader } from "@fb/shared";
import {
  dataOrThrow,
  isApiProblem,
  reconcileOperatorReadModels,
  reconcileOperatorSnapshots,
  safeApiProblemMessage,
} from "@fb/operator-api";

import { refreshTmaSession, tmaApi, tmaAuthenticatedFetch, tmaFetchApi } from "./auth";

const operatorApi = tmaApi;

export const tmaOperatorFetch = tmaAuthenticatedFetch;
export { refreshTmaSession };

export function useOperatorSnapshot(query: OperatorSnapshotQuery = {}) {
  return operatorApi.useQuery(
    "get",
    "/api/operator/snapshot",
    { params: { query } },
    {
      staleTime: 10_000,
      retry: (count, error) => count < 2 && !isTerminalOperatorProblem(error),
    },
  );
}

/**
 * Читает уже загруженный снимок из кэша react-query по тому же ключу, что
 * и `useOperatorSnapshot`, но никогда не инициирует собственный запрос
 * (`enabled: false`). TabBar рендерится на каждом экране и не должен плодить
 * сетевой трафик — бейдж просто отражает то, что уже загрузили другие экраны.
 * Если снимок ещё не загружен нигде, `data` останется `undefined` (unknown,
 * не подтверждённый ноль).
 */
export function usePeekOperatorSnapshot(query: OperatorSnapshotQuery = {}) {
  return operatorApi.useQuery(
    "get",
    "/api/operator/snapshot",
    { params: { query } },
    { enabled: false, staleTime: 10_000 },
  );
}

export function useOperatorCabinetSnapshot(
  cabinetId: string,
  query: Omit<OperatorSnapshotQuery, "account_id"> = {},
) {
  return operatorApi.useQuery(
    "get",
    "/api/operator/cabinets/{cabinet_id}/snapshot",
    { params: { path: { cabinet_id: cabinetId }, query } },
    {
      enabled: Boolean(cabinetId),
      staleTime: 10_000,
      retry: (count, error) => count < 2 && !isTerminalOperatorProblem(error),
    },
  );
}

export function useOperatorIncident(incidentId: string) {
  return operatorApi.useQuery(
    "get",
    "/api/operator/incidents/{incident_id}",
    { params: { path: { incident_id: incidentId } } },
    { enabled: Boolean(incidentId), staleTime: 5_000 },
  );
}

/**
 * Курсорное «Показать ещё» (issue #340) поверх страничной ручки: `/incidents`
 * не отдаёт cursor, только `page`/`page_size`/`total`/`pages`, поэтому `page`
 * играет роль курсора — react-query подставляет его сам через pageParamName,
 * поэтому он не входит в query (иначе засорял бы ключ и накопление страниц).
 */
export function useOperatorIncidents(query: Omit<OperatorIncidentsQuery, "page"> = {}) {
  return operatorApi.useInfiniteQuery(
    "get",
    "/api/operator/incidents",
    { params: { query } },
    {
      pageParamName: "page",
      initialPageParam: 1,
      getNextPageParam: (page) => (page.page < page.pages ? page.page + 1 : undefined),
      staleTime: 5_000,
    },
  );
}

/** Mandatory realtime barrier using the TMA-authenticated fetch transport. */
export async function fetchOperatorSnapshotForRealtime(
  queryClient: QueryClient,
) {
  return reconcileOperatorSnapshots(queryClient, () =>
    queryClient.fetchQuery(
      operatorApi.queryOptions(
        "get",
        "/api/operator/snapshot",
        { params: { query: {} } },
        { retry: false, staleTime: 0 },
      ),
    ),
  );
}

/** TMA-authenticated action projection barrier for the realtime money gate. */
export async function fetchOperatorActionProjectionsForRealtime(
  queryClient: QueryClient,
): Promise<void> {
  const adsKey = ["get", "/api/operator/ads"] as const;
  const actionsKey = ["get", "/api/operator/actions"] as const;
  const readModelReconciliation = reconcileOperatorReadModels(queryClient);
  // Keep the concurrent auxiliary promise observed even if a critical ads
  // projection rejects before the awaited join below.
  void readModelReconciliation.catch(() => undefined);

  queryClient.removeQueries({ queryKey: adsKey, type: "inactive" });
  queryClient.removeQueries({ queryKey: actionsKey, type: "inactive" });
  await Promise.all([
    queryClient.cancelQueries(
      { queryKey: adsKey, type: "active" },
      { silent: true },
    ),
    queryClient.cancelQueries(
      { queryKey: actionsKey, type: "active" },
      { silent: true },
    ),
  ]);
  await Promise.all([
    queryClient.invalidateQueries({ queryKey: adsKey, refetchType: "none" }),
    queryClient.invalidateQueries({
      queryKey: actionsKey,
      refetchType: "none",
    }),
  ]);

  const hasActiveAds =
    queryClient.getQueryCache().findAll({ queryKey: adsKey, type: "active" })
      .length > 0;
  await Promise.all([
    hasActiveAds
      ? queryClient.refetchQueries(
          { queryKey: adsKey, type: "active", stale: true },
          { throwOnError: true },
        )
      : queryClient
          .fetchQuery(
            operatorApi.queryOptions(
              "get",
              "/api/operator/ads",
              { params: { query: {} } },
              { retry: false, staleTime: 0 },
            ),
          )
          .then(() => undefined),
    queryClient.refetchQueries(
      { queryKey: actionsKey, type: "active", stale: true },
      { throwOnError: true },
    ),
  ]);
  queryClient.removeQueries({ queryKey: adsKey, type: "inactive" });
  queryClient.removeQueries({ queryKey: actionsKey, type: "inactive" });
  await readModelReconciliation;
}

/** Typed endpoint used by the re-login recovery control. */
export function useOperatorRetryScan() {
  return operatorApi.useMutation("post", "/api/operator/scan/retry");
}

export function useOperatorActions(query: OperatorActionsQuery = {}) {
  return operatorApi.useInfiniteQuery(
    "get",
    "/api/operator/actions",
    { params: { query: { limit: 30, ...query } } },
    {
      pageParamName: "before_id",
      initialPageParam: null,
      getNextPageParam: (page) => page.next_cursor ?? undefined,
      staleTime: 10_000,
    },
  );
}

export function useOperatorAction(actionId: string) {
  const numericId = Number(actionId);
  return operatorApi.useQuery(
    "get",
    "/api/operator/actions",
    {
      params: {
        query: {
          limit: 1,
          before_id:
            Number.isSafeInteger(numericId) && numericId > 0
              ? numericId + 1
              : undefined,
        },
      },
    },
    {
      enabled: Number.isSafeInteger(numericId) && numericId > 0,
      select: (response) => actionProjectionFromResponse(response, actionId),
      staleTime: 5_000,
    },
  );
}

export function useOperatorAds(query: OperatorAdsQuery = {}) {
  return operatorApi.useQuery(
    "get",
    "/api/operator/ads",
    { params: { query } },
    { staleTime: 10_000 },
  );
}

/**
 * Каталог объявлений как курсорное «Показать ещё» (issue #340): `page` из
 * `OperatorAdsQuery` подставляет react-query через pageParamName, поэтому
 * здесь он исключён из входного query. `useOperatorAds` выше остаётся
 * однострочным чтением (карточка объявления) — накопление ей не нужно.
 */
export function useOperatorAdsList(query: Omit<OperatorAdsQuery, "page"> = {}) {
  return operatorApi.useInfiniteQuery(
    "get",
    "/api/operator/ads",
    { params: { query } },
    {
      pageParamName: "page",
      initialPageParam: 1,
      getNextPageParam: (page) => (page.page < page.pages ? page.page + 1 : undefined),
      staleTime: 10_000,
    },
  );
}

/** Force a post-confirmation network read before any TMA money command. */
export async function fetchOperatorAdForCommand(
  queryClient: QueryClient,
  fbAdId: string,
): Promise<OperatorAdRow & { as_of: string; delivery_status: string }> {
  const response = await queryClient.fetchQuery(
    operatorApi.queryOptions(
      "get",
      "/api/operator/ads",
      { params: { query: { search: fbAdId, page: 1, page_size: 10 } } },
      { retry: false, staleTime: 0 },
    ),
  );
  const row = response.rows.find((candidate) => candidate.fb_ad_id === fbAdId);
  if (
    response.state !== "ready" ||
    !row ||
    row.data_state !== "ready" ||
    row.active_action ||
    !row.as_of ||
    !row.delivery_status
  ) {
    throw new Error("Актуальное состояние объявления не подтверждено");
  }
  return row as OperatorAdRow & { as_of: string; delivery_status: string };
}

export function usePauseOperatorAd() {
  return operatorApi.useMutation("post", "/api/operator/ads/{ad_id}/pause");
}

export function useActivateOperatorAd() {
  return operatorApi.useMutation("post", "/api/operator/ads/{ad_id}/activate");
}

export function useAcknowledgeOperatorIncident() {
  return operatorApi.useMutation(
    "post",
    "/api/operator/incidents/{incident_id}/ack",
  );
}

export function useResolveTmaNavigation() {
  return operatorApi.useMutation("post", "/api/tma/navigation/resolve");
}

/** Campaign history remains available beside the full TMA creation flow. */
export function useCampaignRuns() {
  return operatorApi.useQuery("get", "/api/tools/campaigns/runs", {
    params: { query: { limit: 50, offset: 0 } },
  });
}

const CAMPAIGN_RUNS_HISTORY_PAGE_SIZE = 50;

/**
 * История заливов как курсорное «Показать ещё» (issue #340), тот же паттерн,
 * что и в /actions. `/api/tools/campaigns/runs` честно поддерживает
 * limit/offset и возвращает total заголовком `X-Total-Count`; типизированный
 * `operatorApi.useQuery` заголовков не отдаёт, поэтому здесь используется
 * `tmaFetchApi` напрямую — так же, как web использует `generatedFetchApi`.
 */
export function useRunsHistory(params?: { status?: string }) {
  return useInfiniteQuery({
    queryKey: ["get", "/api/tools/campaigns/runs", "history", params?.status ?? null],
    queryFn: async ({ pageParam }) => {
      const result = await tmaFetchApi.GET("/api/tools/campaigns/runs", {
        params: {
          query: {
            status: params?.status,
            limit: CAMPAIGN_RUNS_HISTORY_PAGE_SIZE,
            offset: pageParam,
          },
        },
      });
      return {
        runs: await dataOrThrow(Promise.resolve(result)),
        total: parseTotalCountHeader(result.response.headers.get("X-Total-Count")),
        offset: pageParam,
        limit: CAMPAIGN_RUNS_HISTORY_PAGE_SIZE,
      };
    },
    initialPageParam: 0,
    getNextPageParam: (lastPage) => nextCampaignRunsOffset(lastPage) ?? undefined,
    staleTime: 15_000,
  });
}

export function useCampaignRun(runId: string, enabled = true) {
  return operatorApi.useQuery(
    "get",
    "/api/tools/campaigns/runs/{run_id}",
    { params: { path: { run_id: runId } } },
    { enabled: enabled && Boolean(runId) },
  );
}

export function useAbortCampaignRun() {
  return operatorApi.useMutation(
    "post",
    "/api/tools/campaigns/runs/{run_id}/abort",
  );
}

export function useResumeCampaignRun() {
  return operatorApi.useMutation(
    "post",
    "/api/tools/campaigns/runs/{run_id}/resume",
  );
}

export function operatorProblemMessage(error: unknown): string {
  // Fallback — подсказка к действию, а не повтор заголовка: дублирование одной
  // и той же строки в карточке ошибки оператору ничего не даёт.
  return safeApiProblemMessage(
    error,
    "Сервер не подтвердил данные. Повторите попытку.",
  );
}

export function operatorIncidentProblemMessage(error: unknown): string {
  return safeApiProblemMessage(error, "Журнал инцидентов временно недоступен");
}

function isTerminalOperatorProblem(error: unknown): boolean {
  return (
    isApiProblem(error) &&
    ["unauthorized", "forbidden", "validation_error"].includes(
      error.code.toLowerCase(),
    )
  );
}
