import type { ComponentType, ReactNode } from "react";
import { render, screen, within } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";

import type { OperatorActionsResponse } from "@fb/shared/operator/contracts";
import { makeOperatorScopeEvidence, makeOperatorSnapshot } from "@fb/shared/operator/testFixture";
import { actionProjectionFromResponse } from "@fb/shared/operator/viewModel";
import { OperatorRealtimeStatusProvider, type OperatorRealtimeStatus } from "@fb/operator-api";

vi.mock("@tanstack/react-router", () => ({
  createFileRoute: () => (options: { component: ComponentType }) => ({
    ...options,
    useParams: () => ({ actionId: "1842" }),
  }),
  Link: ({ children }: { children: ReactNode }) => <a href="#action">{children}</a>,
}));

const useOperatorActions = vi.fn();
const useOperatorAction = vi.fn();

vi.mock("@/lib/api/operator", () => ({
  useOperatorActions: (...args: unknown[]) => useOperatorActions(...args),
  useOperatorAction: (...args: unknown[]) => useOperatorAction(...args),
  operatorProblemMessage: (error: unknown) => (error instanceof Error ? error.message : "Ошибка"),
}));

import { Route as ActionsRoute } from "@/routes/actions/index";
import { Route as ActionDetailRoute } from "@/routes/actions/$actionId";

const ActionsPage = (ActionsRoute as unknown as { component: ComponentType }).component;
const ActionDetailPage = (ActionDetailRoute as unknown as { component: ComponentType }).component;

function confirmedResponse(): OperatorActionsResponse {
  const base = makeOperatorSnapshot().actions.data!.items[0]!;
  return {
    state: "ready",
    as_of: base.updated_at,
    freshness_seconds: 3,
    sources: ["task_queue"],
    issues: [],
    scope: makeOperatorScopeEvidence(),
    items: [{ ...base, state: "confirmed" }],
    next_cursor: null,
  };
}

function renderWithRealtime(component: ReactNode, status: OperatorRealtimeStatus) {
  return render(
    <OperatorRealtimeStatusProvider status={status}>{component}</OperatorRealtimeStatusProvider>,
  );
}

describe("web actions realtime projection", () => {
  beforeEach(() => {
    vi.clearAllMocks();
    const response = confirmedResponse();
    useOperatorActions.mockReturnValue({
      data: { pages: [response] },
      isPending: false,
      isError: false,
      hasNextPage: false,
      isFetchingNextPage: false,
      fetchNextPage: vi.fn(),
      refetch: vi.fn(),
    });
    useOperatorAction.mockReturnValue({
      data: actionProjectionFromResponse(response, "1842"),
      isPending: false,
      isError: false,
      refetch: vi.fn(),
    });
  });

  it("marks a cached list stale and removes its confirmed-green label", () => {
    renderWithRealtime(<ActionsPage />, "reconnecting");

    expect(screen.getByText("Данные устарели")).toBeInTheDocument();
    expect(screen.getByText("Live-связь восстанавливается")).toBeInTheDocument();
    const actionList = screen.getByRole("list", {
      name: "Очередь и история действий",
    });
    expect(within(actionList).getAllByText(/Результат уточняется/).length).toBeGreaterThan(0);
    expect(within(actionList).queryByText("Подтверждено")).not.toBeInTheDocument();
  });

  it("marks cached detail stale and cannot render confirmed as success", () => {
    renderWithRealtime(<ActionDetailPage />, "reconnecting");

    expect(screen.getByText("Данные устарели")).toBeInTheDocument();
    expect(screen.getByText("Live-связь восстанавливается")).toBeInTheDocument();
    const unknownLabels = screen.getAllByText("Результат уточняется");
    expect(unknownLabels.length).toBeGreaterThan(0);
    expect(screen.queryByText("Подтверждено")).not.toBeInTheDocument();
    expect(
      unknownLabels.find((element) => element.classList.contains("inline-flex")),
    ).not.toHaveClass("text-success");
  });

  it("keeps server-confirmed lifecycle visible after reconciliation", () => {
    renderWithRealtime(<ActionDetailPage />, "connected");

    expect(screen.getAllByText("Подтверждено").length).toBeGreaterThan(0);
    expect(screen.getByText("Данные актуальны")).toBeInTheDocument();
    expect(screen.queryByText("Live-связь восстанавливается")).not.toBeInTheDocument();
  });

  it("formats lifecycle timestamps with immutable action timezone evidence", () => {
    const response = confirmedResponse();
    response.items[0] = {
      ...response.items[0]!,
      cabinet_timezone: "Asia/Tokyo",
    };
    useOperatorAction.mockReturnValue({
      data: actionProjectionFromResponse(response, "1842"),
      isPending: false,
      isError: false,
      refetch: vi.fn(),
    });

    renderWithRealtime(<ActionDetailPage />, "connected");

    expect(screen.getByText("Запрошено").parentElement).toHaveTextContent("18.07.2026, 19:12");
    expect(screen.getByText("Часовой пояс").parentElement).toHaveTextContent("Asia/Tokyo");
  });
});
