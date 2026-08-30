import type { ComponentType } from "react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
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
  Link: ({ children }: { children: React.ReactNode }) => (
    <a href="#detail">{children}</a>
  ),
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

const useOperatorAdsList = vi.fn();
const useOperatorSnapshot = vi.fn();

vi.mock("@/lib/operatorApi", () => ({
  useOperatorAdsList: (...args: unknown[]) => useOperatorAdsList(...args),
  useOperatorSnapshot: (...args: unknown[]) => useOperatorSnapshot(...args),
  usePauseOperatorAd: () => ({ mutateAsync: vi.fn(), isPending: false }),
  useActivateOperatorAd: () => ({ mutateAsync: vi.fn(), isPending: false }),
  fetchOperatorAdForCommand: vi.fn(),
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

function adRow(
  fbAdId: string,
  percentToStop: string | null,
): OperatorAdsResponse["rows"][number] {
  return {
    id: `row-${fbAdId}`,
    fb_ad_id: fbAdId,
    name: `Объявление ${fbAdId}`,
    campaign_id: "campaign-1",
    campaign_name: "CR2 | GH",
    adset_id: "adset-1",
    adset_name: "CR2-adset-1",
    account_id: "act_123",
    delivery_status: "ACTIVE",
    data_state: "ready",
    severity: "warning",
    as_of: "2026-08-09T10:00:00Z",
    metrics: {
      spend: "12.50",
      impressions: 1000,
      clicks: 30,
      registrations: 3,
      ftd: 0,
      confirmed_deposits: 0,
      cpc: "0.41",
      cost_per_registration: "4.16",
      frequency: "1.84",
      cost_per_ftd: null,
    },
    rule_context:
      percentToStop === null
        ? {
            offer_code: null,
            rule_code: null,
            rule_title: null,
            value: null,
            threshold: null,
            percent_to_stop: null,
            stage: null,
          }
        : {
            offer_code: "GH_CR2",
            rule_code: "cpr_stop",
            rule_title: "Дорогая рега",
            value: "0.41",
            threshold: "0.48",
            percent_to_stop: percentToStop,
            stage: "warning",
          },
    active_action: null,
  };
}

function renderPage() {
  const client = new QueryClient({
    defaultOptions: { queries: { retry: false } },
  });
  return render(
    <QueryClientProvider client={client}>
      <OperatorRealtimeStatusProvider status="connected">
        <AdsPage />
      </OperatorRealtimeStatusProvider>
    </QueryClientProvider>,
  );
}

function setAdsQuery(
  pages: OperatorAdsResponse | OperatorAdsResponse[],
  options: {
    isPending?: boolean;
    error?: Error;
    hasNextPage?: boolean;
    isFetchingNextPage?: boolean;
    fetchNextPage?: () => void;
  } = {},
) {
  const pageArray = Array.isArray(pages) ? pages : [pages];
  useOperatorAdsList.mockReturnValue({
    data: { pages: pageArray },
    isPending: options.isPending ?? false,
    isFetching: false,
    isError: Boolean(options.error),
    error: options.error ?? null,
    hasNextPage: options.hasNextPage ?? false,
    isFetchingNextPage: options.isFetchingNextPage ?? false,
    fetchNextPage: options.fetchNextPage ?? vi.fn(),
    refetch: vi.fn(),
  });
}

describe("TMA operator ads URL filters", () => {
  beforeEach(() => {
    vi.clearAllMocks();
    routeSearch = {};
    setAdsQuery(emptyResponse());
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
    };

    renderPage();

    expect(useOperatorAdsList).toHaveBeenCalledWith({
      search: "CR2",
      account_id: "123",
      severity: "critical",
      sort: "spend",
      direction: "asc",
      page_size: 30,
    } satisfies Omit<OperatorAdsQuery, "page">);
  });

  it("prioritizes the ads closest to stop on the unfiltered landing", () => {
    renderPage();

    expect(useOperatorAdsList).toHaveBeenCalledWith(
      expect.objectContaining({
        sort: "percent_to_stop",
        direction: "desc",
      }) as unknown as OperatorAdsQuery,
    );

    fireEvent.click(
      screen.getByRole("button", { name: "Открыть фильтры объявлений" }),
    );
    expect(screen.getByLabelText("Сортировка")).toHaveValue("stop_proximity");
  });

  it("delegates stop proximity ranking to the server", () => {
    routeSearch = { sort: "stop_proximity" };
    setAdsQuery({
      ...emptyResponse(),
      state: "ready",
      rows: [
        adRow("far", "12.00"),
        adRow("unknown", null),
        adRow("near", "96.50"),
      ],
      total: 3,
      pages: 1,
    });

    renderPage();

    // Порядок считает БД: клиент видит только текущую страницу, и самое
    // опасное объявление может лежать на следующей.
    expect(useOperatorAdsList).toHaveBeenCalledWith(
      expect.objectContaining({
        sort: "percent_to_stop",
      }) as unknown as OperatorAdsQuery,
    );
    const names = screen
      .getAllByText(/Объявление (far|near|unknown)/)
      .map((node) => node.textContent);
    // Ответ сервера отрисовывается как есть, без переупорядочивания в браузере.
    expect(names).toEqual([
      "Объявление far",
      "Объявление unknown",
      "Объявление near",
    ]);
  });

  it("accumulates a second page below the first, in server order", () => {
    setAdsQuery([
      { ...emptyResponse(), state: "ready", rows: [adRow("111", "40.00")], total: 2, pages: 2 },
      {
        ...emptyResponse(),
        state: "ready",
        page: 2,
        rows: [adRow("222", "80.00")],
        total: 2,
        pages: 2,
      },
    ]);

    renderPage();

    const names = screen
      .getAllByText(/Объявление (111|222)/)
      .map((node) => node.textContent);
    expect(names).toEqual(["Объявление 111", "Объявление 222"]);
  });

  it("offers a show-more control that fetches the next page without touching the URL", async () => {
    const fetchNextPage = vi.fn();
    setAdsQuery(
      { ...emptyResponse(), state: "ready", rows: [adRow("111", "40.00")], total: 2, pages: 2 },
      { hasNextPage: true, fetchNextPage },
    );

    renderPage();
    fireEvent.click(screen.getByRole("button", { name: "Показать ещё" }));

    await waitFor(() => expect(fetchNextPage).toHaveBeenCalledOnce());
    expect(navigate).not.toHaveBeenCalled();
  });

  it("uses a focus-managed sheet with typed cabinet options and resets to a fresh query on filter changes", async () => {
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
    // Смена фильтра — не «страница», а новый ключ запроса: URL больше не
    // хранит номер страницы (issue #340), react-query сам отдаёт первую
    // порцию свежей выборки.
    expect(navigation.search({ q: "old" })).toEqual({
      q: "old",
      account_id: "456",
    });

    fireEvent.click(close);
    await waitFor(() => expect(trigger).toHaveFocus());
  });

  it("offers one-step recovery from an empty filtered result", () => {
    routeSearch = { q: "missing", severity: "critical" };
    renderPage();

    fireEvent.click(screen.getByRole("button", { name: "Сбросить фильтры" }));

    expect(navigate).toHaveBeenCalledWith({ search: {}, replace: true });
  });
});
