import { QueryClient, QueryObserver } from "@tanstack/react-query";
import { afterEach, describe, expect, it, vi } from "vitest";

import { makeOperatorSnapshot } from "@fb/shared/operator/testFixture";
import {
  fetchOperatorActionProjectionsForRealtime,
  fetchOperatorSnapshotForRealtime,
} from "@/lib/operatorApi";

function deferred<T>() {
  let resolve!: (value: T) => void;
  const promise = new Promise<T>((resolvePromise) => {
    resolve = resolvePromise;
  });
  return { promise, resolve };
}

describe("TMA realtime projection reconciliation", () => {
  const unsubscribers: Array<() => void> = [];

  afterEach(() => {
    for (const unsubscribe of unsubscribers.splice(0)) unsubscribe();
    vi.unstubAllGlobals();
  });

  it("waits for every active global and cabinet snapshot before reconnecting", async () => {
    const queryClient = new QueryClient({
      defaultOptions: { queries: { retry: false } },
    });
    const canonicalRead = deferred<ReturnType<typeof makeOperatorSnapshot>>();
    const globalRead = deferred<ReturnType<typeof makeOperatorSnapshot>>();
    const cabinetRead = deferred<ReturnType<typeof makeOperatorSnapshot>>();
    const globalQuery = vi.fn(() => globalRead.promise);
    const cabinetQuery = vi.fn(() => cabinetRead.promise);
    const fetchMock = vi.fn(
      async () =>
        new Response(JSON.stringify(await canonicalRead.promise), {
          status: 200,
          headers: { "Content-Type": "application/json" },
        }),
    );
    vi.stubGlobal("fetch", fetchMock);
    const globalInitial = makeOperatorSnapshot();
    const cabinetInitial = makeOperatorSnapshot();

    for (const observer of [
      new QueryObserver(queryClient, {
        queryKey: [
          "get",
          "/api/operator/snapshot",
          { params: { query: { window: "today" } } },
        ],
        queryFn: globalQuery,
        initialData: globalInitial,
        staleTime: Number.POSITIVE_INFINITY,
      }),
      new QueryObserver(queryClient, {
        queryKey: [
          "get",
          "/api/operator/cabinets/{cabinet_id}/snapshot",
          {
            params: {
              path: { cabinet_id: "123" },
              query: { window: "today" },
            },
          },
        ],
        queryFn: cabinetQuery,
        initialData: cabinetInitial,
        staleTime: Number.POSITIVE_INFINITY,
      }),
    ]) {
      unsubscribers.push(observer.subscribe(() => undefined));
    }

    let settled = false;
    const barrier = fetchOperatorSnapshotForRealtime(queryClient).then(
      (snapshot) => {
        settled = true;
        return snapshot;
      },
    );
    await vi.waitFor(() => expect(fetchMock).toHaveBeenCalledOnce());
    await vi.waitFor(() => expect(globalQuery).toHaveBeenCalledOnce());
    await vi.waitFor(() => expect(cabinetQuery).toHaveBeenCalledOnce());

    const globalNext = makeOperatorSnapshot();
    globalNext.meta.revision = "r2c";
    globalRead.resolve(globalNext);
    await Promise.resolve();
    expect(settled).toBe(false);

    const cabinetNext = makeOperatorSnapshot();
    cabinetNext.meta.revision = "r2b";
    cabinetRead.resolve(cabinetNext);
    await Promise.resolve();
    expect(settled).toBe(false);

    const canonicalNext = makeOperatorSnapshot();
    canonicalNext.meta.revision = "r2d";
    canonicalRead.resolve(canonicalNext);
    const oldest = await barrier;

    expect(settled).toBe(true);
    expect(oldest.meta.revision).toBe("r2b");
  });

  it("keeps reconciliation pending until mounted analytics views refetch", async () => {
    const queryClient = new QueryClient({
      defaultOptions: { queries: { retry: false } },
    });
    const performanceRead = deferred<{ state: string }>();
    const daypartRead = deferred<{ state: string }>();
    const performanceQuery = vi.fn(() => performanceRead.promise);
    const daypartQuery = vi.fn(() => daypartRead.promise);

    for (const observer of [
      new QueryObserver(queryClient, {
        queryKey: [
          "get",
          "/api/operator/ads",
          { params: { query: { page: 1 } } },
        ],
        queryFn: vi.fn().mockResolvedValue({ rows: [] }),
        initialData: { rows: [] },
        staleTime: Number.POSITIVE_INFINITY,
      }),
      new QueryObserver(queryClient, {
        queryKey: [
          "get",
          "/api/analytics/performance",
          { params: { query: { period: "today" } } },
        ],
        queryFn: performanceQuery,
        initialData: { state: "ready" },
        staleTime: Number.POSITIVE_INFINITY,
      }),
      new QueryObserver(queryClient, {
        queryKey: [
          "get",
          "/api/analytics/daypart",
          { params: { query: { timezone: "Europe/Kaliningrad" } } },
        ],
        queryFn: daypartQuery,
        initialData: { state: "ready" },
        staleTime: Number.POSITIVE_INFINITY,
      }),
    ]) {
      unsubscribers.push(observer.subscribe(() => undefined));
    }

    let settled = false;
    const barrier = fetchOperatorActionProjectionsForRealtime(queryClient).then(
      () => {
        settled = true;
      },
    );
    await vi.waitFor(() => expect(performanceQuery).toHaveBeenCalledOnce());
    await vi.waitFor(() => expect(daypartQuery).toHaveBeenCalledOnce());
    expect(settled).toBe(false);

    performanceRead.resolve({ state: "ready" });
    await Promise.resolve();
    expect(settled).toBe(false);
    daypartRead.resolve({ state: "ready" });
    await barrier;
    expect(settled).toBe(true);
  });
});
