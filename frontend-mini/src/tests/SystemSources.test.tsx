import type { ComponentType } from "react";
import { render, screen } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";

import { makeOperatorSnapshot } from "@fb/shared/operator/testFixture";

const mockUseOperatorSnapshot = vi.fn();
const mockRealtimeStatus = vi.fn(() => "connected");

vi.mock("@tanstack/react-router", () => ({
  createFileRoute:
    () =>
    (options: { component: ComponentType }) =>
      options,
}));

vi.mock("@fb/operator-api", async (importOriginal) => ({
  ...(await importOriginal<typeof import("@fb/operator-api")>()),
  useOperatorRealtimeStatus: () => mockRealtimeStatus(),
}));

vi.mock("@/lib/operatorApi", () => ({
  useOperatorSnapshot: (...args: unknown[]) => mockUseOperatorSnapshot(...args),
  operatorProblemMessage: (error: unknown) =>
    error instanceof Error ? error.message : "Ошибка",
}));

vi.mock("@/components/layout/MiniHeader", () => ({
  MiniHeader: ({ title }: { title: string }) => <header>{title}</header>,
}));

import { Route } from "@/routes/system/sources";

const SystemSourcesPage = (Route as unknown as { component: ComponentType })
  .component;

describe("TMA system sources route", () => {
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

  it("renders source health from the canonical operator snapshot", () => {
    render(<SystemSourcesPage />);

    expect(screen.getByText("Источники и воркеры")).toBeInTheDocument();
    expect(screen.getByText("Observer")).toBeInTheDocument();
    expect(screen.getByText("Browser agent")).toBeInTheDocument();
    expect(screen.getByText("Включён")).toBeInTheDocument();
  });

  it("renders the eleven background workers separately from scan actors (issue #176)", () => {
    render(<SystemSourcesPage />);

    expect(screen.getByRole("list", { name: "Фоновые воркеры" })).toBeInTheDocument();
    expect(screen.getByText("Создание кампаний")).toBeInTheDocument();
    expect(screen.getAllByText("В работе").length).toBeGreaterThanOrEqual(1);
    expect(screen.getByText("Сверка задач")).toBeInTheDocument();
    expect(screen.getByText("Не разбирает очередь")).toBeInTheDocument();
  });

  it("neutralizes cached worker state while realtime is reconnecting", () => {
    mockRealtimeStatus.mockReturnValue("reconnecting");

    render(<SystemSourcesPage />);

    expect(screen.getAllByText("Состояние не подтверждено").length).toBe(4);
    expect(screen.getAllByText("Не подтверждено").length).toBeGreaterThan(0);
    expect(screen.queryByText("В работе")).not.toBeInTheDocument();
    expect(screen.getByText("Live-связь восстанавливается")).toBeInTheDocument();
  });

  it("shows a concrete snapshot failure", () => {
    mockUseOperatorSnapshot.mockReturnValue({
      data: undefined,
      isLoading: false,
      isFetching: false,
      isError: true,
      error: new Error("Snapshot failed"),
      refetch: vi.fn(),
    });

    render(<SystemSourcesPage />);

    expect(screen.getByRole("alert")).toHaveTextContent("Snapshot failed");
  });
});
