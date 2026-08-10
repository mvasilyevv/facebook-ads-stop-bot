import type { ComponentType, ReactNode } from "react";
import { render, screen } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";

import { makeOperatorSnapshot } from "@fb/shared/operator/testFixture";

const mockUseOperatorSnapshot = vi.fn();
const mockRealtimeStatus = vi.fn(() => "connected");

vi.mock("@tanstack/react-router", () => ({
  createFileRoute: () => (options: { component: ComponentType }) => options,
}));

vi.mock("@fb/operator-api", async (importOriginal) => ({
  ...(await importOriginal<typeof import("@fb/operator-api")>()),
  useOperatorRealtimeStatus: () => mockRealtimeStatus(),
}));

vi.mock("@/lib/api/operator", () => ({
  useOperatorSnapshot: (...args: unknown[]) => mockUseOperatorSnapshot(...args),
  operatorProblemMessage: (error: unknown) => (error instanceof Error ? error.message : "Ошибка"),
}));

vi.mock("@/components/layout/PageHeader", () => ({
  PageHeader: ({ title, action }: { title: string; action?: ReactNode }) => (
    <header>
      <h1>{title}</h1>
      {action}
    </header>
  ),
}));

import { Route } from "@/routes/system/sources";

const SystemSourcesPage = (Route as unknown as { component: ComponentType }).component;

describe("web operator system sources", () => {
  beforeEach(() => {
    vi.clearAllMocks();
    mockRealtimeStatus.mockReturnValue("connected");
    mockUseOperatorSnapshot.mockReturnValue({
      data: makeOperatorSnapshot(),
      isLoading: false,
      isFetching: false,
      isError: false,
      error: null,
      refetch: vi.fn(),
    });
  });

  it("renders the canonical source and worker snapshot", () => {
    render(<SystemSourcesPage />);

    expect(screen.getByRole("heading", { name: "Источники и воркеры" })).toBeInTheDocument();
    expect(screen.getByText("Observer")).toBeInTheDocument();
    expect(screen.getByText("Browser agent")).toBeInTheDocument();
    expect(screen.getByText("Включён")).toBeInTheDocument();
  });

  it("never leaks green worker state during reconciliation", () => {
    mockRealtimeStatus.mockReturnValue("reconnecting");

    render(<SystemSourcesPage />);

    expect(screen.getAllByText("Состояние не подтверждено").length).toBe(2);
    expect(screen.queryByText("В работе")).not.toBeInTheDocument();
    expect(screen.getByText("Live-связь восстанавливается")).toBeInTheDocument();
  });

  it("renders a concrete failure state", () => {
    mockUseOperatorSnapshot.mockReturnValue({
      data: undefined,
      isLoading: false,
      isFetching: false,
      isError: true,
      error: new Error("Snapshot failed"),
      refetch: vi.fn(),
    });

    render(<SystemSourcesPage />);

    expect(screen.getByText("Snapshot failed")).toBeInTheDocument();
  });
});
