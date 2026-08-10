import type { QueryClient } from "@tanstack/react-query";
import type { OperatorSnapshot } from "@fb/shared/operator/contracts";

const OPERATOR_SNAPSHOT_QUERY_KEYS = [
  ["get", "/api/operator/snapshot"],
  ["get", "/api/operator/cabinets/{cabinet_id}/snapshot"],
] as const;

/**
 * Transport adapter for the mandatory canonical global snapshot read.
 * Implementations must perform an authenticated network fetch and reject on
 * transport, API, or runtime-validation failures.
 */
export type OperatorCanonicalSnapshotFetcher = () => Promise<OperatorSnapshot>;

/**
 * Mandatory realtime snapshot barrier shared by web and TMA.
 *
 * A post-event canonical global read always runs, every active global/cabinet
 * variant is refetched, and the oldest returned revision is used by realtime
 * so one lagging projection keeps money actions locked.
 */
export async function reconcileOperatorSnapshots(
  queryClient: QueryClient,
  fetchCanonicalSnapshot: OperatorCanonicalSnapshotFetcher,
): Promise<OperatorSnapshot> {
  OPERATOR_SNAPSHOT_QUERY_KEYS.forEach((queryKey) =>
    queryClient.removeQueries({ queryKey, type: "inactive" }),
  );

  // Never join a request which may have started before the websocket commit.
  await Promise.all(
    OPERATOR_SNAPSHOT_QUERY_KEYS.map((queryKey) =>
      queryClient.cancelQueries({ queryKey }, { silent: true }),
    ),
  );
  await Promise.all(
    OPERATOR_SNAPSHOT_QUERY_KEYS.map((queryKey) =>
      queryClient.invalidateQueries({ queryKey, refetchType: "none" }),
    ),
  );

  const activeQueries = OPERATOR_SNAPSHOT_QUERY_KEYS.flatMap((queryKey) =>
    queryClient.getQueryCache().findAll({ queryKey, type: "active" }),
  );
  const canonicalSnapshotPromise = fetchCanonicalSnapshot();
  await Promise.all([
    canonicalSnapshotPromise,
    ...OPERATOR_SNAPSHOT_QUERY_KEYS.map((queryKey) =>
      queryClient.refetchQueries(
        { queryKey, type: "active", stale: true },
        { throwOnError: true },
      ),
    ),
  ]);

  const snapshots = [
    await canonicalSnapshotPromise,
    ...activeQueries.flatMap((query) => {
      const value = queryClient.getQueryData<OperatorSnapshot>(query.queryKey);
      return value ? [value] : [];
    }),
  ];

  // A variant may unmount while its post-event refetch is in flight. Remove it
  // before unlocking so it cannot later render a stale cached snapshot.
  OPERATOR_SNAPSHOT_QUERY_KEYS.forEach((queryKey) =>
    queryClient.removeQueries({ queryKey, type: "inactive" }),
  );
  return oldestOperatorSnapshot(snapshots);
}

function oldestOperatorSnapshot(
  snapshots: OperatorSnapshot[],
): OperatorSnapshot {
  return snapshots.reduce((oldest, candidate) => {
    const oldestRevision = operatorRevisionValue(oldest.meta.revision);
    const candidateRevision = operatorRevisionValue(candidate.meta.revision);
    if (candidateRevision === null) return candidate;
    if (oldestRevision === null) return oldest;
    return candidateRevision < oldestRevision ? candidate : oldest;
  });
}

function operatorRevisionValue(revision: string): bigint | null {
  const match = /^r([0-9a-f]+)$/.exec(revision);
  if (!match) return null;
  try {
    return BigInt(`0x${match[1]}`);
  } catch {
    return null;
  }
}

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
