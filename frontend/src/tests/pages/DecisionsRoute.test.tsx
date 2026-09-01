import type { ComponentType } from "react";
import { render, screen } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";

import type { OperatorSnapshot } from "@fb/shared/operator/contracts";
import { makeOperatorSnapshot } from "@fb/shared/operator/testFixture";
import { OperatorRealtimeStatusProvider, type OperatorRealtimeStatus } from "@fb/operator-api";

vi.mock("@tanstack/react-router", () => ({
  createFileRoute: () => (options: { component: ComponentType }) => options,
}));

const useOperatorSnapshot = vi.fn();
vi.mock("@/lib/api/operator", () => ({
  useOperatorSnapshot: (...args: unknown[]) => useOperatorSnapshot(...args),
  operatorProblemMessage: (error: unknown) =>
    error instanceof Error ? error.message : "Неизвестная ошибка",
}));

const decisionsFeedProps = vi.fn();
vi.mock("@/features/decisions/DecisionsFeed", () => ({
  DecisionsFeed: (props: { snapshot: OperatorSnapshot; realtimeConnected: boolean }) => {
    decisionsFeedProps(props);
    return <div data-testid="decisions-feed" />;
  },
}));

import { Route } from "@/routes/decisions/index";

const DecisionsPage = (Route as unknown as { component: ComponentType }).component;

function renderPage(status: OperatorRealtimeStatus = "connected") {
  return render(
    <OperatorRealtimeStatusProvider status={status}>
      <DecisionsPage />
    </OperatorRealtimeStatusProvider>,
  );
}

describe("маршрут /decisions", () => {
  it("показывает скелет, пока снимок ещё не загружен", () => {
    useOperatorSnapshot.mockReturnValue({
      data: undefined,
      isLoading: true,
      isError: false,
      error: null,
      refetch: vi.fn(),
    });
    renderPage();

    expect(screen.getByRole("status", { name: "Загрузка решений" })).toBeInTheDocument();
    expect(screen.queryByTestId("decisions-feed")).not.toBeInTheDocument();
  });

  it("показывает отказ, если снимок недоступен без кэша", () => {
    useOperatorSnapshot.mockReturnValue({
      data: undefined,
      isLoading: false,
      isError: true,
      error: new Error("Снимок недоступен"),
      refetch: vi.fn(),
    });
    renderPage();

    expect(screen.getByRole("alert")).toBeInTheDocument();
    expect(screen.queryByTestId("decisions-feed")).not.toBeInTheDocument();
  });

  it("понижает секции до stale, пока realtime-канал не сверился, прежде чем передать снимок в ленту", () => {
    const snapshot = makeOperatorSnapshot();
    useOperatorSnapshot.mockReturnValue({
      data: snapshot,
      isLoading: false,
      isError: false,
      error: null,
      refetch: vi.fn(),
    });
    renderPage("reconnecting");

    expect(screen.getByTestId("decisions-feed")).toBeInTheDocument();
    const props = decisionsFeedProps.mock.calls.at(-1)?.[0] as {
      snapshot: OperatorSnapshot;
      realtimeConnected: boolean;
    };
    expect(props.realtimeConnected).toBe(false);
    // Кэшированный "ready" снимок не остаётся текущим без сверки канала.
    expect(props.snapshot.attention.state).toBe("stale");
  });

  it("передаёт снимок как есть, когда realtime-канал подключён", () => {
    const snapshot = makeOperatorSnapshot();
    useOperatorSnapshot.mockReturnValue({
      data: snapshot,
      isLoading: false,
      isError: false,
      error: null,
      refetch: vi.fn(),
    });
    renderPage("connected");

    const props = decisionsFeedProps.mock.calls.at(-1)?.[0] as {
      snapshot: OperatorSnapshot;
      realtimeConnected: boolean;
    };
    expect(props.realtimeConnected).toBe(true);
    expect(props.snapshot.attention.state).toBe("ready");
  });
});
