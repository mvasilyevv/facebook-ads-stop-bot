import type { ReactNode } from "react";
import { act, render, screen, waitFor } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";

const navigate = vi.fn(async ({ to }: { to: string }) => {
  window.history.replaceState(window.history.state, "", to);
});
const mutateAsync = vi.fn();

vi.mock("@tanstack/react-router", () => ({
  createRootRoute: (options: unknown) => options,
  createFileRoute: () => (options: unknown) => options,
  Outlet: () => null,
  useNavigate: () => navigate,
}));
vi.mock("@fb/operator-api", () => ({ useOperatorRealtime: vi.fn() }));
vi.mock("@/components/layout/AuthGuard", () => ({
  AuthGuard: ({ children }: { children: ReactNode }) => children,
}));
vi.mock("@/components/layout/TabBar", () => ({ TabBar: () => null }));
vi.mock("@/components/layout/TelegramBackButton", () => ({
  TelegramBackButton: () => null,
}));
vi.mock("@/lib/auth", () => ({ getStoredToken: () => null }));
vi.mock("@/lib/operatorApi", () => ({
  useResolveTmaNavigation: () => ({ mutateAsync }),
}));
const tgAlert = vi.hoisted(() => vi.fn());
vi.mock("@/lib/tg", () => ({
  getTgStartParam: () => null,
  tgAlert,
}));
vi.mock("@/routes/actions/ActionDetailView", () => ({
  MiniActionDetail: ({ actionId }: { actionId: string }) => (
    <div>action:{actionId}</div>
  ),
}));
vi.mock("@/routes/ads/$fbAdId", () => ({
  MiniAdDetail: ({ fbAdId }: { fbAdId: string }) => <div>ad:{fbAdId}</div>,
}));
vi.mock("@/routes/incidents/$incidentId", () => ({
  MiniIncidentDetail: ({ incidentId }: { incidentId: string }) => (
    <div>incident:{incidentId}</div>
  ),
}));

import {
  clearResolvedNavigation,
  parseTmaAttentionHref,
  readResolvedNavigation,
  readResolvedNavigationState,
  storeResolvedNavigation,
} from "@/lib/transientNavigation";
import { OpaqueNavigationResolver } from "@/routes/__root";
import { OpaqueTargetPage } from "@/routes/open";

describe("opaque TMA navigation", () => {
  beforeEach(() => {
    vi.clearAllMocks();
    mutateAsync.mockReset();
    clearResolvedNavigation();
    window.history.replaceState({}, "", "/");
  });

  it("keeps the shared analytics attention route available in TMA", () => {
    expect(parseTmaAttentionHref("/analytics")).toEqual({
      kind: "route",
      to: "/analytics",
    });
  });

  it("removes the capability and never places the resolved target id in the URL", async () => {
    const token = "abcdefghijklmnopqrstuv";
    const targetId = "raw-meta-target-123456";
    mutateAsync.mockResolvedValue({ target_kind: "ad", target_id: targetId });
    window.history.replaceState({}, "", `/?nav=${token}`);

    render(<OpaqueNavigationResolver />);

    await waitFor(() =>
      expect(readResolvedNavigation()).toEqual({
        target_kind: "ad",
        target_id: targetId,
      }),
    );
    expect(navigate).toHaveBeenCalledWith({ to: "/open", replace: true });
    expect(window.location.pathname).toBe("/open");
    expect(window.location.href).not.toContain(token);
    expect(window.location.href).not.toContain(targetId);
    expect(readResolvedNavigation()).toEqual({
      target_kind: "ad",
      target_id: targetId,
    });
  });

  it("clears target A while token B resolves and keeps it cleared when B expires", async () => {
    const expiredToken = "zyxwvutsrqponmlkjihgfe";
    let rejectResolution: (reason?: unknown) => void = () => {};
    mutateAsync.mockReturnValue(
      new Promise((_resolve, reject) => {
        rejectResolution = reject;
      }),
    );
    storeResolvedNavigation({
      target_kind: "ad",
      target_id: "previous-ad-A",
    });
    window.history.replaceState({}, "", `/open?nav=${expiredToken}`);

    render(
      <>
        <OpaqueNavigationResolver />
        <OpaqueTargetPage />
      </>,
    );

    expect(readResolvedNavigationState().status).toBe("resolving");
    expect(readResolvedNavigation()).toBeNull();
    expect(screen.getByRole("status")).toHaveTextContent("Проверяем ссылку");
    expect(screen.queryByText("ad:previous-ad-A")).not.toBeInTheDocument();

    await act(async () => {
      rejectResolution(new Error("expired"));
      await Promise.resolve();
    });

    await waitFor(() =>
      expect(readResolvedNavigationState().status).toBe("error"),
    );
    expect(readResolvedNavigation()).toBeNull();
    expect(screen.getByRole("alert")).toHaveTextContent(
      "Ссылка недействительна",
    );
    expect(screen.queryByText("ad:previous-ad-A")).not.toBeInTheDocument();
    expect(tgAlert).toHaveBeenCalledOnce();
  });

  it("lazily renders the resolved target's detail view behind a Suspense boundary", async () => {
    storeResolvedNavigation({ target_kind: "action", target_id: "act-9001" });

    render(<OpaqueTargetPage />);

    // Компонент детали грузится динамическим import() (см. open.tsx) —
    // сразу после рендера доступен только fallback-скелет.
    expect(screen.getByRole("status", { name: "Загрузка" })).toBeInTheDocument();

    await waitFor(() =>
      expect(screen.getByText("action:act-9001")).toBeInTheDocument(),
    );
  });
});
