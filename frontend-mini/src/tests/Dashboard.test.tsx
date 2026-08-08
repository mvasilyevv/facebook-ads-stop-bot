import type { ComponentType, ReactNode } from "react";
import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { beforeEach, describe, expect, it, vi } from "vitest";

import { makeOperatorSnapshot } from "@fb/shared/operator/testFixture";

const mockUseOperatorSnapshot = vi.fn();
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

  it("uses 44px native controls for chart touch and keyboard points", async () => {
    render(<Dashboard />);
    const chartPoints = screen
      .getAllByRole("button")
      .filter((button) => button.getAttribute("aria-label")?.includes("Факт"));
    const point = chartPoints.find((button) =>
      button.querySelector("[data-actual-marker]"),
    );
    const unknownActualPoint = chartPoints.find((button) =>
      button.getAttribute("aria-label")?.includes("Факт —"),
    );

    expect(point).toBeDefined();
    expect(point).toHaveClass("size-11");
    expect(unknownActualPoint).toBeDefined();
    expect(
      unknownActualPoint?.querySelector("[data-actual-marker]"),
    ).toBeNull();
    expect(
      screen.getByRole("list", { name: "Обозначения графика" }),
    ).toHaveTextContent("ФактБазаStop");
    expect(document.querySelector("[data-current-time-marker]")).not.toBeNull();
    expect(
      document.querySelector("[data-current-time-label]"),
    ).toHaveTextContent("Сейчас");
    await userEvent.click(point!);
    expect(screen.getByRole("status")).toHaveTextContent(/факт/i);
  });

  it("renders action-first sections and the corrected scan control", () => {
    render(<Dashboard />);
    expect(screen.getByRole("heading", { level: 1 })).toHaveTextContent(
      "Контроль",
    );
    expect(screen.getByText("Требует внимания")).toBeInTheDocument();
    expect(screen.getByText("CPL выше базы")).toBeInTheDocument();
    expect(screen.getByLabelText("Внимание")).toBeInTheDocument();
    expect(screen.getByText("Внимание")).toBeInTheDocument();
    expect(screen.getByLabelText("Сканировать сейчас")).toBeInTheDocument();
    expect(screen.getByText(/Стоимость USD.*0\.44/)).toBeInTheDocument();
    expect(screen.getByText(/Стоимость USD.*3\.68/)).toBeInTheDocument();
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
    snapshot.economy = {
      ...snapshot.economy,
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
    expect(screen.getAllByText(/Устарело/).length).toBeGreaterThan(0);
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
    expect(screen.getAllByText("Частично").length).toBeGreaterThan(0);
    expect(screen.queryByText("Активных рисков нет")).not.toBeInTheDocument();
  });

  it("neutralizes cached worker health when the system snapshot is stale", () => {
    const snapshot = makeOperatorSnapshot();
    snapshot.system = { ...snapshot.system, state: "stale" };
    snapshot.meta = { ...snapshot.meta, cabinet_timezone_known: false };
    mockUseOperatorSnapshot.mockReturnValue({
      data: snapshot,
      isLoading: false,
      isError: false,
      error: null,
      refetch: vi.fn(),
    });

    render(<Dashboard />);

    expect(
      screen.getByText("Состояние ещё не подтверждено"),
    ).toBeInTheDocument();
    expect(screen.getByText("Устарело")).toHaveAttribute(
      "data-tone",
      "neutral",
    );
    for (const label of ["Observer", "Browser agent"]) {
      const worker = screen.getByText(label).closest("li");
      expect(worker?.querySelector("[data-severity]")).toHaveAttribute(
        "data-severity",
        "unknown",
      );
      expect(worker).toHaveTextContent("Состояние не подтверждено");
    }
  });

  it("downgrades the complete cached snapshot while realtime is reconnecting", () => {
    mockUseOperatorRealtimeStatus.mockReturnValue("reconnecting");

    render(<Dashboard />);

    expect(
      screen.getByText("Состояние ещё не подтверждено"),
    ).toBeInTheDocument();
    expect(screen.queryByText("Активных рисков нет")).not.toBeInTheDocument();
    expect(screen.getAllByText(/Устарело/).length).toBeGreaterThan(0);
    for (const label of ["Observer", "Browser agent"]) {
      const worker = screen.getByText(label).closest("li");
      expect(worker?.querySelector("[data-severity]")).toHaveAttribute(
        "data-severity",
        "unknown",
      );
    }
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
