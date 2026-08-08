import { QueryClient, QueryObserver } from "@tanstack/react-query";
import { afterEach, describe, expect, it, vi } from "vitest";

import { fetchOperatorActionProjectionsForRealtime } from "@/lib/operatorApi";

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
