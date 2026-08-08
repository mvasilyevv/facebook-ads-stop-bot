import type { ComponentType, ReactNode } from "react";
import { fireEvent, render, screen, within } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";

import type { OperatorActionsResponse } from "@fb/shared/operator/contracts";
import {
  makeOperatorScopeEvidence,
  makeOperatorSnapshot,
} from "@fb/shared/operator/testFixture";
import { actionProjectionFromResponse } from "@fb/shared/operator/viewModel";
import {
  OperatorRealtimeStatusProvider,
  type OperatorRealtimeStatus,
} from "@fb/operator-api";

vi.mock("@tanstack/react-router", () => ({
  createFileRoute: () => (options: { component: ComponentType }) => ({
    ...options,
    useParams: () => ({ actionId: "1842" }),
  }),
  Link: ({ children }: { children: ReactNode }) => <span>{children}</span>,
}));

const useOperatorActions = vi.fn();
const useOperatorAction = vi.fn();

vi.mock("@/lib/operatorApi", () => ({
  useOperatorActions: (...args: unknown[]) => useOperatorActions(...args),
  useOperatorAction: (...args: unknown[]) => useOperatorAction(...args),
  operatorProblemMessage: (error: unknown) =>
    error instanceof Error ? error.message : "Ошибка",
}));

import { Route as ActionsRoute } from "@/routes/actions/index";
import { MiniActionDetail } from "@/routes/actions/$actionId";

const ActionsPage = (ActionsRoute as unknown as { component: ComponentType })
  .component;

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

function renderWithRealtime(
  component: ReactNode,
  status: OperatorRealtimeStatus,
) {
  return render(
    <OperatorRealtimeStatusProvider status={status}>
      {component}
    </OperatorRealtimeStatusProvider>,
  );
}

describe("TMA actions realtime projection", () => {
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
    });
  });

  it("marks a cached mobile list stale and removes confirmed-green output", () => {
    renderWithRealtime(<ActionsPage />, "reconnecting");

    expect(screen.getByText("Устарело")).toBeInTheDocument();
    expect(
      screen.getByText("Live-связь восстанавливается"),
    ).toBeInTheDocument();
    const actionList = screen.getByRole("list");
    expect(
      within(actionList).getByText(/Результат уточняется/),
    ).toBeInTheDocument();
    expect(
      within(actionList).queryByText("Подтверждено"),
    ).not.toBeInTheDocument();
  });

  it("offers the queued lifecycle filter and sends it to the typed query", () => {
    renderWithRealtime(<ActionsPage />, "connected");

    fireEvent.click(screen.getByRole("button", { name: "В очереди" }));

    expect(useOperatorActions).toHaveBeenLastCalledWith({
      state: ["queued"],
    });
  });

  it("marks cached mobile detail stale and projects confirmation to unknown", () => {
    renderWithRealtime(<MiniActionDetail actionId="1842" />, "reconnecting");

    expect(screen.getByText("Устарело")).toBeInTheDocument();
    expect(
      screen.getByText("Live-связь восстанавливается"),
    ).toBeInTheDocument();
    expect(screen.getByText("Результат уточняется")).toBeInTheDocument();
    expect(screen.queryByText("Подтверждено")).not.toBeInTheDocument();
    expect(screen.getByText("Результат уточняется")).not.toHaveClass(
      "text-success",
    );
  });

  it("restores confirmed output only after realtime reconciliation", () => {
    const response = confirmedResponse();
    response.items[0]!.reason = "Traceback: secret-host token=unsafe";
    useOperatorAction.mockReturnValue({
      data: actionProjectionFromResponse(response, "1842"),
      isPending: false,
      isError: false,
    });

    renderWithRealtime(<MiniActionDetail actionId="1842" />, "connected");

    expect(
      screen.getByText("#1842 · Отключение объявления"),
    ).toBeInTheDocument();
    expect(screen.queryByText("#1842 · pause")).not.toBeInTheDocument();
    expect(screen.getByText("Актуально")).toBeInTheDocument();
    expect(screen.getByText("Подтверждено")).toBeInTheDocument();
    expect(
      screen.getByText("Результат команды подтверждён."),
    ).toBeInTheDocument();
    expect(
      screen.queryByText(/Traceback|secret-host|token=unsafe/),
    ).not.toBeInTheDocument();
    expect(
      screen.queryByText("Live-связь восстанавливается"),
    ).not.toBeInTheDocument();
  });

  it("does not expose diagnostic correlation UUIDs", () => {
    const response = confirmedResponse();
    response.items[0]!.correlation_id = "8b8d0c93-15dc-46b4-8fe0-8da6bec3667f";
    useOperatorAction.mockReturnValue({
      data: actionProjectionFromResponse(response, "1842"),
      isPending: false,
      isError: false,
    });

    renderWithRealtime(<MiniActionDetail actionId="1842" />, "connected");

    expect(
      screen.queryByText("8b8d0c93-15dc-46b4-8fe0-8da6bec3667f"),
    ).not.toBeInTheDocument();
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
    });

    renderWithRealtime(<MiniActionDetail actionId="1842" />, "connected");

    expect(screen.getByText("Запрошено").parentElement).toHaveTextContent(
      "18.07.2026, 19:12",
    );
    expect(screen.getByText("Часовой пояс").parentElement).toHaveTextContent(
      "Asia/Tokyo",
    );
  });
});
