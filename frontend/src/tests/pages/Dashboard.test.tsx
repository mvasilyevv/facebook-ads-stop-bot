import type { ComponentType, ReactNode } from "react";
import { render, screen } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";

import { OperatorRealtimeStatusProvider, type OperatorRealtimeStatus } from "@fb/operator-api";
import { makeOperatorSnapshot } from "@fb/shared/operator/testFixture";

const mockUseOperatorSnapshot = vi.fn();
const mockScan = vi.fn();

vi.mock("@tanstack/react-router", () => ({
  createFileRoute: () => (options: { component: ComponentType }) => options,
  Link: ({ children, to, ...props }: { children: ReactNode; to: string }) => (
    <a href={to} {...props}>
      {children}
    </a>
  ),
}));

vi.mock("@/lib/api/operator", () => ({
  useOperatorSnapshot: (...args: unknown[]) => mockUseOperatorSnapshot(...args),
  useOperatorScanNow: () => ({ mutate: mockScan, isPending: false }),
  operatorProblemMessage: (error: unknown) => (error instanceof Error ? error.message : "Ошибка"),
}));

vi.mock("@/components/ui/toastStore", () => ({
  toast: { success: vi.fn(), error: vi.fn() },
}));

import { Route } from "@/routes/index";

const Dashboard = (Route as unknown as { component: ComponentType }).component;

function renderDashboard(status: OperatorRealtimeStatus = "connected") {
  return render(
    <OperatorRealtimeStatusProvider status={status}>
      <Dashboard />
    </OperatorRealtimeStatusProvider>,
  );
}

describe("operator dashboard", () => {
  beforeEach(() => {
    vi.clearAllMocks();
    mockUseOperatorSnapshot.mockReturnValue({
      data: makeOperatorSnapshot(),
      isLoading: false,
      isError: false,
      error: null,
      refetch: vi.fn(),
    });
  });

  it("shows the action-first overview and confirmed money", () => {
    renderDashboard();
    expect(screen.getByRole("heading", { level: 1 })).toHaveTextContent("Сейчас");
    expect(screen.getByText("Есть отклонения, требующие решения")).toBeInTheDocument();
    expect(screen.getByRole("heading", { name: "Портфель" })).toBeInTheDocument();
    expect(screen.getByText("CPL выше базы")).toBeInTheDocument();
    expect(screen.getAllByText(/\$18\.40/).length).toBeGreaterThan(0);
    expect(screen.getByRole("button", { name: "Сканировать" })).toBeInTheDocument();
  });

  it("never turns unavailable data into a green or zero state", () => {
    const snapshot = makeOperatorSnapshot();
    snapshot.system = {
      ...snapshot.system,
      state: "unavailable",
      data: null,
      issues: [
        {
          code: "HEARTBEATS_MISSING",
          title: "Воркеры не отвечают",
          detail: "Нет подтверждённых heartbeat.",
          severity: "critical",
          correlation_id: "corr-down",
        },
      ],
    };
    snapshot.economy = { ...snapshot.economy, state: "unavailable", data: null };
    mockUseOperatorSnapshot.mockReturnValue({
      data: snapshot,
      isLoading: false,
      isError: false,
      error: null,
      refetch: vi.fn(),
    });

    renderDashboard();
    expect(screen.getAllByText("Источник недоступен").length).toBeGreaterThan(0);
    expect(screen.getByText("Состояние ещё не подтверждено")).toBeInTheDocument();
    expect(screen.queryByText("Активных рисков нет")).not.toBeInTheDocument();
    expect(screen.queryByText("$0.00")).not.toBeInTheDocument();
  });

  it("surfaces partial sections while retaining explicitly marked data", () => {
    const snapshot = makeOperatorSnapshot();
    snapshot.funnel = {
      ...snapshot.funnel,
      state: "partial",
      issues: [
        {
          code: "TRACKER_DELAYED",
          title: "Tracker задерживается",
          detail: "Показана только подтверждённая часть воронки.",
          severity: "warning",
          correlation_id: "corr-tracker",
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

    renderDashboard();
    expect(screen.getByText("Tracker задерживается")).toBeInTheDocument();
    expect(screen.getByText("Клики")).toBeInTheDocument();
  });

  it("never paints degraded money context as a healthy overview", () => {
    const snapshot = makeOperatorSnapshot();
    snapshot.system.data!.severity = "ok";
    snapshot.attention.data!.items = [];
    snapshot.economy = { ...snapshot.economy, state: "partial" };
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

    renderDashboard();

    expect(screen.getByText("Денежный контекст требует проверки")).toBeInTheDocument();
    expect(screen.getAllByText("Данные неполные").length).toBeGreaterThan(0);
    expect(screen.getByText("Валюта не подтверждена")).toBeInTheDocument();
    expect(screen.queryByText("$47.80")).not.toBeInTheDocument();
    expect(screen.queryByText("Активных рисков нет")).not.toBeInTheDocument();
  });

  it("does not render a server-provided action outside the internal allowlist", () => {
    const snapshot = makeOperatorSnapshot();
    snapshot.attention.data!.items[0]!.action = {
      label: "Открыть",
      href: "https://example.invalid/operator",
    };
    mockUseOperatorSnapshot.mockReturnValue({
      data: snapshot,
      isLoading: false,
      isError: false,
      error: null,
      refetch: vi.fn(),
    });

    renderDashboard();

    expect(screen.queryByRole("link", { name: "Открыть" })).not.toBeInTheDocument();
  });

  it("renders an actionable unavailable state for request failures", () => {
    mockUseOperatorSnapshot.mockReturnValue({
      data: undefined,
      isLoading: false,
      isError: true,
      error: new Error("Network down"),
      refetch: vi.fn(),
    });
    renderDashboard();
    expect(screen.getByRole("alert")).toHaveTextContent("Операторский снимок недоступен");
    expect(screen.getByRole("button", { name: "Повторить" })).toBeInTheDocument();
  });

  it("never presents a cached snapshot as current while realtime reconnects", () => {
    renderDashboard("reconnecting");

    expect(screen.getByText("Состояние ещё не подтверждено")).toBeInTheDocument();
    expect(screen.getAllByText("Данные устарели").length).toBeGreaterThan(0);
    expect(screen.queryByText("Данные актуальны")).not.toBeInTheDocument();
    expect(screen.queryByText("Активных рисков нет")).not.toBeInTheDocument();
    expect(screen.queryByText("В работе")).not.toBeInTheDocument();
    expect(screen.getByText("Выполняется")).toBeInTheDocument();
    expect(screen.queryByText("Подтверждено")).not.toBeInTheDocument();
  });
});
