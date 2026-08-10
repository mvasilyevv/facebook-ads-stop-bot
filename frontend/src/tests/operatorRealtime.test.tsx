import { act, render, screen, waitFor } from "@testing-library/react";
import {
  QueryClient,
  QueryClientProvider,
  useQuery,
  type QueryFunction,
} from "@tanstack/react-query";
import type { ReactNode } from "react";

import {
  useOperatorRealtime,
  type OperatorActionProjectionFetcher,
  type OperatorAuthFailureHandler,
  type OperatorSnapshotFetcher,
} from "@fb/operator-api";
import type { OperatorSnapshot } from "@fb/shared/operator/contracts";
import { makeOperatorSnapshot } from "@fb/shared/operator/testFixture";

const SNAPSHOT_KEY = ["get", "/api/operator/snapshot", { params: { query: {} } }] as const;
const CACHED_ACTIVE_ADS_KEY = [
  "get",
  "/api/operator/ads",
  { params: { query: { delivery_status: "ACTIVE", page: 2 } } },
] as const;

const completedActionProjectionFetch: OperatorActionProjectionFetcher = async () => {};

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

function queryBackedFetcher(
  queryFn: QueryFunction<OperatorSnapshot, typeof SNAPSHOT_KEY>,
): OperatorSnapshotFetcher {
  return (queryClient) =>
    queryClient.fetchQuery({
      queryKey: SNAPSHOT_KEY,
      queryFn,
      retry: false,
      staleTime: 0,
    });
}

class TestWebSocket {
  static instances: TestWebSocket[] = [];
  readonly listeners = new Map<string, Array<(event: Event) => void>>();
  readonly url: string;
  readonly protocols?: string | string[];

  constructor(url: string, protocols?: string | string[]) {
    this.url = url;
    this.protocols = protocols;
    TestWebSocket.instances.push(this);
  }

  addEventListener(type: string, listener: (event: Event) => void) {
    this.listeners.set(type, [...(this.listeners.get(type) ?? []), listener]);
  }

  emit(type: string, payload: unknown = undefined) {
    const event: Event =
      type === "message"
        ? new MessageEvent("message", { data: JSON.stringify(payload) })
        : type === "close"
          ? new CloseEvent("close", {
              code: typeof payload === "number" ? payload : 1000,
            })
          : new Event(type);
    for (const listener of this.listeners.get(type) ?? []) listener(event);
  }

  close(_code?: number, _reason?: string) {}
}

function Harness({ children, client }: { children: ReactNode; client: QueryClient }) {
  return <QueryClientProvider client={client}>{children}</QueryClientProvider>;
}

function Realtime({
  fetchActionProjections = completedActionProjectionFetch,
  fetchSnapshot,
  onAuthFailure,
  onStatus,
  protocols,
}: {
  fetchActionProjections?: OperatorActionProjectionFetcher;
  fetchSnapshot?: OperatorSnapshotFetcher;
  onAuthFailure?: OperatorAuthFailureHandler;
  onStatus?: (status: ReturnType<typeof useOperatorRealtime>) => void;
  protocols?: string[];
}) {
  const status = useOperatorRealtime({
    fetchActionProjections,
    fetchSnapshot,
    onAuthFailure,
    protocols,
  });
  onStatus?.(status);
  return <output data-testid="realtime-status">{status}</output>;
}

function ActiveSnapshotQuery({
  queryFn,
}: {
  queryFn: QueryFunction<OperatorSnapshot, typeof SNAPSHOT_KEY>;
}) {
  useQuery({
    queryKey: SNAPSHOT_KEY,
    queryFn,
    initialData: snapshot("r0"),
    staleTime: Number.POSITIVE_INFINITY,
  });
  return null;
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

describe("operator realtime reconciliation", () => {
  beforeEach(() => {
    TestWebSocket.instances = [];
    vi.stubGlobal("WebSocket", TestWebSocket);
  });

  afterEach(() => {
    vi.useRealTimers();
    vi.unstubAllGlobals();
  });

  it("reconciles once on snapshot requirement and sequence gaps", async () => {
    const client = new QueryClient();
    const fetchSnapshot = vi
      .fn<OperatorSnapshotFetcher>()
      .mockResolvedValueOnce(snapshot("r10"))
      .mockResolvedValueOnce(snapshot("r12"));
    const fetchActionProjections = vi
      .fn<OperatorActionProjectionFetcher>()
      .mockResolvedValue(undefined);
    render(
      <Realtime fetchActionProjections={fetchActionProjections} fetchSnapshot={fetchSnapshot} />,
      {
        wrapper: ({ children }) => <Harness client={client}>{children}</Harness>,
      },
    );
    const socket = TestWebSocket.instances[0]!;

    act(() =>
      socket.emit("message", {
        type: "snapshot_required",
        sequence: 1,
        snapshot_revision: "r10",
        scopes: ["snapshot"],
        ts: "2026-07-19T00:00:00Z",
      }),
    );
    await waitFor(() => expect(fetchSnapshot).toHaveBeenCalledTimes(1));
    await waitFor(() => expect(fetchActionProjections).toHaveBeenCalledTimes(1));

    act(() =>
      socket.emit("message", {
        type: "changed",
        sequence: 3,
        snapshot_revision: "r12",
        scopes: ["metrics"],
        ts: "2026-07-19T00:00:01Z",
      }),
    );
    await waitFor(() => expect(fetchSnapshot).toHaveBeenCalledTimes(2));
    await waitFor(() => expect(fetchActionProjections).toHaveBeenCalledTimes(2));
  });

  it("does not become connected until snapshot reconciliation completes", async () => {
    const client = new QueryClient();
    let finishReconciliation: ((value: OperatorSnapshot) => void) | undefined;
    const reconciliation = new Promise<OperatorSnapshot>((resolve) => {
      finishReconciliation = resolve;
    });
    const fetchSnapshot = vi.fn<OperatorSnapshotFetcher>().mockReturnValue(reconciliation);
    const view = render(<Realtime fetchSnapshot={fetchSnapshot} />, {
      wrapper: ({ children }) => <Harness client={client}>{children}</Harness>,
    });
    const socket = TestWebSocket.instances[0]!;

    act(() => socket.emit("open"));
    expect(screen.getByTestId("realtime-status")).toHaveTextContent("connecting");

    act(() =>
      socket.emit("message", {
        type: "snapshot_required",
        sequence: 1,
        snapshot_revision: "r10",
        scopes: ["snapshot"],
        ts: "2026-07-19T00:00:00Z",
      }),
    );
    expect(screen.getByTestId("realtime-status")).toHaveTextContent("connecting");

    finishReconciliation?.(snapshot("r10"));
    await waitFor(() =>
      expect(screen.getByTestId("realtime-status")).toHaveTextContent("connected"),
    );

    act(() => socket.emit("close"));
    expect(screen.getByTestId("realtime-status")).toHaveTextContent("reconnecting");
    view.unmount();
  });

  it("fails closed during the render which rotates the connection identity", async () => {
    const client = new QueryClient();
    const fetchSnapshot = vi.fn<OperatorSnapshotFetcher>().mockResolvedValue(snapshot("r1"));
    const observedStatuses = vi.fn<(status: string) => void>();
    const view = render(
      <Realtime
        fetchSnapshot={fetchSnapshot}
        onStatus={observedStatuses}
        protocols={["fb-operator-v1", "tma.old-token"]}
      />,
      { wrapper: ({ children }) => <Harness client={client}>{children}</Harness> },
    );
    const socket = TestWebSocket.instances[0]!;
    act(() =>
      socket.emit("message", {
        type: "snapshot_required",
        sequence: 1,
        snapshot_revision: "r1",
        scopes: ["snapshot"],
        ts: "2026-07-19T00:00:00Z",
      }),
    );
    await waitFor(() =>
      expect(screen.getByTestId("realtime-status")).toHaveTextContent("connected"),
    );

    observedStatuses.mockClear();
    view.rerender(
      <Realtime
        fetchSnapshot={fetchSnapshot}
        onStatus={observedStatuses}
        protocols={["fb-operator-v1", "tma.rotated-token"]}
      />,
    );

    expect(observedStatuses.mock.calls[0]?.[0]).toBe("reconnecting");
    expect(observedStatuses).not.toHaveBeenCalledWith("connected");
  });

  it("refreshes a policy-expired credential before reconnecting", async () => {
    vi.useFakeTimers();
    const client = new QueryClient();
    const refresh = deferred<void>();
    const onAuthFailure = vi.fn<OperatorAuthFailureHandler>().mockReturnValue(refresh.promise);
    render(
      <Realtime
        fetchSnapshot={vi.fn().mockResolvedValue(snapshot("r1"))}
        onAuthFailure={onAuthFailure}
        protocols={["fb-operator-v1", "tma.expired-token"]}
      />,
      { wrapper: ({ children }) => <Harness client={client}>{children}</Harness> },
    );
    const socket = TestWebSocket.instances[0]!;

    await act(async () => {
      socket.emit("close", 1008);
      await Promise.resolve();
    });
    expect(onAuthFailure).toHaveBeenCalledOnce();
    expect(screen.getByTestId("realtime-status")).toHaveTextContent("reconnecting");

    act(() => vi.advanceTimersByTime(30_000));
    expect(TestWebSocket.instances).toHaveLength(1);

    await act(async () => {
      refresh.resolve();
      await refresh.promise;
    });
    act(() => vi.advanceTimersByTime(1_000));
    expect(TestWebSocket.instances).toHaveLength(2);
  });

  it("keeps a cached active-ad action fail-closed until its projection barrier succeeds", async () => {
    const client = new QueryClient();
    client.setQueryData(CACHED_ACTIVE_ADS_KEY, {
      items: [{ delivery_status: "ACTIVE", data_state: "ready" }],
    });
    const projection = deferred<void>();
    const fetchActionProjections = vi.fn<OperatorActionProjectionFetcher>(async (queryClient) => {
      expect(queryClient.getQueryData(CACHED_ACTIVE_ADS_KEY)).toEqual({
        items: [{ delivery_status: "ACTIVE", data_state: "ready" }],
      });
      await projection.promise;
    });
    render(
      <Realtime
        fetchActionProjections={fetchActionProjections}
        fetchSnapshot={vi.fn().mockResolvedValue(snapshot("r10"))}
      />,
      { wrapper: ({ children }) => <Harness client={client}>{children}</Harness> },
    );
    const socket = TestWebSocket.instances[0]!;

    act(() =>
      socket.emit("message", {
        type: "snapshot_required",
        sequence: 1,
        snapshot_revision: "r10",
        scopes: ["snapshot"],
        ts: "2026-07-19T00:00:00Z",
      }),
    );
    await waitFor(() => expect(fetchActionProjections).toHaveBeenCalledOnce());
    expect(screen.getByTestId("realtime-status")).toHaveTextContent("connecting");

    await act(async () => {
      projection.resolve();
      await projection.promise;
    });
    await waitFor(() =>
      expect(screen.getByTestId("realtime-status")).toHaveTextContent("connected"),
    );
  });

  it("never unlocks a cached active-ad action when projection refresh rejects", async () => {
    const client = new QueryClient();
    client.setQueryData(CACHED_ACTIVE_ADS_KEY, {
      items: [{ delivery_status: "ACTIVE", data_state: "ready" }],
    });
    const fetchActionProjections = vi
      .fn<OperatorActionProjectionFetcher>()
      .mockRejectedValue(new Error("ads projection unavailable"));
    render(
      <Realtime
        fetchActionProjections={fetchActionProjections}
        fetchSnapshot={vi.fn().mockResolvedValue(snapshot("r10"))}
      />,
      { wrapper: ({ children }) => <Harness client={client}>{children}</Harness> },
    );
    const socket = TestWebSocket.instances[0]!;

    act(() =>
      socket.emit("message", {
        type: "snapshot_required",
        sequence: 1,
        snapshot_revision: "r10",
        scopes: ["snapshot"],
        ts: "2026-07-19T00:00:00Z",
      }),
    );
    await waitFor(() => expect(fetchActionProjections).toHaveBeenCalledOnce());
    expect(screen.getByTestId("realtime-status")).toHaveTextContent("connecting");
  });

  it("uses background invalidation only for the explicit non-action allowlist", async () => {
    const client = new QueryClient();
    const invalidate = vi.spyOn(client, "invalidateQueries").mockResolvedValue(undefined);
    render(<Realtime fetchSnapshot={vi.fn().mockResolvedValue(snapshot("rf"))} />, {
      wrapper: ({ children }) => <Harness client={client}>{children}</Harness>,
    });
    const socket = TestWebSocket.instances[0]!;

    act(() =>
      socket.emit("message", {
        type: "snapshot_required",
        sequence: 1,
        snapshot_revision: "rf",
        scopes: ["snapshot"],
        ts: "2026-07-19T00:00:00Z",
      }),
    );
    await waitFor(() =>
      expect(screen.getByTestId("realtime-status")).toHaveTextContent("connected"),
    );
    invalidate.mockClear();

    act(() =>
      socket.emit("message", {
        type: "changed",
        sequence: 2,
        snapshot_revision: "r10",
        scopes: ["notification_delivery"],
        ts: "2026-07-19T00:00:01Z",
      }),
    );
    expect(invalidate).toHaveBeenCalledTimes(2);
    expect(invalidate).toHaveBeenCalledWith({ queryKey: ["get", "/api/operator/snapshot"] });
    expect(invalidate).toHaveBeenCalledWith({
      queryKey: ["get", "/api/operator/cabinets/{cabinet_id}/snapshot"],
    });

    invalidate.mockClear();
    act(() =>
      socket.emit("message", {
        type: "changed",
        sequence: 3,
        snapshot_revision: "r11",
        scopes: ["observer_config"],
        ts: "2026-07-19T00:00:02Z",
      }),
    );
    expect(invalidate).toHaveBeenCalledTimes(2);
    expect(invalidate).toHaveBeenCalledWith({ queryKey: ["get", "/api/operator/snapshot"] });
    expect(invalidate).toHaveBeenCalledWith({
      queryKey: ["get", "/api/operator/cabinets/{cabinet_id}/snapshot"],
    });
  });

  it("invalidates only the changed campaign run and run lists without polling", async () => {
    const client = new QueryClient();
    const fetchSnapshot = vi.fn<OperatorSnapshotFetcher>().mockResolvedValue(snapshot("r10"));
    const fetchActionProjections = vi
      .fn<OperatorActionProjectionFetcher>()
      .mockResolvedValue(undefined);
    render(
      <Realtime fetchActionProjections={fetchActionProjections} fetchSnapshot={fetchSnapshot} />,
      { wrapper: ({ children }) => <Harness client={client}>{children}</Harness> },
    );
    const socket = TestWebSocket.instances[0]!;

    act(() =>
      socket.emit("message", {
        type: "snapshot_required",
        sequence: 1,
        snapshot_revision: "r10",
        scopes: ["snapshot"],
        ts: "2026-07-19T00:00:00Z",
      }),
    );
    await waitFor(() =>
      expect(screen.getByTestId("realtime-status")).toHaveTextContent("connected"),
    );
    fetchSnapshot.mockClear();
    fetchActionProjections.mockClear();

    const changedRunId = "9af0a7ac-1e0d-4bdd-9f5a-ec2cba8ec156";
    const otherRunId = "44372ed4-2bad-4771-a2cf-6a53f62c94b6";
    const listKey = [
      "get",
      "/api/tools/campaigns/runs",
      { params: { query: { limit: 50, offset: 0 } } },
    ] as const;
    const changedDetailKey = [
      "get",
      "/api/tools/campaigns/runs/{run_id}",
      { params: { path: { run_id: changedRunId } } },
    ] as const;
    const otherDetailKey = [
      "get",
      "/api/tools/campaigns/runs/{run_id}",
      { params: { path: { run_id: otherRunId } } },
    ] as const;
    client.setQueryData(listKey, []);
    client.setQueryData(changedDetailKey, { id: changedRunId });
    client.setQueryData(otherDetailKey, { id: otherRunId });

    act(() =>
      socket.emit("message", {
        type: "ping",
        sequence: 2,
        snapshot_revision: "r10",
        scopes: [],
        ts: "2026-07-19T00:00:01Z",
      }),
    );
    await waitFor(() => expect(fetchSnapshot).toHaveBeenCalledOnce());
    await waitFor(() => expect(fetchActionProjections).toHaveBeenCalledOnce());
    expect(client.getQueryState(listKey)?.isInvalidated).toBe(false);
    expect(client.getQueryState(changedDetailKey)?.isInvalidated).toBe(false);
    expect(client.getQueryState(otherDetailKey)?.isInvalidated).toBe(false);
    fetchSnapshot.mockClear();
    fetchActionProjections.mockClear();

    act(() =>
      socket.emit("message", {
        type: "changed",
        sequence: 3,
        snapshot_revision: "r11",
        scopes: [`campaign_run:${changedRunId}`],
        ts: "2026-07-19T00:00:02Z",
      }),
    );

    await waitFor(() => expect(client.getQueryState(listKey)?.isInvalidated).toBe(true));
    expect(client.getQueryState(changedDetailKey)?.isInvalidated).toBe(true);
    expect(client.getQueryState(otherDetailKey)?.isInvalidated).toBe(false);
    expect(fetchSnapshot).not.toHaveBeenCalled();
    expect(fetchActionProjections).not.toHaveBeenCalled();
    expect(screen.getByTestId("realtime-status")).toHaveTextContent("connected");

    client.setQueryData(listKey, []);
    client.setQueryData(changedDetailKey, { id: changedRunId });
    client.setQueryData(otherDetailKey, { id: otherRunId });
    fetchSnapshot.mockResolvedValue(snapshot("r12"));

    act(() =>
      socket.emit("message", {
        type: "changed",
        sequence: 4,
        snapshot_revision: "r12",
        scopes: [`campaign_run:${changedRunId}`, "task"],
        ts: "2026-07-19T00:00:03Z",
      }),
    );

    await waitFor(() => expect(client.getQueryState(listKey)?.isInvalidated).toBe(true));
    expect(client.getQueryState(changedDetailKey)?.isInvalidated).toBe(true);
    expect(client.getQueryState(otherDetailKey)?.isInvalidated).toBe(false);
    await waitFor(() => expect(fetchSnapshot).toHaveBeenCalledOnce());
    await waitFor(() => expect(fetchActionProjections).toHaveBeenCalledOnce());
  });

  it("coalesces contiguous action events and never unlocks between barriers", async () => {
    const client = new QueryClient();
    const projection = deferred<void>();
    const fetchSnapshot = vi
      .fn<OperatorSnapshotFetcher>()
      .mockResolvedValueOnce(snapshot("r1"))
      .mockResolvedValueOnce(snapshot("r2"))
      .mockResolvedValueOnce(snapshot("r3"));
    const fetchActionProjections = vi
      .fn<OperatorActionProjectionFetcher>()
      .mockResolvedValueOnce(undefined)
      .mockReturnValueOnce(projection.promise)
      .mockResolvedValueOnce(undefined);
    render(
      <Realtime fetchActionProjections={fetchActionProjections} fetchSnapshot={fetchSnapshot} />,
      { wrapper: ({ children }) => <Harness client={client}>{children}</Harness> },
    );
    const socket = TestWebSocket.instances[0]!;

    act(() =>
      socket.emit("message", {
        type: "snapshot_required",
        sequence: 1,
        snapshot_revision: "r1",
        scopes: ["snapshot"],
        ts: "2026-07-19T00:00:00Z",
      }),
    );
    await waitFor(() =>
      expect(screen.getByTestId("realtime-status")).toHaveTextContent("connected"),
    );

    act(() =>
      socket.emit("message", {
        type: "changed",
        sequence: 2,
        snapshot_revision: "r2",
        scopes: ["task"],
        ts: "2026-07-19T00:00:01Z",
      }),
    );
    await waitFor(() => expect(fetchActionProjections).toHaveBeenCalledTimes(2));
    expect(screen.getByTestId("realtime-status")).toHaveTextContent("reconnecting");

    act(() =>
      socket.emit("message", {
        type: "changed",
        sequence: 3,
        snapshot_revision: "r3",
        scopes: ["metrics"],
        ts: "2026-07-19T00:00:02Z",
      }),
    );
    expect(fetchSnapshot).toHaveBeenCalledTimes(2);
    expect(fetchActionProjections).toHaveBeenCalledTimes(2);
    expect(screen.getByTestId("realtime-status")).toHaveTextContent("reconnecting");

    await act(async () => {
      projection.resolve();
      await projection.promise;
    });
    await waitFor(() => expect(fetchSnapshot).toHaveBeenCalledTimes(3));
    await waitFor(() => expect(fetchActionProjections).toHaveBeenCalledTimes(3));
    await waitFor(() =>
      expect(screen.getByTestId("realtime-status")).toHaveTextContent("connected"),
    );
  });

  it("fails closed for an unknown contiguous scope and rejected projection", async () => {
    const client = new QueryClient();
    const fetchSnapshot = vi
      .fn<OperatorSnapshotFetcher>()
      .mockResolvedValueOnce(snapshot("r1"))
      .mockResolvedValueOnce(snapshot("r2"));
    const fetchActionProjections = vi
      .fn<OperatorActionProjectionFetcher>()
      .mockResolvedValueOnce(undefined)
      .mockRejectedValueOnce(new Error("ads unavailable"));
    render(
      <Realtime fetchActionProjections={fetchActionProjections} fetchSnapshot={fetchSnapshot} />,
      { wrapper: ({ children }) => <Harness client={client}>{children}</Harness> },
    );
    const socket = TestWebSocket.instances[0]!;

    act(() =>
      socket.emit("message", {
        type: "snapshot_required",
        sequence: 1,
        snapshot_revision: "r1",
        scopes: ["snapshot"],
        ts: "2026-07-19T00:00:00Z",
      }),
    );
    await waitFor(() =>
      expect(screen.getByTestId("realtime-status")).toHaveTextContent("connected"),
    );

    act(() =>
      socket.emit("message", {
        type: "changed",
        sequence: 2,
        snapshot_revision: "r2",
        scopes: ["future_unknown_scope"],
        ts: "2026-07-19T00:00:01Z",
      }),
    );
    await waitFor(() => expect(fetchActionProjections).toHaveBeenCalledTimes(2));
    expect(screen.getByTestId("realtime-status")).toHaveTextContent("reconnecting");
  });

  it("rechecks time-based freshness on every heartbeat before staying connected", async () => {
    const client = new QueryClient();
    const heartbeatProjection = deferred<void>();
    const fetchSnapshot = vi.fn<OperatorSnapshotFetcher>().mockResolvedValue(snapshot("r1"));
    const fetchActionProjections = vi
      .fn<OperatorActionProjectionFetcher>()
      .mockResolvedValueOnce(undefined)
      .mockReturnValueOnce(heartbeatProjection.promise);
    render(
      <Realtime fetchActionProjections={fetchActionProjections} fetchSnapshot={fetchSnapshot} />,
      { wrapper: ({ children }) => <Harness client={client}>{children}</Harness> },
    );
    const socket = TestWebSocket.instances[0]!;

    act(() =>
      socket.emit("message", {
        type: "snapshot_required",
        sequence: 1,
        snapshot_revision: "r1",
        scopes: ["snapshot"],
        ts: "2026-07-19T00:00:00Z",
      }),
    );
    await waitFor(() =>
      expect(screen.getByTestId("realtime-status")).toHaveTextContent("connected"),
    );

    act(() =>
      socket.emit("message", {
        type: "ping",
        sequence: 2,
        snapshot_revision: "r1",
        scopes: [],
        ts: "2026-07-19T00:00:30Z",
      }),
    );
    await waitFor(() => expect(fetchActionProjections).toHaveBeenCalledTimes(2));
    expect(screen.getByTestId("realtime-status")).toHaveTextContent("reconnecting");

    await act(async () => {
      heartbeatProjection.resolve();
      await heartbeatProjection.promise;
    });
    await waitFor(() =>
      expect(screen.getByTestId("realtime-status")).toHaveTextContent("connected"),
    );
  });

  it.each([
    ["active", true],
    ["inactive", false],
  ] as const)("stays fail-closed when a real %s snapshot query rejects", async (_label, active) => {
    const client = new QueryClient({
      defaultOptions: { queries: { retry: false } },
    });
    client.setQueryData(SNAPSHOT_KEY, snapshot("r0"));
    const queryFn = vi
      .fn<QueryFunction<OperatorSnapshot, typeof SNAPSHOT_KEY>>()
      .mockResolvedValueOnce(snapshot("r1"))
      .mockRejectedValueOnce(new Error("snapshot unavailable"));
    const fetchSnapshot = queryBackedFetcher(queryFn);

    render(
      <>
        {active ? <ActiveSnapshotQuery queryFn={queryFn} /> : null}
        <Realtime fetchSnapshot={fetchSnapshot} />
      </>,
      {
        wrapper: ({ children }) => <Harness client={client}>{children}</Harness>,
      },
    );
    const socket = TestWebSocket.instances[0]!;

    act(() =>
      socket.emit("message", {
        type: "snapshot_required",
        sequence: 1,
        snapshot_revision: "r1",
        scopes: ["snapshot"],
        ts: "2026-07-19T00:00:00Z",
      }),
    );
    await waitFor(() =>
      expect(screen.getByTestId("realtime-status")).toHaveTextContent("connected"),
    );

    act(() =>
      socket.emit("message", {
        type: "ping",
        sequence: 2,
        snapshot_revision: "r2",
        scopes: [],
        ts: "2026-07-19T00:00:01Z",
      }),
    );
    await waitFor(() => expect(queryFn).toHaveBeenCalledTimes(2));
    await waitFor(() => expect(client.getQueryState(SNAPSHOT_KEY)?.fetchStatus).toBe("idle"));
    expect(screen.getByTestId("realtime-status")).toHaveTextContent("reconnecting");
  });

  it("rejects a fetched snapshot older than the WS revision", async () => {
    const client = new QueryClient();
    const fetchSnapshot = vi
      .fn<OperatorSnapshotFetcher>()
      .mockResolvedValueOnce(snapshot("r9"))
      .mockResolvedValueOnce(snapshot("ra"));
    render(<Realtime fetchSnapshot={fetchSnapshot} />, {
      wrapper: ({ children }) => <Harness client={client}>{children}</Harness>,
    });
    const socket = TestWebSocket.instances[0]!;

    act(() =>
      socket.emit("message", {
        type: "snapshot_required",
        sequence: 1,
        snapshot_revision: "ra",
        scopes: ["snapshot"],
        ts: "2026-07-19T00:00:00Z",
      }),
    );
    await waitFor(() => expect(fetchSnapshot).toHaveBeenCalledTimes(1));
    expect(screen.getByTestId("realtime-status")).toHaveTextContent("connecting");

    act(() =>
      socket.emit("message", {
        type: "ping",
        sequence: 2,
        snapshot_revision: "ra",
        scopes: [],
        ts: "2026-07-19T00:00:01Z",
      }),
    );
    await waitFor(() =>
      expect(screen.getByTestId("realtime-status")).toHaveTextContent("connected"),
    );
  });

  it("coalesces a newer revision without letting the first promise unlock", async () => {
    const client = new QueryClient();
    const first = deferred<OperatorSnapshot>();
    const second = deferred<OperatorSnapshot>();
    const fetchSnapshot = vi
      .fn<OperatorSnapshotFetcher>()
      .mockReturnValueOnce(first.promise)
      .mockReturnValueOnce(second.promise);
    render(<Realtime fetchSnapshot={fetchSnapshot} />, {
      wrapper: ({ children }) => <Harness client={client}>{children}</Harness>,
    });
    const socket = TestWebSocket.instances[0]!;

    await act(async () => {
      socket.emit("message", {
        type: "snapshot_required",
        sequence: 1,
        snapshot_revision: "r1",
        scopes: ["snapshot"],
        ts: "2026-07-19T00:00:00Z",
      });
      await Promise.resolve();
    });
    await act(async () => {
      socket.emit("message", {
        type: "changed",
        sequence: 3,
        snapshot_revision: "r2",
        scopes: ["task"],
        ts: "2026-07-19T00:00:01Z",
      });
      await Promise.resolve();
    });
    expect(fetchSnapshot).toHaveBeenCalledTimes(1);

    await act(async () => {
      first.resolve(snapshot("r2"));
      await first.promise;
    });
    expect(screen.getByTestId("realtime-status")).toHaveTextContent("connecting");
    await waitFor(() => expect(fetchSnapshot).toHaveBeenCalledTimes(2));

    await act(async () => {
      second.resolve(snapshot("r2"));
      await second.promise;
    });
    expect(screen.getByTestId("realtime-status")).toHaveTextContent("connected");
  });

  it("ignores reconciliation completed by a stale socket", async () => {
    vi.useFakeTimers();
    const client = new QueryClient();
    const first = deferred<OperatorSnapshot>();
    const second = deferred<OperatorSnapshot>();
    const fetchSnapshot = vi
      .fn<OperatorSnapshotFetcher>()
      .mockReturnValueOnce(first.promise)
      .mockReturnValueOnce(second.promise);
    render(<Realtime fetchSnapshot={fetchSnapshot} />, {
      wrapper: ({ children }) => <Harness client={client}>{children}</Harness>,
    });
    const firstSocket = TestWebSocket.instances[0]!;

    await act(async () => {
      firstSocket.emit("message", {
        type: "snapshot_required",
        sequence: 1,
        snapshot_revision: "r1",
        scopes: ["snapshot"],
        ts: "2026-07-19T00:00:00Z",
      });
      await Promise.resolve();
    });
    act(() => firstSocket.emit("close"));
    act(() => vi.advanceTimersByTime(1_000));
    const secondSocket = TestWebSocket.instances[1]!;

    await act(async () => {
      secondSocket.emit("message", {
        type: "snapshot_required",
        sequence: 1,
        snapshot_revision: "r2",
        scopes: ["snapshot"],
        ts: "2026-07-19T00:00:01Z",
      });
      await Promise.resolve();
    });
    await act(async () => {
      first.resolve(snapshot("r1"));
      await first.promise;
    });
    expect(screen.getByTestId("realtime-status")).toHaveTextContent("reconnecting");

    await act(async () => {
      second.resolve(snapshot("r2"));
      await second.promise;
    });
    expect(screen.getByTestId("realtime-status")).toHaveTextContent("connected");
  });

  it("sends the TMA session through WebSocket subprotocols, never the URL", () => {
    const client = new QueryClient();
    render(<Realtime protocols={["fb-operator-v1", "tma.signed-session"]} />, {
      wrapper: ({ children }) => <Harness client={client}>{children}</Harness>,
    });
    const socket = TestWebSocket.instances[0]!;

    expect(socket.url).toBe("ws://localhost:3000/ws/operator");
    expect(socket.url).not.toContain("signed-session");
    expect(socket.protocols).toEqual(["fb-operator-v1", "tma.signed-session"]);
  });
});
