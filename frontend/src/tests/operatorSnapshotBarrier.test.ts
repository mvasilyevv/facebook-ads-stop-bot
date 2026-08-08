import { QueryClient, QueryObserver } from "@tanstack/react-query";
import { afterEach, describe, expect, it, vi } from "vitest";

import { makeOperatorSnapshot } from "@fb/shared/operator/testFixture";

import { fetchOperatorSnapshotForRealtime } from "@/lib/api/operator";

function deferred<T>() {
  let resolve!: (value: T) => void;
  const promise = new Promise<T>((resolvePromise) => {
    resolve = resolvePromise;
  });
  return { promise, resolve };
}

describe("web operator snapshot reconciliation barrier", () => {
  const unsubscribers: Array<() => void> = [];

  afterEach(() => {
    for (const unsubscribe of unsubscribers.splice(0)) unsubscribe();
    vi.unstubAllGlobals();
  });

  it("waits for the canonical global and an active cabinet snapshot before reconnecting", async () => {
    const queryClient = new QueryClient({
      defaultOptions: { queries: { retry: false } },
    });
    const globalRead = deferred<ReturnType<typeof makeOperatorSnapshot>>();
    const cabinetRead = deferred<ReturnType<typeof makeOperatorSnapshot>>();
    const cabinetQuery = vi.fn(() => cabinetRead.promise);
    const fetchMock = vi.fn(
      async () =>
        new Response(JSON.stringify(await globalRead.promise), {
          status: 200,
          headers: { "Content-Type": "application/json" },
        }),
    );
    vi.stubGlobal("fetch", fetchMock);
    const cabinetKey = [
      "get",
      "/api/operator/cabinets/{cabinet_id}/snapshot",
      {
        params: {
          path: { cabinet_id: "123" },
          query: { window: "today" },
        },
      },
    ] as const;
    const observer = new QueryObserver(queryClient, {
      queryKey: cabinetKey,
      queryFn: cabinetQuery,
      initialData: makeOperatorSnapshot(),
      staleTime: Number.POSITIVE_INFINITY,
    });
    unsubscribers.push(observer.subscribe(() => undefined));

    let settled = false;
    const barrier = fetchOperatorSnapshotForRealtime(queryClient).then((snapshot) => {
      settled = true;
      return snapshot;
    });

    await vi.waitFor(() => expect(fetchMock).toHaveBeenCalledOnce());
    await vi.waitFor(() => expect(cabinetQuery).toHaveBeenCalledOnce());
    expect(settled).toBe(false);

    const cabinetNext = makeOperatorSnapshot();
    cabinetNext.meta.revision = "r2b";
    cabinetRead.resolve(cabinetNext);
    await Promise.resolve();
    expect(settled).toBe(false);

    const globalNext = makeOperatorSnapshot();
    globalNext.meta.revision = "r2a";
    globalRead.resolve(globalNext);

    await expect(barrier).resolves.toMatchObject({
      meta: { revision: "r2a" },
    });
    expect(settled).toBe(true);
  });
});
