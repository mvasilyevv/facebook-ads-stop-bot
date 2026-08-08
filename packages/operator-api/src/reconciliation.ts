import type { QueryClient } from "@tanstack/react-query";

/**
 * Read models which are not part of the operator snapshot but are rendered
 * alongside it. A websocket commit must make every cached variant stale and
 * wait for mounted variants to finish a post-commit read before realtime can
 * be reported as connected again.
 */
export const OPERATOR_REALTIME_READ_MODEL_PATHS = [
  "/api/analytics/performance",
  "/api/analytics/live-budget",
  "/api/analytics/daypart",
  "/api/operator/events",
] as const;

/**
 * Reconcile non-money projections without making their availability a global
 * money-action dependency. Query errors remain on their own query state, while
 * this promise still waits for every active post-commit refetch to settle.
 */
export async function reconcileOperatorReadModels(
  queryClient: QueryClient,
): Promise<void> {
  const filters = OPERATOR_REALTIME_READ_MODEL_PATHS.map((path) => ({
    queryKey: ["get", path] as const,
  }));

  // Do not join a request which started before the websocket commit.
  await Promise.all(
    filters.map(({ queryKey }) =>
      queryClient.cancelQueries({ queryKey, type: "active" }, { silent: true }),
    ),
  );

  // Inactive variants must fetch when mounted; active variants are refreshed
  // explicitly below so their completion is an awaitable reconciliation gate.
  await Promise.all(
    filters.map(({ queryKey }) =>
      queryClient.invalidateQueries({ queryKey, refetchType: "none" }),
    ),
  );

  await Promise.all(
    filters.map(async ({ queryKey }) => {
      try {
        await queryClient.refetchQueries(
          { queryKey, type: "active", stale: true },
          { throwOnError: false },
        );
      } catch {
        // The owning query exposes the failure as unavailable. Analytics or
        // history outages must not deadlock the global money-action barrier.
      }
    }),
  );
}
