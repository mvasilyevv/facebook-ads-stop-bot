import type { PropsWithChildren } from "react";
import { act, render, waitFor } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { makeOperatorSnapshot } from "@fb/shared/operator/testFixture";

const { useOperatorRealtime } = vi.hoisted(() => ({
  useOperatorRealtime: vi.fn((_options?: unknown) => "connected" as const),
}));

vi.mock("@tanstack/react-router", () => ({
  createRootRoute: (options: unknown) => options,
  Outlet: () => null,
  useNavigate: () => vi.fn(),
}));
vi.mock("@fb/operator-api", async (importOriginal) => ({
  ...(await importOriginal<typeof import("@fb/operator-api")>()),
  useOperatorRealtime,
  useOperatorRealtimeStatus: () => "connected",
  OperatorRealtimeStatusProvider: ({ children }: PropsWithChildren) => children,
}));
vi.mock("@/components/layout/AuthGuard", () => ({
  AuthGuard: ({ children }: PropsWithChildren) => children,
}));
vi.mock("@/components/layout/TabBar", () => ({ TabBar: () => null }));
vi.mock("@/components/layout/TelegramBackButton", () => ({
  TelegramBackButton: () => null,
}));
vi.mock("@/lib/operatorApi", async (importOriginal) => ({
  ...(await importOriginal<typeof import("@/lib/operatorApi")>()),
  useResolveTmaNavigation: () => ({ mutateAsync: vi.fn() }),
}));
vi.mock("@/lib/tg", () => ({
  getInitData: () => "signed_init_data",
  getTgStartParam: () => null,
  tgAlert: vi.fn(),
}));

import { tmaOperatorFetch } from "@/lib/operatorApi";
import { loginToBackend, logout } from "@/lib/auth";
import { OperatorRealtimeBridge } from "@/routes/__root";

describe("TMA realtime auth rotation", () => {
  beforeEach(() => {
    vi.clearAllMocks();
    logout();
    localStorage.clear();
    sessionStorage.clear();
  });

  it("recreates the websocket protocols after an HTTP 401 rotates the token", async () => {
    global.fetch = vi.fn().mockResolvedValueOnce(
      new Response(JSON.stringify({ token: "expired_token", role: "owner" }), {
        status: 200,
        headers: { "Content-Type": "application/json" },
      }),
    );
    await loginToBackend();
    render(
      <OperatorRealtimeBridge>
        <span>content</span>
      </OperatorRealtimeBridge>,
    );

    expect(useOperatorRealtime).toHaveBeenLastCalledWith(
      expect.objectContaining({
        protocols: ["fb-operator-v1", "tma.expired_token"],
      }),
    );

    global.fetch = vi
      .fn()
      .mockResolvedValueOnce(new Response(null, { status: 401 }))
      .mockResolvedValueOnce(
        new Response(JSON.stringify({ token: "rotated_token", role: "owner" }), {
          status: 200,
          headers: { "Content-Type": "application/json" },
        }),
      )
      .mockResolvedValueOnce(
        new Response(JSON.stringify(makeOperatorSnapshot()), {
          status: 200,
          headers: { "Content-Type": "application/json" },
        }),
      );
    await act(async () => {
      const response = await tmaOperatorFetch("https://operator.test/api/operator/snapshot");
      expect(response.status).toBe(200);
    });

    await waitFor(() =>
      expect(useOperatorRealtime).toHaveBeenLastCalledWith(
        expect.objectContaining({
          protocols: ["fb-operator-v1", "tma.rotated_token"],
        }),
      ),
    );
  });

  it("rotates an expired websocket session without waiting for an HTTP 401", async () => {
    global.fetch = vi.fn().mockResolvedValueOnce(
      new Response(JSON.stringify({ token: "expired_token", role: "owner" }), {
        status: 200,
        headers: { "Content-Type": "application/json" },
      }),
    );
    await loginToBackend();
    render(
      <OperatorRealtimeBridge>
        <span>content</span>
      </OperatorRealtimeBridge>,
    );

    global.fetch = vi.fn().mockResolvedValueOnce(
      new Response(JSON.stringify({ token: "rotated_token", role: "owner" }), {
        status: 200,
        headers: { "Content-Type": "application/json" },
      }),
    );
    const options = useOperatorRealtime.mock.calls.at(-1)?.[0] as
      | { onAuthFailure?: () => Promise<void> }
      | undefined;
    await act(async () => {
      await options?.onAuthFailure?.();
    });

    await waitFor(() =>
      expect(useOperatorRealtime).toHaveBeenLastCalledWith(
        expect.objectContaining({
          enabled: true,
          protocols: ["fb-operator-v1", "tma.rotated_token"],
        }),
      ),
    );
    expect(global.fetch).toHaveBeenCalledTimes(1);
    const [request] = (global.fetch as ReturnType<typeof vi.fn>).mock.calls[0] as [Request];
    expect(request.url).toContain("/api/tma/auth");
    expect(request.method).toBe("POST");
  });
});
