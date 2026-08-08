import { QueryClient, QueryObserver } from "@tanstack/react-query";
import { afterEach, describe, expect, it, vi } from "vitest";

import type { OperatorSnapshot } from "@fb/shared/operator/contracts";
import { makeOperatorSnapshot } from "@fb/shared/operator/testFixture";

import {
  fetchOperatorActionProjectionsForRealtime,
  fetchOperatorSnapshotForRealtime,
} from "@/lib/api/operator";

const ACTIVE_FILTERED_ADS_KEY = [
  "get",
  "/api/operator/ads",
  { params: { query: { delivery_status: "ACTIVE", page: 2 } } },
] as const;
const INACTIVE_FILTERED_ADS_KEY = [
  "get",
  "/api/operator/ads",
  { params: { query: { search: "stale cached ad", page: 4 } } },
] as const;
const ACTIVE_TODAY_SNAPSHOT_KEY = [
  "get",
  "/api/operator/snapshot",
  { params: { query: { window: "today" } } },
] as const;
const INACTIVE_SEVEN_DAY_SNAPSHOT_KEY = [
  "get",
  "/api/operator/snapshot",
  { params: { query: { window: "7d" } } },
] as const;

function snapshot(revision: `r${string}`): OperatorSnapshot {
  const value = makeOperatorSnapshot();
  return {
    ...value,
    meta: {
      ...value.meta,
      revision,
      sequence: Number.parseInt(revision.slice(1), 16),
    },
  };
}

function deferred<T>() {
  let resolve!: (value: T) => void;
  let reject!: (reason?: unknown) => void;
  const promise = new Promise<T>((resolvePromise, rejectPromise) => {
    resolve = resolvePromise;
    reject = rejectPromise;
  });
  return { promise, reject, resolve };
}

describe("operator action projection reconciliation", () => {
  const unsubscribers: Array<() => void> = [];

  afterEach(() => {
    for (const unsubscribe of unsubscribers.splice(0)) unsubscribe();
  });

  it("drops inactive filtered caches and waits for the active filtered ads read", async () => {
    const queryClient = new QueryClient({
      defaultOptions: { queries: { retry: false } },
    });
    const activeRead = deferred<{ items: Array<{ delivery_status: string }> }>();
    const activeQuery = vi.fn(() => activeRead.promise);
    const invalidateSpy = vi.spyOn(queryClient, "invalidateQueries");
    queryClient.setQueryData(INACTIVE_FILTERED_ADS_KEY, {
      items: [{ delivery_status: "ACTIVE" }],
    });
    const observer = new QueryObserver(queryClient, {
      queryKey: ACTIVE_FILTERED_ADS_KEY,
      queryFn: activeQuery,
      initialData: { items: [{ delivery_status: "ACTIVE" }] },
      staleTime: Number.POSITIVE_INFINITY,
    });
    const unsubscribe = observer.subscribe(() => undefined);
    unsubscribers.push(unsubscribe);

    let settled = false;
    const barrier = fetchOperatorActionProjectionsForRealtime(queryClient).then(() => {
      settled = true;
    });
    await vi.waitFor(() => expect(activeQuery).toHaveBeenCalledOnce());

    expect(queryClient.getQueryData(INACTIVE_FILTERED_ADS_KEY)).toBeUndefined();
    expect(settled).toBe(false);

    // The page may unmount while its authoritative refetch is in flight. The
    // second inactive-cache sweep must remove the result before unlock.
    unsubscribers.pop()?.();
    activeRead.resolve({ items: [{ delivery_status: "INACTIVE" }] });
    await barrier;
    expect(settled).toBe(true);
    expect(queryClient.getQueryData(ACTIVE_FILTERED_ADS_KEY)).toBeUndefined();
    for (const path of [
      "/api/analytics/performance",
      "/api/analytics/live-budget",
      "/api/analytics/daypart",
      "/api/operator/events",
    ]) {
      expect(invalidateSpy).toHaveBeenCalledWith({
        queryKey: ["get", path],
        refetchType: "none",
      });
    }
  });

  it("waits for active analytics and event projections to settle", async () => {
    const queryClient = new QueryClient({
      defaultOptions: { queries: { retry: false } },
    });
    const analyticsRead = deferred<{ state: string }>();
    const eventsRead = deferred<unknown[]>();
    const analyticsQuery = vi.fn(() => analyticsRead.promise);
    const eventsQuery = vi.fn(() => eventsRead.promise);
    const activeAds = new QueryObserver(queryClient, {
      queryKey: ACTIVE_FILTERED_ADS_KEY,
      queryFn: vi.fn().mockResolvedValue({ items: [] }),
      initialData: { items: [] },
      staleTime: Number.POSITIVE_INFINITY,
    });
    const analytics = new QueryObserver(queryClient, {
      queryKey: ["get", "/api/analytics/performance", { params: { query: { period: "today" } } }],
      queryFn: analyticsQuery,
      initialData: { state: "ready" },
      staleTime: Number.POSITIVE_INFINITY,
    });
    const events = new QueryObserver(queryClient, {
      queryKey: ["get", "/api/operator/events", { params: { query: { limit: 500 } } }],
      queryFn: eventsQuery,
      initialData: [],
      staleTime: Number.POSITIVE_INFINITY,
    });
    unsubscribers.push(
      activeAds.subscribe(() => undefined),
      analytics.subscribe(() => undefined),
      events.subscribe(() => undefined),
    );

    let settled = false;
    const barrier = fetchOperatorActionProjectionsForRealtime(queryClient).then(() => {
      settled = true;
    });
    await vi.waitFor(() => expect(analyticsQuery).toHaveBeenCalledOnce());
    await vi.waitFor(() => expect(eventsQuery).toHaveBeenCalledOnce());
    expect(settled).toBe(false);

    analyticsRead.resolve({ state: "ready" });
    await Promise.resolve();
    expect(settled).toBe(false);
    eventsRead.resolve([]);
    await barrier;
    expect(settled).toBe(true);
  });

  it("does not complete reconciliation when a read-model cache cannot be invalidated", async () => {
    const queryClient = new QueryClient({
      defaultOptions: { queries: { retry: false } },
    });
    const activeAds = new QueryObserver(queryClient, {
      queryKey: ACTIVE_FILTERED_ADS_KEY,
      queryFn: vi.fn().mockResolvedValue({ items: [] }),
      initialData: { items: [] },
      staleTime: Number.POSITIVE_INFINITY,
    });
    unsubscribers.push(activeAds.subscribe(() => undefined));
    const originalInvalidate = queryClient.invalidateQueries.bind(queryClient);
    vi.spyOn(queryClient, "invalidateQueries").mockImplementation((filters, options) => {
      if (
        filters?.queryKey?.[0] === "get" &&
        filters?.queryKey?.[1] === "/api/analytics/performance"
      ) {
        return Promise.reject(new Error("analytics cache unavailable"));
      }
      return originalInvalidate(filters, options);
    });

    await expect(fetchOperatorActionProjectionsForRealtime(queryClient)).rejects.toThrow(
      "analytics cache unavailable",
    );
  });

  it("rejects when an active filtered ads projection cannot be refreshed", async () => {
    const queryClient = new QueryClient({
      defaultOptions: { queries: { retry: false } },
    });
    const observer = new QueryObserver(queryClient, {
      queryKey: ACTIVE_FILTERED_ADS_KEY,
      queryFn: vi.fn().mockRejectedValue(new Error("ads unavailable")),
      initialData: { items: [{ delivery_status: "ACTIVE" }] },
      staleTime: Number.POSITIVE_INFINITY,
    });
    unsubscribers.push(observer.subscribe(() => undefined));

    await expect(fetchOperatorActionProjectionsForRealtime(queryClient)).rejects.toThrow(
      "ads unavailable",
    );
  });

  it("replaces an active ads read which started before the websocket event", async () => {
    const queryClient = new QueryClient({
      defaultOptions: { queries: { retry: false } },
    });
    const postEventRead = deferred<{ items: never[] }>();
    let firstSignal: AbortSignal | undefined;
    const queryFn = vi.fn(({ signal }: { signal: AbortSignal }) => {
      if (!firstSignal) {
        firstSignal = signal;
        return new Promise<{ items: never[] }>((_resolve, reject) => {
          signal.addEventListener(
            "abort",
            () => reject(new DOMException("cancelled", "AbortError")),
            { once: true },
          );
        });
      }
      return postEventRead.promise;
    });
    const observer = new QueryObserver(queryClient, {
      queryKey: ACTIVE_FILTERED_ADS_KEY,
      queryFn,
    });
    unsubscribers.push(observer.subscribe(() => undefined));
    await vi.waitFor(() => expect(queryFn).toHaveBeenCalledOnce());

    let settled = false;
    const barrier = fetchOperatorActionProjectionsForRealtime(queryClient).then(() => {
      settled = true;
    });
    await vi.waitFor(() => expect(queryFn).toHaveBeenCalledTimes(2));

    expect(firstSignal?.aborted).toBe(true);
    expect(settled).toBe(false);
    postEventRead.resolve({ items: [] });
    await barrier;
    expect(settled).toBe(true);
  });

  it("refreshes the active snapshot variant and drops inactive snapshot caches", async () => {
    const queryClient = new QueryClient({
      defaultOptions: { queries: { retry: false } },
    });
    queryClient.setQueryData(INACTIVE_SEVEN_DAY_SNAPSHOT_KEY, snapshot("r1"));
    const activeSnapshotRead = vi.fn().mockResolvedValue(snapshot("r2"));
    const observer = new QueryObserver(queryClient, {
      queryKey: ACTIVE_TODAY_SNAPSHOT_KEY,
      queryFn: activeSnapshotRead,
      initialData: snapshot("r1"),
      staleTime: Number.POSITIVE_INFINITY,
    });
    unsubscribers.push(observer.subscribe(() => undefined));

    const reconciled = await fetchOperatorSnapshotForRealtime(queryClient);

    expect(activeSnapshotRead).toHaveBeenCalledOnce();
    expect(reconciled.meta.revision).toBe("r2");
    expect(queryClient.getQueryData(INACTIVE_SEVEN_DAY_SNAPSHOT_KEY)).toBeUndefined();
    expect(
      queryClient.getQueryData<OperatorSnapshot>(ACTIVE_TODAY_SNAPSHOT_KEY)?.meta.revision,
    ).toBe("r2");
  });
});
