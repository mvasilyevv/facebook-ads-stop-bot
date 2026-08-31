import type { ComponentType, ReactNode } from "react";
import {
  fireEvent,
  render,
  screen,
  waitFor,
  within,
} from "@testing-library/react";
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

let routeSearch: Record<string, unknown> = {};
const navigate = vi.fn();

vi.mock("@tanstack/react-router", () => ({
  createFileRoute: () => (options: { component: ComponentType }) => ({
    ...options,
    useParams: () => ({ actionId: "1842" }),
    useSearch: () => routeSearch,
  }),
  useNavigate: () => navigate,
  Link: ({ children }: { children: ReactNode }) => <span>{children}</span>,
}));

const useOperatorActions = vi.fn();
const useOperatorAction = vi.fn();
const useOperatorSnapshot = vi.fn();

vi.mock("@/lib/operatorApi", () => ({
  useOperatorActions: (...args: unknown[]) => useOperatorActions(...args),
  useOperatorAction: (...args: unknown[]) => useOperatorAction(...args),
  useOperatorSnapshot: (...args: unknown[]) => useOperatorSnapshot(...args),
  operatorProblemMessage: (error: unknown) =>
    error instanceof Error ? error.message : "Ошибка",
}));

import { Route as ActionsRoute } from "@/routes/actions/index";
import { MiniActionDetail } from "@/routes/actions/ActionDetailView";

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
    });
    useOperatorSnapshot.mockReturnValue({
      data: makeOperatorSnapshot(),
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

  it("collapses consecutive identical actions into one row with a visible repeat counter", () => {
    const response = confirmedResponse();
    const base = response.items[0]!;
    // Порядок как в реальной ленте: новые записи первыми.
    response.items = [
      {
        ...base,
        id: "1844",
        public_id: "#1844",
        updated_at: "2026-07-18T10:15:00Z",
      },
      {
        ...base,
        id: "1843",
        public_id: "#1843",
        updated_at: "2026-07-18T10:14:00Z",
      },
      {
        ...base,
        id: "1842",
        public_id: "#1842",
        updated_at: "2026-07-18T10:13:00Z",
      },
    ];
    useOperatorActions.mockReturnValue({
      data: { pages: [response] },
      isPending: false,
      isError: false,
      hasNextPage: false,
      isFetchingNextPage: false,
      fetchNextPage: vi.fn(),
      refetch: vi.fn(),
    });

    renderWithRealtime(<ActionsPage />, "connected");

    const actionList = screen.getByRole("list");
    expect(within(actionList).getAllByRole("listitem")).toHaveLength(1);
    expect(within(actionList).getByText("×3")).toBeInTheDocument();
    // Строка построена по самому свежему повтору, а не по первому/последнему id.
    expect(within(actionList).getByText("#1844")).toBeInTheDocument();
    expect(within(actionList).queryByText("#1843")).not.toBeInTheDocument();
    expect(within(actionList).queryByText("#1842")).not.toBeInTheDocument();
  });

  it("keeps non-adjacent repeats separate so the timeline is not reshuffled", () => {
    const response = confirmedResponse();
    const base = response.items[0]!;
    // A, A, B, A — вторая «A» не рядом с первой парой и не должна с ней слиться.
    response.items = [
      {
        ...base,
        id: "1845",
        public_id: "#1845",
        updated_at: "2026-07-18T10:16:00Z",
      },
      {
        ...base,
        id: "1844",
        public_id: "#1844",
        updated_at: "2026-07-18T10:15:00Z",
      },
      {
        ...base,
        id: "9001",
        public_id: "#9001",
        state: "failed",
        updated_at: "2026-07-18T10:14:00Z",
      },
      {
        ...base,
        id: "1842",
        public_id: "#1842",
        updated_at: "2026-07-18T10:13:00Z",
      },
    ];
    useOperatorActions.mockReturnValue({
      data: { pages: [response] },
      isPending: false,
      isError: false,
      hasNextPage: false,
      isFetchingNextPage: false,
      fetchNextPage: vi.fn(),
      refetch: vi.fn(),
    });

    renderWithRealtime(<ActionsPage />, "connected");

    const rows = within(screen.getByRole("list")).getAllByRole("listitem");
    expect(rows).toHaveLength(3);
    expect(within(rows[0]!).getByText("×2")).toBeInTheDocument();
    expect(within(rows[0]!).getByText("#1845")).toBeInTheDocument();
    expect(within(rows[1]!).queryByText(/×\d/)).not.toBeInTheDocument();
    // Одиночная запись выглядит как раньше: без счётчика повторов.
    expect(within(rows[2]!).queryByText(/×\d/)).not.toBeInTheDocument();
    expect(within(rows[2]!).getByText("#1842")).toBeInTheDocument();
  });

  it("accumulates a second cursor page below the first without re-sorting it", () => {
    const older = confirmedResponse();
    older.items = [
      {
        ...older.items[0]!,
        id: "1800",
        public_id: "#1800",
        target_label: "PL_VIP",
        updated_at: "2026-07-18T09:00:00Z",
      },
    ];
    const newer = confirmedResponse();
    newer.items = [
      {
        ...newer.items[0]!,
        id: "1900",
        public_id: "#1900",
        target_label: "GH_CR2",
        updated_at: "2026-07-18T11:00:00Z",
      },
    ];
    useOperatorActions.mockReturnValue({
      // Курсорная пагинация «Показать предыдущие»: первая страница — самые
      // новые записи, вторая — более старые, накопленные по клику.
      data: { pages: [newer, older] },
      isPending: false,
      isError: false,
      hasNextPage: false,
      isFetchingNextPage: false,
      fetchNextPage: vi.fn(),
      refetch: vi.fn(),
    });

    renderWithRealtime(<ActionsPage />, "connected");

    const rows = within(screen.getByRole("list")).getAllByRole("listitem");
    expect(rows).toHaveLength(2);
    expect(within(rows[0]!).getByText("#1900")).toBeInTheDocument();
    expect(within(rows[1]!).getByText("#1800")).toBeInTheDocument();
  });

  it("offers a show-more control that fetches the next cursor page", async () => {
    const fetchNextPage = vi.fn();
    useOperatorActions.mockReturnValue({
      data: { pages: [confirmedResponse()] },
      isPending: false,
      isError: false,
      hasNextPage: true,
      isFetchingNextPage: false,
      fetchNextPage,
      refetch: vi.fn(),
    });

    renderWithRealtime(<ActionsPage />, "connected");
    fireEvent.click(screen.getByRole("button", { name: "Показать предыдущие" }));

    await waitFor(() => expect(fetchNextPage).toHaveBeenCalledOnce());
  });

  it("shows only the current query's pages once a filter change discards the old accumulation", () => {
    useOperatorActions.mockReturnValue({
      data: { pages: [confirmedResponse(), confirmedResponse()] },
      isPending: false,
      isError: false,
      hasNextPage: false,
      isFetchingNextPage: false,
      fetchNextPage: vi.fn(),
      refetch: vi.fn(),
    });
    const { rerender } = renderWithRealtime(<ActionsPage />, "connected");
    expect(within(screen.getByRole("list")).getAllByRole("listitem")).toHaveLength(1);

    // Смена фильтра — новый ключ запроса: react-query отдаёт свежую первую
    // страницу, а не хвост от предыдущей выборки.
    const solo = confirmedResponse();
    solo.items[0] = { ...solo.items[0]!, id: "solo", public_id: "#solo" };
    useOperatorActions.mockReturnValue({
      data: { pages: [solo] },
      isPending: false,
      isError: false,
      hasNextPage: false,
      isFetchingNextPage: false,
      fetchNextPage: vi.fn(),
      refetch: vi.fn(),
    });
    rerender(
      <OperatorRealtimeStatusProvider status="connected">
        <ActionsPage />
      </OperatorRealtimeStatusProvider>,
    );

    const rows = within(screen.getByRole("list")).getAllByRole("listitem");
    expect(rows).toHaveLength(1);
    expect(within(rows[0]!).getByText("#solo")).toBeInTheDocument();
  });

  it("reads cancelled and cabinet filters from URL state and sends them to the typed query", () => {
    routeSearch = { state: "cancelled", account_id: "456" };
    renderWithRealtime(<ActionsPage />, "connected");

    expect(useOperatorActions).toHaveBeenLastCalledWith({
      account_id: "456",
      state: ["cancelled"],
    });
  });

  it("uses the accessible filter sheet, returns focus, and writes URL state", async () => {
    renderWithRealtime(<ActionsPage />, "connected");

    const trigger = screen.getByRole("button", {
      name: "Открыть фильтры действий",
    });
    fireEvent.click(trigger);
    const dialog = await screen.findByRole("dialog", {
      name: "Фильтры действий",
    });
    const close = within(dialog).getByRole("button", { name: "Закрыть" });
    await waitFor(() => expect(close).toHaveFocus());
    expect(close).toHaveClass("size-11", "touch-manipulation");
    expect(within(dialog).getByLabelText("Кабинет")).toHaveClass("min-h-11");

    fireEvent.change(within(dialog).getByLabelText("Состояние действия"), {
      target: { value: "cancelled" },
    });
    const navigation = navigate.mock.calls.at(-1)?.[0] as {
      search: (previous: Record<string, unknown>) => Record<string, unknown>;
      replace: boolean;
    };
    expect(navigation.search({ account_id: "123", state: "queued" })).toEqual({
      account_id: "123",
      state: "cancelled",
    });

    fireEvent.click(close);
    await waitFor(() => expect(trigger).toHaveFocus());
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
    });

    renderWithRealtime(<MiniActionDetail actionId="1842" />, "connected");

    expect(screen.getByText("Проверить объявление")).toBeInTheDocument();
  });

  it("shows an honest error with retry instead of hanging on 'Загрузка…' when the request fails", () => {
    const refetch = vi.fn();
    useOperatorAction.mockReturnValue({
      data: undefined,
      isPending: false,
      isError: true,
      error: new Error("Сеть недоступна"),
      refetch,
    });

    renderWithRealtime(<MiniActionDetail actionId="1842" />, "connected");

    expect(screen.queryByText("Загрузка действия…")).not.toBeInTheDocument();
    expect(screen.getByText("Сеть недоступна")).toBeInTheDocument();
    const retry = screen.getByRole("button", { name: "Повторить" });
    fireEvent.click(retry);
    expect(refetch).toHaveBeenCalledTimes(1);
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
