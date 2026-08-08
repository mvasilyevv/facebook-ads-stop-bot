import { useEffect, useRef, useState } from "react";
import { useQueryClient, type QueryClient } from "@tanstack/react-query";

import type { OperatorSnapshot } from "@fb/shared/operator/contracts";

export type OperatorRealtimeStatus =
  | "connecting"
  | "connected"
  | "reconnecting";

export interface OperatorWsEvent {
  type: "snapshot_required" | "changed" | "reconcile_required" | "ping";
  sequence: number;
  snapshot_revision: string;
  scopes: string[];
  ts: string;
}

export interface OperatorRealtimeOptions {
  enabled?: boolean;
  path?: string;
  protocols?: string[];
  fetchSnapshot?: OperatorSnapshotFetcher;
  fetchActionProjections?: OperatorActionProjectionFetcher;
  onAuthFailure?: OperatorAuthFailureHandler;
}

/** Refreshes an expired WebSocket credential before any reconnect attempt. */
export type OperatorAuthFailureHandler = () => void | Promise<void>;

/**
 * Auth-aware, typed snapshot fetch supplied by the web or TMA API client.
 * It must perform a real fetch through the provided QueryClient and reject on
 * transport/API errors; cache invalidation is not a reconciliation barrier.
 */
export type OperatorSnapshotFetcher = (
  queryClient: QueryClient,
) => Promise<OperatorSnapshot>;

/**
 * Auth-aware reconciliation for every projection which can render a money
 * action. Implementations must perform real network reads and reject if any
 * active projection cannot be refreshed. Invalidating a cache is not enough:
 * stale rows must stay locked until this promise resolves.
 */
export type OperatorActionProjectionFetcher = (
  queryClient: QueryClient,
) => Promise<void>;

type RealtimeFetcher =
  | OperatorSnapshotFetcher
  | OperatorActionProjectionFetcher
  | OperatorAuthFailureHandler;

const FETCHER_IDENTITIES = new WeakMap<RealtimeFetcher, number>();
let nextFetcherIdentity = 1;

function fetcherIdentity(fetcher: RealtimeFetcher | undefined): number {
  if (!fetcher) return 0;
  const existing = FETCHER_IDENTITIES.get(fetcher);
  if (existing !== undefined) return existing;
  const identity = nextFetcherIdentity++;
  FETCHER_IDENTITIES.set(fetcher, identity);
  return identity;
}

const RECONNECT_DELAYS_MS = [
  1_000, 2_000, 4_000, 8_000, 16_000, 30_000,
] as const;

const WS_EVENT_TYPES = new Set<OperatorWsEvent["type"]>([
  "snapshot_required",
  "changed",
  "reconcile_required",
  "ping",
]);

// Only these DB scopes are proven not to change an action target, its source
// freshness, or its lifecycle. New/unknown scopes fail closed by default.
const NON_ACTION_EVENT_SCOPES = new Set([
  "notification_delivery",
  "observer_config",
]);
const CAMPAIGN_RUN_SCOPE = "campaign_run";
const CAMPAIGN_RUN_LIST_PATH = "/api/tools/campaigns/runs";
const CAMPAIGN_RUN_DETAIL_PATH = "/api/tools/campaigns/runs/{run_id}";
const CAMPAIGN_RUN_SCOPE_PATTERN =
  /^campaign_run:([0-9a-f]{8}-[0-9a-f]{4}-[1-5][0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12})$/;

function campaignRunIdFromScope(scope: string): string | null {
  return CAMPAIGN_RUN_SCOPE_PATTERN.exec(scope)?.[1] ?? null;
}

function isCampaignRunScope(scope: string): boolean {
  return scope === CAMPAIGN_RUN_SCOPE || campaignRunIdFromScope(scope) !== null;
}

function isNonActionScope(scope: string): boolean {
  return NON_ACTION_EVENT_SCOPES.has(scope) || isCampaignRunScope(scope);
}

function revisionNumber(revision: string): bigint | null {
  const match = /^r([0-9a-f]+)$/.exec(revision);
  if (!match) return null;
  try {
    return BigInt(`0x${match[1]}`);
  } catch {
    return null;
  }
}

function isOperatorWsEvent(value: unknown): value is OperatorWsEvent {
  if (typeof value !== "object" || value === null) return false;
  const candidate = value as Partial<OperatorWsEvent>;
  return (
    typeof candidate.type === "string" &&
    WS_EVENT_TYPES.has(candidate.type as OperatorWsEvent["type"]) &&
    Number.isInteger(candidate.sequence) &&
    (candidate.sequence ?? 0) > 0 &&
    typeof candidate.snapshot_revision === "string" &&
    revisionNumber(candidate.snapshot_revision) !== null &&
    Array.isArray(candidate.scopes) &&
    candidate.scopes.every((scope) => typeof scope === "string") &&
    typeof candidate.ts === "string"
  );
}

function snapshotCoversRevision(
  snapshot: OperatorSnapshot,
  expectedRevision: string,
): boolean {
  const actual = revisionNumber(snapshot?.meta?.revision);
  const expected = revisionNumber(expectedRevision);
  return actual !== null && expected !== null && actual >= expected;
}

function eventRequiresActionBarrier(scopes: string[]): boolean {
  return (
    scopes.length === 0 || scopes.some((scope) => !isNonActionScope(scope))
  );
}

/**
 * DB-authoritative operator stream. Reconnects, sequence gaps and every scope
 * which can affect a money action pass the same snapshot + projection barrier.
 * Only an explicit non-action allowlist may use background invalidation.
 */
export function useOperatorRealtime({
  enabled = true,
  path = "/ws/operator",
  protocols,
  fetchSnapshot,
  fetchActionProjections,
  onAuthFailure,
}: OperatorRealtimeOptions = {}): OperatorRealtimeStatus {
  const queryClient = useQueryClient();
  const protocolKey = protocols?.join(",") ?? "";
  const connectionIdentity = [
    enabled ? "1" : "0",
    path,
    protocolKey,
    fetcherIdentity(fetchSnapshot),
    fetcherIdentity(fetchActionProjections),
    fetcherIdentity(onAuthFailure),
  ].join("\u0000");
  const [realtimeState, setRealtimeState] = useState<{
    identity: string;
    status: OperatorRealtimeStatus;
  }>({ identity: connectionIdentity, status: "connecting" });
  const lastSequence = useRef<number | null>(null);

  useEffect(() => {
    if (!enabled || typeof window === "undefined") return;
    const setStatus = (status: OperatorRealtimeStatus) =>
      setRealtimeState({ identity: connectionIdentity, status });
    let disposed = false;
    let socket: WebSocket | null = null;
    let reconnectTimer: number | null = null;
    let reconnectAttempt = 0;
    let hasEverReconciled = false;
    let hasDisconnected = false;
    let needsReconciliation = true;
    let socketGeneration = 0;
    let reconciliationGeneration = 0;
    let reconciliationInFlight = false;
    let reconciliationSocketGeneration = 0;
    let pendingExpectedRevision: string | null = null;
    let pendingCampaignReconciliation = false;
    lastSequence.current = null;

    const invalidate = (
      scopes: string[],
      reconcileAll = false,
      includeSnapshot = true,
    ) => {
      const scopeSet = new Set(scopes);
      const invalidations: Array<Promise<void>> = [];
      if (includeSnapshot) {
        invalidations.push(
          queryClient.invalidateQueries({
            queryKey: ["get", "/api/operator/snapshot"],
          }),
          queryClient.invalidateQueries({
            queryKey: ["get", "/api/operator/cabinets/{cabinet_id}/snapshot"],
          }),
        );
      }
      if (reconcileAll || scopeSet.has("task")) {
        invalidations.push(
          queryClient.invalidateQueries({
            queryKey: ["get", "/api/operator/actions"],
          }),
        );
      }
      if (
        reconcileAll ||
        ["task", "metrics", "ads", "tracker"].some((scope) =>
          scopeSet.has(scope),
        )
      ) {
        invalidations.push(
          queryClient.invalidateQueries({
            queryKey: ["get", "/api/operator/ads"],
          }),
        );
      }
      return Promise.all(invalidations);
    };

    const invalidateCampaignRuns = (scopes: string[], reconcileAll = false) => {
      const campaignScopes = scopes.filter(isCampaignRunScope);
      if (!reconcileAll && campaignScopes.length === 0) {
        return Promise.resolve();
      }
      const invalidateAllDetails =
        reconcileAll || campaignScopes.includes(CAMPAIGN_RUN_SCOPE);
      const runIds = new Set(
        campaignScopes
          .map(campaignRunIdFromScope)
          .filter((runId): runId is string => runId !== null),
      );
      return queryClient.invalidateQueries({
        predicate: ({ queryKey }) => {
          if (queryKey[0] !== "get") return false;
          if (queryKey[1] === CAMPAIGN_RUN_LIST_PATH) return true;
          if (queryKey[1] !== CAMPAIGN_RUN_DETAIL_PATH) return false;
          if (invalidateAllDetails) return true;
          const options = queryKey[2] as
            | { params?: { path?: { run_id?: unknown } } }
            | undefined;
          const runId = options?.params?.path?.run_id;
          return typeof runId === "string" && runIds.has(runId);
        },
      });
    };

    const reconcile = (
      connectedSocket: WebSocket,
      connectedSocketGeneration: number,
      expectedRevision: string,
      reconcileCampaignRuns: boolean,
    ) => {
      const expected = revisionNumber(expectedRevision);
      const pending = pendingExpectedRevision
        ? revisionNumber(pendingExpectedRevision)
        : null;
      if (expected !== null && (pending === null || expected > pending)) {
        pendingExpectedRevision = expectedRevision;
      } else if (pendingExpectedRevision === null) {
        // The event validator has already rejected invalid revisions. Keeping
        // the exact value here also records a same-revision event which arrived
        // while a prior barrier was in flight.
        pendingExpectedRevision = expectedRevision;
      }
      pendingCampaignReconciliation ||= reconcileCampaignRuns;
      needsReconciliation = true;
      setStatus(
        hasEverReconciled || hasDisconnected ? "reconnecting" : "connecting",
      );

      // Coalesce a burst on the same socket. The runner consumes the latest
      // pending revision and loops once more if another event arrives while its
      // network reads are in flight.
      if (
        reconciliationInFlight &&
        reconciliationSocketGeneration === connectedSocketGeneration
      ) {
        return;
      }

      const generation = ++reconciliationGeneration;
      reconciliationInFlight = true;
      reconciliationSocketGeneration = connectedSocketGeneration;

      const isCurrent = () =>
        !disposed &&
        socket === connectedSocket &&
        socketGeneration === connectedSocketGeneration &&
        reconciliationGeneration === generation;

      void (async () => {
        while (isCurrent()) {
          const revisionToCover = pendingExpectedRevision;
          pendingExpectedRevision = null;
          const reconcileCampaignProjection = pendingCampaignReconciliation;
          pendingCampaignReconciliation = false;
          if (!revisionToCover) return;

          try {
            if (!fetchSnapshot) {
              throw new Error("operator snapshot fetcher is not configured");
            }
            if (!fetchActionProjections) {
              throw new Error(
                "operator action projection fetcher is not configured",
              );
            }
            const snapshot = await fetchSnapshot(queryClient);
            if (!isCurrent()) return;
            if (!snapshotCoversRevision(snapshot, revisionToCover)) {
              throw new Error("operator snapshot revision is stale or invalid");
            }

            await fetchActionProjections(queryClient);
            if (!isCurrent()) return;
            if (reconcileCampaignProjection) {
              await invalidateCampaignRuns([], true);
              if (!isCurrent()) return;
            }

            // An event which arrived during either read may have committed
            // after that read began. Consume the coalesced latest revision with
            // another full barrier before unlocking.
            if (pendingExpectedRevision !== null) continue;

            hasEverReconciled = true;
            needsReconciliation = false;
            setStatus("connected");
            return;
          } catch {
            if (!isCurrent()) return;
            pendingCampaignReconciliation ||= reconcileCampaignProjection;
            needsReconciliation = true;
            setStatus(
              hasEverReconciled || hasDisconnected
                ? "reconnecting"
                : "connecting",
            );
            // One queued event is one bounded retry; otherwise wait for the
            // next heartbeat/reconnect rather than creating a retry storm.
            if (pendingExpectedRevision === null) return;
          }
        }
      })().finally(() => {
        if (reconciliationGeneration === generation) {
          reconciliationInFlight = false;
          reconciliationSocketGeneration = 0;
        }
      });
    };

    const connect = () => {
      if (disposed) return;
      const connectedSocketGeneration = ++socketGeneration;
      needsReconciliation = true;
      setStatus(reconnectAttempt ? "reconnecting" : "connecting");
      const protocol = window.location.protocol === "https:" ? "wss" : "ws";
      const url = `${protocol}://${window.location.host}${path}`;
      const connectedSocket = protocolKey
        ? new WebSocket(url, protocolKey.split(",").filter(Boolean))
        : new WebSocket(url);
      socket = connectedSocket;

      connectedSocket.addEventListener("open", () => {
        if (
          disposed ||
          socket !== connectedSocket ||
          socketGeneration !== connectedSocketGeneration
        )
          return;
        reconnectAttempt = 0;
      });
      connectedSocket.addEventListener("message", (message) => {
        if (
          disposed ||
          socket !== connectedSocket ||
          socketGeneration !== connectedSocketGeneration
        )
          return;
        let decoded: unknown;
        try {
          decoded = JSON.parse(String(message.data));
        } catch {
          setStatus("reconnecting");
          connectedSocket.close(1002, "invalid operator event");
          return;
        }
        if (!isOperatorWsEvent(decoded)) {
          setStatus("reconnecting");
          connectedSocket.close(1002, "invalid operator event");
          return;
        }
        const event = decoded;
        const hasGap =
          lastSequence.current !== null &&
          event.sequence !== lastSequence.current + 1;
        lastSequence.current = event.sequence;
        if (event.type === "changed" && event.scopes.some(isCampaignRunScope)) {
          void invalidateCampaignRuns(event.scopes);
        }

        if (
          needsReconciliation ||
          hasGap ||
          event.type === "snapshot_required" ||
          event.type === "reconcile_required" ||
          event.type === "ping" ||
          (event.type === "changed" && eventRequiresActionBarrier(event.scopes))
        ) {
          const reconcileCampaignProjection =
            needsReconciliation ||
            hasGap ||
            event.type === "snapshot_required" ||
            event.type === "reconcile_required";
          reconcile(
            connectedSocket,
            connectedSocketGeneration,
            event.snapshot_revision,
            reconcileCampaignProjection,
          );
          return;
        }
        // A PostgreSQL notification is delivered only after commit. Always
        // reconcile its scopes: source event IDs may commit out of allocation
        // order, while snapshot_revision remains a lost-NOTIFY heartbeat cursor.
        if (event.type === "changed") {
          const campaignOnly =
            event.scopes.length > 0 && event.scopes.every(isCampaignRunScope);
          void Promise.all([invalidate(event.scopes, false, !campaignOnly)]);
        }
      });
      connectedSocket.addEventListener("close", (event) => {
        if (
          disposed ||
          socket !== connectedSocket ||
          socketGeneration !== connectedSocketGeneration
        )
          return;
        socket = null;
        socketGeneration += 1;
        reconciliationGeneration += 1;
        reconciliationInFlight = false;
        reconciliationSocketGeneration = 0;
        pendingExpectedRevision = null;
        pendingCampaignReconciliation = false;
        needsReconciliation = true;
        hasDisconnected = true;
        lastSequence.current = null;
        setStatus("reconnecting");
        const closedGeneration = socketGeneration;
        const scheduleReconnect = () => {
          if (
            disposed ||
            socket !== null ||
            socketGeneration !== closedGeneration
          )
            return;
          const delay =
            RECONNECT_DELAYS_MS[
              Math.min(reconnectAttempt, RECONNECT_DELAYS_MS.length - 1)
            ];
          reconnectAttempt += 1;
          reconnectTimer = window.setTimeout(() => {
            if (
              disposed ||
              socket !== null ||
              socketGeneration !== closedGeneration
            )
              return;
            connect();
          }, delay);
        };

        if (event.code === 1008 && onAuthFailure) {
          // A policy close is the server's explicit signal that the TMA
          // session expired or was revoked. Refresh before reconnecting so we
          // never loop forever with the same credential. A token rotation
          // changes the hook identity and disposes this generation; a failed
          // refresh remains fail-closed and falls back to bounded reconnect.
          void Promise.resolve()
            .then(onAuthFailure)
            .catch(() => undefined)
            .finally(scheduleReconnect);
          return;
        }
        scheduleReconnect();
      });
    };

    connect();
    return () => {
      disposed = true;
      if (reconnectTimer !== null) window.clearTimeout(reconnectTimer);
      socket?.close();
    };
  }, [
    enabled,
    connectionIdentity,
    fetchActionProjections,
    fetchSnapshot,
    onAuthFailure,
    path,
    protocolKey,
    queryClient,
  ]);

  if (realtimeState.identity !== connectionIdentity) {
    return realtimeState.status === "connected"
      ? "reconnecting"
      : realtimeState.status;
  }
  return realtimeState.status;
}
