import type { ComponentType, ReactNode } from "react";
import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { beforeEach, describe, expect, it, vi } from "vitest";

import { makeOperatorSnapshot } from "@fb/shared/operator/testFixture";

const mockUseOperatorSnapshot = vi.fn();
const mockUseOperatorCabinetSnapshot = vi.fn();
const mockScan = vi.fn();
const mockNavigate = vi.fn();
const mockUseOperatorRealtimeStatus = vi.fn(() => "connected");
const { mockHapticNotify } = vi.hoisted(() => ({
  mockHapticNotify: vi.fn(),
}));

vi.mock("@tanstack/react-router", () => ({
  createFileRoute: () => (options: { component: ComponentType }) => options,
  useNavigate: () => mockNavigate,
  Link: ({
    children,
    to,
    params,
    ...props
  }: {
    children: ReactNode;
    to: string;
    params?: Record<string, string>;
  }) => (
    <a href={to.replace("$actionId", params?.actionId ?? "")} {...props}>
      {children}
    </a>
  ),
}));

vi.mock("@/lib/operatorApi", () => ({
  useOperatorSnapshot: (...args: unknown[]) => mockUseOperatorSnapshot(...args),
  useOperatorCabinetSnapshot: (...args: unknown[]) =>
    mockUseOperatorCabinetSnapshot(...args),
  useOperatorScanNow: () => ({ mutateAsync: mockScan, isPending: false }),
  operatorProblemMessage: (error: unknown) =>
    error instanceof Error ? error.message : "Ошибка",
}));

vi.mock("@fb/operator-api", async (importOriginal) => ({
  ...(await importOriginal<typeof import("@fb/operator-api")>()),
  useOperatorRealtimeStatus: () => mockUseOperatorRealtimeStatus(),
}));

vi.mock("@/lib/tg", () => ({
  haptic: { impact: vi.fn(), notify: mockHapticNotify, selection: vi.fn() },
  tgAlert: vi.fn(),
}));

import { Route } from "@/routes/index";
import { readResolvedNavigation } from "@/lib/transientNavigation";

const Dashboard = (Route as unknown as { component: ComponentType }).component;

describe("TMA operator dashboard", () => {
  beforeEach(() => {
    vi.clearAllMocks();
    window.sessionStorage.clear();
    mockUseOperatorRealtimeStatus.mockReturnValue("connected");
    mockUseOperatorSnapshot.mockReturnValue({
      data: makeOperatorSnapshot(),
      isLoading: false,
      isError: false,
      error: null,
      refetch: vi.fn(),
    });
  });

  it("opens attention targets through opaque TMA navigation", async () => {
    render(<Dashboard />);

    await userEvent.click(
      screen.getByRole("button", { name: "Открыть объявление" }),
    );

    expect(readResolvedNavigation()).toEqual({
      target_kind: "ad",
      target_id: "ad-1",
    });
    expect(mockNavigate).toHaveBeenCalledWith({ to: "/open" });
    expect(window.location.href).not.toContain("ad-1");
  });

  it("keeps the action-first ledger order on the compact shell", () => {
    render(<Dashboard />);
    const headings = screen
      .getAllByRole("heading", { level: 2 })
      .map((heading) => heading.textContent);
    expect(headings).toEqual([
      "Требует внимания",
      "Портфель",
      "Действия",
      "Воронка",
    ]);
    expect(screen.queryByText("Накопительный расход")).not.toBeInTheDocument();
  });

  it("renders action-first sections and the corrected scan control", () => {
    render(<Dashboard />);
    expect(screen.getByRole("heading", { level: 1 })).toHaveTextContent(
      "Сейчас",
    );
    expect(screen.getByText("FB Agent · оператор")).toBeInTheDocument();
    expect(
      screen.getByRole("heading", { name: "Требует внимания", level: 2 }),
    ).toBeInTheDocument();
    expect(screen.getByText("CPL выше базы")).toBeInTheDocument();
    expect(screen.getAllByText("Требует внимания").length).toBeGreaterThan(1);
    expect(screen.getByLabelText("Сканировать сейчас")).toBeInTheDocument();
    expect(screen.getByText("$47.80")).toBeInTheDocument();
    expect(screen.getByText("$39.00")).toBeInTheDocument();
    expect(screen.getByText("$45.00")).toBeInTheDocument();
  });

  it("opens a cabinet through the typed TMA route", async () => {
    render(<Dashboard />);

    await userEvent.click(screen.getByRole("button", { name: /GH_CR2/ }));

    expect(mockNavigate).toHaveBeenCalledWith({
      to: "/cabinets/$cabinetId",
      params: { cabinetId: "123" },
    });
  });

  it("keeps the 202 scan receipt and links to the queued action lifecycle", async () => {
    mockScan.mockResolvedValue({
      status: "queued",
      task_id: 1842,
      correlation_id: "8b8d0c93-15dc-46b4-8fe0-8da6bec3667f",
    });
    render(<Dashboard />);

    await userEvent.click(screen.getByLabelText("Сканировать сейчас"));

    const queued = (
      await screen.findByText("Сканирование поставлено в очередь")
    ).closest('[role="status"]');
    if (!queued) throw new Error("Queued scan status is missing");
    expect(queued).toHaveTextContent("Сканирование поставлено в очередь");
    expect(queued).toHaveTextContent("Задача #1842");
    expect(
      screen.getByRole("link", { name: "Открыть выполнение" }),
    ).toHaveAttribute("href", "/actions/1842");
    expect(mockScan).toHaveBeenCalledWith({});
    expect(mockHapticNotify).toHaveBeenCalledWith("warning");
    expect(mockHapticNotify).not.toHaveBeenCalledWith("success");
  });

  it("labels stale data and does not present it as current", () => {
    const snapshot = makeOperatorSnapshot();
    snapshot.portfolio = {
      ...snapshot.portfolio,
      state: "stale",
      issues: [
        {
          code: "META_STALE",
          title: "Meta давно не обновлялась",
          detail: "Показан последний подтверждённый снимок.",
          severity: "warning",
          correlation_id: "corr-meta",
        },
      ],
    };
    mockUseOperatorSnapshot.mockReturnValue({
      data: snapshot,
      isLoading: false,
      isError: false,
      error: null,
      refetch: vi.fn(),
    });
    render(<Dashboard />);
    expect(screen.getByText("Meta давно не обновлялась")).toBeInTheDocument();
    expect(screen.getAllByText(/снимок устарел/i).length).toBeGreaterThan(0);
  });

  it("never paints degraded money context as a healthy overview", () => {
    const snapshot = makeOperatorSnapshot();
    snapshot.system.data!.severity = "ok";
    snapshot.attention.data!.items = [];
    snapshot.funnel = { ...snapshot.funnel, state: "partial" };
    snapshot.meta = {
      ...snapshot.meta,
      currency: null,
      currency_state: "mixed",
    };
    mockUseOperatorSnapshot.mockReturnValue({
      data: snapshot,
      isLoading: false,
      isError: false,
      error: null,
      refetch: vi.fn(),
    });

    render(<Dashboard />);

    expect(
      screen.getByText("Денежный контекст требует проверки"),
    ).toBeInTheDocument();
    expect(
      screen.getByText("Валюта не подтверждена; суммы скрыты"),
    ).toBeInTheDocument();
    expect(screen.queryByText("$47.80")).not.toBeInTheDocument();
    expect(screen.queryByText("Активных рисков нет")).not.toBeInTheDocument();
  });

  it("hides a stale cabinet scale instead of presenting it as current", () => {
    const snapshot = makeOperatorSnapshot();
    snapshot.portfolio.data!.currency_groups[0]!.state = "stale";
    snapshot.portfolio.data!.currency_groups[0]!.cabinets[0]!.state = "stale";
    mockUseOperatorSnapshot.mockReturnValue({
      data: snapshot,
      isLoading: false,
      isError: false,
      error: null,
      refetch: vi.fn(),
    });

    render(<Dashboard />);

    const cabinet = screen.getByRole("button", {
      name: /GH_CR2/,
    }).parentElement;
    expect(cabinet).toHaveTextContent("снимок устарел");
  });

  it("downgrades the complete cached snapshot while realtime is reconnecting", () => {
    mockUseOperatorRealtimeStatus.mockReturnValue("reconnecting");

    render(<Dashboard />);

    expect(
      screen.getByText("Состояние ещё не подтверждено"),
    ).toBeInTheDocument();
    expect(screen.queryByText("Активных рисков нет")).not.toBeInTheDocument();
    expect(screen.getAllByText(/снимок устарел/i).length).toBeGreaterThan(0);
  });

  it("renders a visible failure instead of legacy dashboard data", () => {
    mockUseOperatorSnapshot.mockReturnValue({
      data: undefined,
      isLoading: false,
      isError: true,
      error: new Error("Snapshot failed"),
      refetch: vi.fn(),
    });
    render(<Dashboard />);
    expect(screen.getByRole("alert")).toHaveTextContent("Snapshot failed");
  });
});
