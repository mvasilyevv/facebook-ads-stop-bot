import type {
  OperatorActionsQuery,
  OperatorAdRow,
  OperatorAdsQuery,
  OperatorIncidentsQuery,
  OperatorSnapshotQuery,
} from "@fb/shared/operator/contracts";
import type { operations } from "@fb/shared/api/generated";
import { actionProjectionFromResponse } from "@fb/shared/operator/viewModel";
import {
  isApiProblem,
  reconcileOperatorReadModels,
  reconcileOperatorSnapshots,
  safeApiProblemMessage,
} from "@fb/operator-api";
import { type QueryClient } from "@tanstack/react-query";

import { generatedApi as operatorApi } from "./generatedClient";

export type OperatorEventsQuery = NonNullable<
  operations["get_operator_events_api_operator_events_get"]["parameters"]["query"]
>;

/** OpenAPI-generated analytics event feed. */
export function useOperatorEvents(query: OperatorEventsQuery = {}) {
  return operatorApi.useQuery(
    "get",
    "/api/operator/events",
    { params: { query } },
    { staleTime: 30_000 },
  );
}

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

export function useOperatorIncidents(query: OperatorIncidentsQuery = {}) {
  return operatorApi.useQuery(
    "get",
    "/api/operator/incidents",
    { params: { query } },
    { staleTime: 5_000 },
  );
}

/** Mandatory realtime barrier: force a typed network fetch and propagate errors. */
export async function fetchOperatorSnapshotForRealtime(queryClient: QueryClient) {
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

/**
 * Reconcile every cache which can render a money action before realtime is
 * marked connected. Inactive filtered pages are discarded so that mounting
 * them later cannot expose a stale action while their background fetch runs.
 */
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
  // Refetch must start after the WS event. Joining a pre-event in-flight read
  // would make a successful promise an invalid safety barrier.
  await Promise.all([
    queryClient.cancelQueries({ queryKey: adsKey, type: "active" }, { silent: true }),
    queryClient.cancelQueries({ queryKey: actionsKey, type: "active" }, { silent: true }),
  ]);
  await Promise.all([
    queryClient.invalidateQueries({ queryKey: adsKey, refetchType: "none" }),
    queryClient.invalidateQueries({ queryKey: actionsKey, refetchType: "none" }),
  ]);

  const hasActiveAds =
    queryClient.getQueryCache().findAll({ queryKey: adsKey, type: "active" }).length > 0;
  await Promise.all([
    // An authenticated read is mandatory. Refresh every active filtered/paged
    // view; if none is mounted, fetch the canonical first page instead.
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
  // A query can unmount while its refetch is in flight. Drop it again before
  // resolving so a later mount cannot render that now-inactive stale row.
  queryClient.removeQueries({ queryKey: adsKey, type: "inactive" });
  queryClient.removeQueries({ queryKey: actionsKey, type: "inactive" });
  await readModelReconciliation;
}

/** Canonical typed mutation for the re-login recovery scan command. */
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
          before_id: Number.isSafeInteger(numericId) && numericId > 0 ? numericId + 1 : undefined,
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

export function useOperatorAds(query: OperatorAdsQuery = {}, options: { enabled?: boolean } = {}) {
  return operatorApi.useQuery(
    "get",
    "/api/operator/ads",
    { params: { query } },
    { staleTime: 10_000, ...options },
  );
}

/** Force a post-confirmation network read before any operator money command. */
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
  return operatorApi.useMutation("post", "/api/operator/incidents/{incident_id}/ack");
}

export function operatorProblemMessage(error: unknown): string {
  // Fallback — это подсказка к действию, а не повтор заголовка: ErrorState уже
  // сообщил, что именно недоступно, и дублирование строки ничего не добавляет.
  return safeApiProblemMessage(
    error,
    "Сервер не подтвердил данные. Повторите попытку; если не помогает — откройте «Источники и воркеры».",
  );
}

export function operatorIncidentProblemMessage(error: unknown): string {
  return safeApiProblemMessage(error, "Журнал инцидентов временно недоступен");
}

function isTerminalOperatorProblem(error: unknown): boolean {
  return (
    isApiProblem(error) &&
    ["unauthorized", "forbidden", "validation_error"].includes(error.code.toLowerCase())
  );
}
