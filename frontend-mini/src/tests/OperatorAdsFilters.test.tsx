import type { ComponentType } from "react";
import {
  fireEvent,
  render,
  screen,
  waitFor,
  within,
} from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";

import type {
  OperatorAdsQuery,
  OperatorAdsResponse,
} from "@fb/shared/operator/contracts";
import {
  makeOperatorScopeEvidence,
  makeOperatorSnapshot,
} from "@fb/shared/operator/testFixture";
import { OperatorRealtimeStatusProvider } from "@fb/operator-api";

let routeSearch: Record<string, unknown> = {};
const navigate = vi.fn();

vi.mock("@tanstack/react-router", () => ({
  createFileRoute: () => (options: { component: ComponentType }) => ({
    ...options,
    useSearch: () => routeSearch,
  }),
  useNavigate: () => navigate,
}));

vi.mock("@/components/layout/MiniHeader", () => ({
  MiniHeader: ({
    title,
    right,
  }: {
    title: string;
    right?: React.ReactNode;
  }) => (
    <header>
      <h1>{title}</h1>
      {right}
    </header>
  ),
}));

vi.mock("@/lib/tg", () => ({
  haptic: { selection: vi.fn(), impact: vi.fn(), notify: vi.fn() },
}));

const useOperatorAds = vi.fn();
const useOperatorSnapshot = vi.fn();

vi.mock("@/lib/operatorApi", () => ({
  useOperatorAds: (...args: unknown[]) => useOperatorAds(...args),
  useOperatorSnapshot: (...args: unknown[]) => useOperatorSnapshot(...args),
  operatorProblemMessage: (error: unknown) =>
    error instanceof Error ? error.message : "Ошибка",
}));

import { Route } from "@/routes/ads/index";

const AdsPage = (Route as unknown as { component: ComponentType }).component;

function emptyResponse(): OperatorAdsResponse {
  return {
    state: "empty",
    as_of: "2026-08-09T10:00:00Z",
    freshness_seconds: 1,
    sources: ["meta"],
    issues: [],
    scope: makeOperatorScopeEvidence(),
    rows: [],
    page: 1,
    page_size: 30,
    total: 0,
    pages: 0,
  };
}

function renderPage() {
  return render(
    <OperatorRealtimeStatusProvider status="connected">
      <AdsPage />
    </OperatorRealtimeStatusProvider>,
  );
}

describe("TMA operator ads URL filters", () => {
  beforeEach(() => {
    vi.clearAllMocks();
    routeSearch = {};
    useOperatorAds.mockReturnValue({
      data: emptyResponse(),
      isPending: false,
      isFetching: false,
      isError: false,
      error: null,
      refetch: vi.fn(),
    });
    useOperatorSnapshot.mockReturnValue({
      data: makeOperatorSnapshot(),
      isError: false,
    });
  });

  it("passes URL search state to the typed query", () => {
    routeSearch = {
      q: "CR2",
      account_id: "123",
      severity: "critical",
      sort: "spend",
      direction: "asc",
      page: 3,
    };

    renderPage();

    expect(useOperatorAds).toHaveBeenCalledWith({
      search: "CR2",
      account_id: "123",
      severity: "critical",
      sort: "spend",
      direction: "asc",
      page: 3,
      page_size: 30,
    } satisfies OperatorAdsQuery);
  });

  it("uses a focus-managed sheet with typed cabinet options and resets page on filter changes", async () => {
    const trigger = renderPage().getByRole("button", {
      name: "Открыть фильтры объявлений",
    });
    fireEvent.click(trigger);

    const dialog = await screen.findByRole("dialog", {
      name: "Фильтры объявлений",
    });
    const close = within(dialog).getByRole("button", { name: "Закрыть" });
    await waitFor(() => expect(close).toHaveFocus());
    expect(close).toHaveClass("size-11", "touch-manipulation");
    const cabinet = within(dialog).getByLabelText("Кабинет");
    expect(cabinet).toHaveClass("min-h-11");
    expect(within(dialog).getByRole("option", { name: "GH_CR2" })).toHaveValue(
      "123",
    );

    fireEvent.change(cabinet, { target: { value: "456" } });
    const navigation = navigate.mock.calls.at(-1)?.[0] as {
      search: (previous: Record<string, unknown>) => Record<string, unknown>;
      replace: boolean;
    };
    expect(navigation.search({ page: 8, q: "old" })).toEqual({
      page: undefined,
      q: "old",
      account_id: "456",
    });

    fireEvent.click(close);
    await waitFor(() => expect(trigger).toHaveFocus());
  });
});
