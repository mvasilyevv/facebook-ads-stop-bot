import type { ComponentType, ReactNode } from "react";
import { fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";

import type { OperatorActionsResponse } from "@fb/shared/operator/contracts";
import { makeOperatorScopeEvidence, makeOperatorSnapshot } from "@fb/shared/operator/testFixture";
import { actionProjectionFromResponse } from "@fb/shared/operator/viewModel";
import { OperatorRealtimeStatusProvider, type OperatorRealtimeStatus } from "@fb/operator-api";

let routeSearch: Record<string, unknown> = {};
const navigate = vi.fn();

vi.mock("@tanstack/react-router", () => ({
  createFileRoute: () => (options: { component: ComponentType }) => ({
    ...options,
    useParams: () => ({ actionId: "1842" }),
    useSearch: () => routeSearch,
  }),
  useNavigate: () => navigate,
  Link: ({ children }: { children: ReactNode }) => <a href="#action">{children}</a>,
}));

const useOperatorActions = vi.fn();
const useOperatorAction = vi.fn();
const useOperatorSnapshot = vi.fn();

vi.mock("@/lib/api/operator", () => ({
  useOperatorActions: (...args: unknown[]) => useOperatorActions(...args),
  useOperatorAction: (...args: unknown[]) => useOperatorAction(...args),
  useOperatorSnapshot: (...args: unknown[]) => useOperatorSnapshot(...args),
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
    routeSearch = {};
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
    useOperatorSnapshot.mockReturnValue({
      data: makeOperatorSnapshot(),
      isError: false,
    });
  });

  it("reads cancelled and cabinet filters from URL state and sends a fresh typed query", () => {
    routeSearch = { state: "cancelled", account_id: "456" };

    renderWithRealtime(<ActionsPage />, "connected");

    expect(useOperatorActions).toHaveBeenCalledWith({
      account_id: "456",
      state: ["cancelled"],
    });
    expect(screen.getByRole("option", { name: "Отменены" })).toBeInTheDocument();
    expect(screen.getByRole("option", { name: "PL_VIP" })).toBeInTheDocument();
  });

  it("opens mobile filters as a focus-managed dialog with 44px controls and resets URL state", async () => {
    renderWithRealtime(<ActionsPage />, "connected");

    fireEvent.click(screen.getByRole("button", { name: "Открыть фильтры действий" }));
    const dialog = await screen.findByRole("dialog", { name: "Фильтры действий" });
    const close = within(dialog).getByRole("button", { name: "Закрыть" });
    await waitFor(() => expect(close).toHaveFocus());
    expect(close).toHaveClass("size-11");

    fireEvent.change(within(dialog).getByLabelText("Состояние действия"), {
      target: { value: "cancelled" },
    });
    expect(navigate).toHaveBeenCalled();
    const navigation = navigate.mock.calls.at(-1)?.[0] as {
      search: (previous: Record<string, unknown>) => Record<string, unknown>;
      replace: boolean;
    };
    expect(navigation.replace).toBe(true);
    expect(navigation.search({ account_id: "123", state: "queued" })).toEqual({
      account_id: "123",
      state: "cancelled",
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
    const response = confirmedResponse();
    response.items[0]!.reason = "Traceback: secret-host token=unsafe";
    useOperatorAction.mockReturnValue({
      data: actionProjectionFromResponse(response, "1842"),
      isPending: false,
      isError: false,
      refetch: vi.fn(),
    });

    renderWithRealtime(<ActionDetailPage />, "connected");

    expect(screen.getByText("#1842 · Отключение объявления")).toBeInTheDocument();
    expect(screen.queryByText("#1842 · pause")).not.toBeInTheDocument();
    expect(screen.getAllByText("Подтверждено").length).toBeGreaterThan(0);
    expect(screen.getByText("Результат команды подтверждён.")).toBeInTheDocument();
    expect(screen.queryByText(/Traceback|secret-host|token=unsafe/)).not.toBeInTheDocument();
    expect(screen.getByText("Данные актуальны")).toBeInTheDocument();
    expect(screen.queryByText("Live-связь восстанавливается")).not.toBeInTheDocument();
  });

  it("offers an exact target check after a failed command", () => {
    const response = confirmedResponse();
    response.items[0] = {
      ...response.items[0]!,
      state: "failed",
      target_id: "ad-42",
    };
    useOperatorAction.mockReturnValue({
      data: actionProjectionFromResponse(response, "1842"),
      isPending: false,
      isError: false,
      refetch: vi.fn(),
    });

    renderWithRealtime(<ActionDetailPage />, "connected");

    expect(screen.getByRole("link", { name: "Проверить объявление" })).toBeInTheDocument();
  });

  it("does not expose diagnostic correlation UUIDs", () => {
    const response = confirmedResponse();
    response.items[0]!.correlation_id = "8b8d0c93-15dc-46b4-8fe0-8da6bec3667f";
    useOperatorAction.mockReturnValue({
      data: actionProjectionFromResponse(response, "1842"),
      isPending: false,
      isError: false,
      refetch: vi.fn(),
    });

    renderWithRealtime(<ActionDetailPage />, "connected");

    expect(screen.queryByText("8b8d0c93-15dc-46b4-8fe0-8da6bec3667f")).not.toBeInTheDocument();
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
