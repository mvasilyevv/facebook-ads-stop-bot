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

// operatorProblemMessage делегирует канону safeApiProblemMessage: тест обязан
// проверять реальную санитизацию, а не подставлять сырой error.message.
vi.mock("@/lib/api/operator", async () => {
  const { safeApiProblemMessage } = await import("@fb/operator-api");
  return {
    useOperatorSnapshot: (...args: unknown[]) => mockUseOperatorSnapshot(...args),
    operatorProblemMessage: (error: unknown) =>
      safeApiProblemMessage(error, "Операторский снимок недоступен"),
  };
});

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

  it("renders the eleven background workers separately from scan actors (issue #176)", () => {
    render(<SystemSourcesPage />);

    expect(
      screen.getByRole("heading", { name: "Сканирование кабинетов" }),
    ).toBeInTheDocument();
    expect(screen.getByRole("heading", { name: "Фоновые воркеры" })).toBeInTheDocument();
    expect(screen.getByRole("list", { name: "Фоновые воркеры" })).toBeInTheDocument();
    expect(screen.getByText("Создание кампаний")).toBeInTheDocument();
    // Простаивающий воркер (heartbeat жив, очередь пуста) выглядит здоровым,
    // как и здоровый scan actor — оба статуса совпадают на "В работе".
    expect(screen.getAllByText("В работе").length).toBeGreaterThanOrEqual(1);
    // ...а зависший (heartbeat жив, но опрос очереди устарел) — отдельным,
    // явно тревожным статусом, не таким же зелёным.
    expect(screen.getByText("Сверка задач")).toBeInTheDocument();
    expect(screen.getByText("Не разбирает очередь")).toBeInTheDocument();
  });

  it("never leaks green worker state during reconciliation", () => {
    mockRealtimeStatus.mockReturnValue("reconnecting");

    render(<SystemSourcesPage />);

    expect(screen.getAllByText("Состояние не подтверждено").length).toBe(4);
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

    // Сырое сообщение исключения оператору не показывается: только recovery-копия.
    expect(screen.queryByText("Snapshot failed")).not.toBeInTheDocument();
    expect(screen.getByText("Операторский снимок недоступен")).toBeInTheDocument();
  });
});
