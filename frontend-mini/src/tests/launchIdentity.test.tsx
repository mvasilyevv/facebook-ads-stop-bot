import {
  StrictMode,
  useEffect,
  useState,
  type PropsWithChildren,
} from "react";
import { render, screen, waitFor } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { makeOperatorSnapshot } from "@fb/shared/operator/testFixture";

const { currentInitData, useOperatorRealtime } = vi.hoisted(() => ({
  currentInitData: { value: "new_recipient_init_data" },
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
vi.mock("@/components/layout/TabBar", () => ({ TabBar: () => null }));
vi.mock("@/components/layout/TelegramBackButton", () => ({
  TelegramBackButton: () => null,
}));
vi.mock("@/lib/operatorApi", async (importOriginal) => ({
  ...(await importOriginal<typeof import("@/lib/operatorApi")>()),
  useResolveTmaNavigation: () => ({ mutateAsync: vi.fn() }),
}));
vi.mock("@/lib/tg", () => ({
  getInitData: () => currentInitData.value,
  getTgStartParam: () => null,
  initTheme: () => vi.fn(),
  tgAlert: vi.fn(),
}));

import { AuthGuard } from "@/components/layout/AuthGuard";
import {
  getStoredRole,
  getStoredToken,
  loginToBackend,
  logout,
} from "@/lib/auth";
import { tmaOperatorFetch } from "@/lib/operatorApi";
import { OperatorRealtimeBridge } from "@/routes/__root";

interface ObservedRequest {
  authorization: string | null;
  path: string;
}

function ProtectedDataProbe() {
  const [loaded, setLoaded] = useState(false);
  useEffect(() => {
    void tmaOperatorFetch("https://operator.test/api/operator/snapshot").then(
      (response) => {
        if (response.ok) setLoaded(true);
      },
    );
  }, []);
  return <span>{loaded ? "protected-loaded" : "protected-loading"}</span>;
}

describe("TMA launch identity", () => {
  beforeEach(() => {
    vi.clearAllMocks();
    logout();
    localStorage.clear();
    sessionStorage.clear();
  });

  it("reauthenticates current initData before rendering data or opening realtime", async () => {
    currentInitData.value = "old_owner_init_data";
    globalThis.fetch = vi.fn().mockResolvedValueOnce(
      new Response(JSON.stringify({ token: "old_owner_token", role: "owner" }), {
        status: 200,
        headers: { "Content-Type": "application/json" },
      }),
    );
    await loginToBackend();
    expect(getStoredToken()).toBe("old_owner_token");
    expect(getStoredRole()).toBe("owner");

    localStorage.setItem("tma_token", "old_owner_token");
    localStorage.setItem("tma_role", "owner");
    sessionStorage.setItem("tma_token", "old_owner_session_token");
    sessionStorage.setItem("tma_role", "owner");
    currentInitData.value = "new_recipient_init_data";

    const requests: ObservedRequest[] = [];
    let launchAuthBody = "";
    let resolveLaunchAuth: (response: Response) => void = () => undefined;
    const launchAuthResponse = new Promise<Response>((resolve) => {
      resolveLaunchAuth = resolve;
    });
    globalThis.fetch = vi.fn(
      async (input: RequestInfo | URL, init?: RequestInit) => {
        const request = input instanceof Request ? input : new Request(input, init);
        const path = new URL(request.url).pathname;
        requests.push({
          authorization: request.headers.get("Authorization"),
          path,
        });
        if (path === "/api/tma/auth") {
          launchAuthBody = await request.clone().text();
          return launchAuthResponse;
        }
        return new Response(JSON.stringify(makeOperatorSnapshot()), {
          status: 200,
          headers: { "Content-Type": "application/json" },
        });
      },
    ) as typeof fetch;

    render(
      <StrictMode>
        <AuthGuard>
          <OperatorRealtimeBridge>
            <ProtectedDataProbe />
          </OperatorRealtimeBridge>
        </AuthGuard>
      </StrictMode>,
    );

    await waitFor(() =>
      expect(requests).toEqual([
        { authorization: null, path: "/api/tma/auth" },
      ]),
    );
    expect(screen.queryByText("protected-loading")).not.toBeInTheDocument();
    expect(useOperatorRealtime).not.toHaveBeenCalled();
    expect(getStoredToken()).toBeNull();
    expect(getStoredRole()).toBeNull();
    expect(launchAuthBody).toBe(
      JSON.stringify({ init_data: "new_recipient_init_data" }),
    );
    expect(launchAuthBody).not.toContain("old_owner_init_data");

    resolveLaunchAuth(
      new Response(
        JSON.stringify({ token: "new_recipient_token", role: "recipient" }),
        {
          status: 200,
          headers: { "Content-Type": "application/json" },
        },
      ),
    );

    expect(await screen.findByText("protected-loaded")).toBeInTheDocument();
    const launchAuthRequests = requests.filter(
      ({ path }) => path === "/api/tma/auth",
    );
    const protectedRequests = requests.filter(
      ({ path }) => path === "/api/operator/snapshot",
    );
    expect(launchAuthRequests).toEqual([
      { authorization: null, path: "/api/tma/auth" },
    ]);
    expect(protectedRequests.length).toBeGreaterThan(0);
    expect(protectedRequests).toEqual(
      protectedRequests.map(() => ({
        authorization: "Bearer new_recipient_token",
        path: "/api/operator/snapshot",
      })),
    );
    expect(getStoredToken()).toBe("new_recipient_token");
    expect(getStoredRole()).toBe("recipient");
    expect(localStorage.getItem("tma_token")).toBeNull();
    expect(localStorage.getItem("tma_role")).toBeNull();
    expect(sessionStorage.getItem("tma_token")).toBeNull();
    expect(sessionStorage.getItem("tma_role")).toBeNull();
    expect(useOperatorRealtime).toHaveBeenCalledWith(
      expect.objectContaining({
        enabled: true,
        protocols: ["fb-operator-v1", "tma.new_recipient_token"],
      }),
    );
    const realtimeProtocols = useOperatorRealtime.mock.calls.flatMap(
      ([options]) =>
        (options as { protocols?: string[] } | undefined)?.protocols ?? [],
    );
    expect(realtimeProtocols).not.toContain("tma.old_owner_token");
    expect(realtimeProtocols).not.toContain("tma.old_owner_session_token");
  });
});
