import type { ComponentType, ReactNode } from "react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { beforeEach, describe, expect, it, vi } from "vitest";

import { OperatorRealtimeStatusProvider } from "@fb/operator-api";
import type { OperatorAdRow, OperatorAdsResponse } from "@fb/shared/operator/contracts";
import { makeOperatorScopeEvidence, makeOperatorSnapshot } from "@fb/shared/operator/testFixture";

const navigate = vi.fn();

vi.mock("@tanstack/react-router", () => ({
  createFileRoute: () => (options: { component: ComponentType }) => options,
  useNavigate: () => navigate,
  Link: ({
    children,
    to,
    params,
    search: _search,
    ...props
  }: {
    children: ReactNode;
    to: string;
    params?: Record<string, string>;
    search?: unknown;
  }) => (
    <a
      href={Object.entries(params ?? {}).reduce(
        (href, [key, value]) => href.replace(`$${key}`, value),
        to,
      )}
      {...props}
    >
      {children}
    </a>
  ),
}));

const tgConfirm = vi.fn();
const tgAlert = vi.fn();

vi.mock("@/lib/tg", () => ({
  haptic: { impact: vi.fn(), notify: vi.fn(), selection: vi.fn() },
  tgConfirm: (...args: unknown[]) => tgConfirm(...args),
  tgAlert: (...args: unknown[]) => tgAlert(...args),
}));

const mockUseOperatorCabinetSnapshot = vi.fn();
const mockScan = vi.fn();
const useOperatorAdsList = vi.fn();
const fetchOperatorAdForCommand = vi.fn();
const pauseMutate = vi.fn();
const activateMutate = vi.fn();

vi.mock("@/lib/operatorApi", () => ({
  useOperatorCabinetSnapshot: (...args: unknown[]) => mockUseOperatorCabinetSnapshot(...args),
  useOperatorRetryScan: () => ({ mutateAsync: mockScan, isPending: false }),
  useOperatorAdsList: (...args: unknown[]) => useOperatorAdsList(...args),
  fetchOperatorAdForCommand: (...args: unknown[]) => fetchOperatorAdForCommand(...args),
  usePauseOperatorAd: () => ({ mutateAsync: pauseMutate, isPending: false }),
  useActivateOperatorAd: () => ({ mutateAsync: activateMutate, isPending: false }),
  operatorProblemMessage: (error: unknown) => (error instanceof Error ? error.message : "Ошибка"),
}));

import { OperatorMiniCabinetDashboard } from "@/features/operator/OperatorMiniDashboard";

function makeAd(id: string, overrides: Partial<OperatorAdRow> = {}): OperatorAdRow {
  return {
    id: `row-${id}`,
    fb_ad_id: id,
    name: `Объявление ${id}`,
    campaign_id: `campaign-${id}`,
    campaign_name: `Кампания ${id}`,
    adset_id: `adset-${id}`,
    adset_name: `Адсет ${id}`,
    account_id: "123",
    delivery_status: "ACTIVE",
    data_state: "ready",
    severity: "ok",
    as_of: "2026-07-19T10:00:00Z",
    metrics: {
      spend: "12.50",
      impressions: 100,
      clicks: 0,
      registrations: null,
      ftd: 0,
      confirmed_deposits: 0,
      cpc: null,
      cost_per_registration: null,
      frequency: "1.84",
      cost_per_ftd: null,
    },
    rule_context: {
      offer_code: "GH_CR2",
      rule_code: "cpr_stop",
      rule_title: "Дорогая рега",
      value: "0.41",
      threshold: "0.48",
      percent_to_stop: "85.41",
      stage: "warning",
    },
    active_action: null,
    ...overrides,
  };
}

function adsResponse(rows: OperatorAdRow[] = [makeAd("111")]): OperatorAdsResponse {
  return {
    state: rows.length ? "ready" : "empty",
    as_of: "2026-07-19T10:00:00Z",
    freshness_seconds: 5,
    sources: ["meta"],
    issues: [],
    scope: makeOperatorScopeEvidence(),
    rows,
    page: 1,
    page_size: 20,
    total: rows.length,
    pages: rows.length ? 1 : 0,
  };
}

let currentAdsRows: OperatorAdRow[] = [];

function setAdsQuery(
  pages: OperatorAdsResponse | OperatorAdsResponse[] | undefined,
  options: {
    isPending?: boolean;
    isError?: boolean;
    error?: Error;
    hasNextPage?: boolean;
    fetchNextPage?: () => void;
  } = {},
) {
  const pageArray = pages === undefined ? undefined : Array.isArray(pages) ? pages : [pages];
  currentAdsRows = pageArray?.flatMap((page) => page.rows) ?? [];
  useOperatorAdsList.mockReturnValue({
    data: pageArray ? { pages: pageArray } : undefined,
    isPending: options.isPending ?? false,
    isError: options.isError ?? Boolean(options.error),
    error: options.error ?? null,
    hasNextPage: options.hasNextPage ?? false,
    isFetchingNextPage: false,
    fetchNextPage: options.fetchNextPage ?? vi.fn(),
    refetch: vi.fn(),
  });
}

function renderCabinet(cabinetId = "123") {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(
    <QueryClientProvider client={client}>
      <OperatorRealtimeStatusProvider status="connected">
        <OperatorMiniCabinetDashboard cabinetId={cabinetId} />
      </OperatorRealtimeStatusProvider>
    </QueryClientProvider>,
  );
}

describe("TMA operator cabinet page", () => {
  beforeEach(() => {
    vi.clearAllMocks();
    mockUseOperatorCabinetSnapshot.mockReturnValue({
      data: makeOperatorSnapshot(),
      isLoading: false,
      isError: false,
      error: null,
      refetch: vi.fn(),
    });
    setAdsQuery(adsResponse());
    pauseMutate.mockResolvedValue({
      task_id: 1842,
      public_id: "#1842",
      created: true,
      state: "queued",
    });
    activateMutate.mockResolvedValue({
      task_id: 1843,
      public_id: "#1843",
      created: true,
      state: "queued",
    });
    fetchOperatorAdForCommand.mockImplementation(async (_client: unknown, id: string) => {
      const row = currentAdsRows.find((candidate) => candidate.fb_ad_id === id);
      if (!row || !row.as_of || !row.delivery_status) throw new Error("row unavailable");
      return row;
    });
    tgConfirm.mockResolvedValue(true);
  });

  it("uses the selected cabinet timezone and drops the portfolio/funnel sections", () => {
    renderCabinet();

    expect(screen.getAllByText(/USD · Africa\/Accra/).length).toBeGreaterThan(0);
    expect(screen.getAllByText(/18\.07\.2026, 10:1[45]/).length).toBeGreaterThan(0);
    expect(mockUseOperatorCabinetSnapshot).toHaveBeenCalledWith("123", { window: "today" });
    expect(screen.queryByRole("heading", { name: "Портфель" })).not.toBeInTheDocument();
    expect(screen.queryByRole("heading", { name: "Воронка" })).not.toBeInTheDocument();
  });

  it("keeps cabinet timestamps unconfirmed when timezone evidence is missing", () => {
    const snapshot = makeOperatorSnapshot();
    snapshot.portfolio.data!.currency_groups[0]!.cabinets[0]!.timezone = null;
    snapshot.meta.cabinet_timezone = null;
    snapshot.meta.cabinet_timezone_known = false;
    snapshot.meta.cabinet_timezone_state = "unknown";
    mockUseOperatorCabinetSnapshot.mockReturnValue({
      data: snapshot,
      isLoading: false,
      isError: false,
      error: null,
      refetch: vi.fn(),
    });

    renderCabinet();

    expect(screen.getByRole("heading", { level: 1 }).parentElement).toHaveTextContent(
      "часовой пояс не подтверждён",
    );
    expect(screen.getAllByText(/as_of\s+не подтверждено/).length).toBeGreaterThan(0);
    expect(screen.queryByText(/18\.07\.2026, 12:1[45]/)).not.toBeInTheDocument();
  });

  it("shows the cabinet's spend/base/stop line with a server-confirmed overage note", () => {
    renderCabinet();

    const section = screen.getByRole("heading", { name: "Бюджет кабинета" }).closest("section")!;
    expect(within(section).getByText("Расход")).toBeInTheDocument();
    expect(within(section).getByText("$18.40")).toBeInTheDocument();
    expect(within(section).getAllByText("Stop превышен").length).toBeGreaterThan(0);
    expect(within(section).getByText("Факт $18.40 ≥ stop $18.00")).toBeInTheDocument();
  });

  it("shows the recovery banner for an incident that belongs to this cabinet", async () => {
    const snapshot = makeOperatorSnapshot();
    snapshot.attention.data!.items[0] = {
      ...snapshot.attention.data!.items[0]!,
      id: "login-incident-1",
      severity: "critical",
      title: "В Facebook нужно войти снова",
      summary: "Кабинет: 123",
      occurred_at: "2026-07-18T10:14:00Z",
      recovery_action: "retry_scan",
    };
    mockUseOperatorCabinetSnapshot.mockReturnValue({
      data: snapshot,
      isLoading: false,
      isError: false,
      error: null,
      refetch: vi.fn(),
    });
    mockScan.mockResolvedValue({
      task_id: 1900,
      public_id: "#1900",
      state: "queued",
      created: true,
      correlation_id: "corr-scan",
    });

    renderCabinet();
    // Раньше баннер был безусловно выключен на странице кабинета
    // (`cabinetId ? null : ...`), хотя снимок уже сужен до этого кабинета.
    const button = screen.getByRole("button", { name: /Повторить скан/ });
    await userEvent.click(button);

    expect(mockScan).toHaveBeenCalledOnce();
  });

  it("renders this cabinet's ads with a preset account_id filter, reusing the shared ads card", () => {
    setAdsQuery(adsResponse([makeAd("111"), makeAd("222")]));
    renderCabinet();

    expect(useOperatorAdsList).toHaveBeenCalledWith(
      expect.objectContaining({ account_id: "123" }),
    );
    expect(screen.getAllByText("Объявление 111").length).toBeGreaterThan(0);
    expect(screen.getAllByText("Объявление 222").length).toBeGreaterThan(0);
  });

  it("queues pause from the cabinet page through the same tgConfirm command path as /ads", async () => {
    setAdsQuery(adsResponse([makeAd("111")]));
    const user = userEvent.setup();
    renderCabinet();

    await user.click(screen.getAllByRole("button", { name: /Отключить/ })[0]!);

    await waitFor(() => expect(tgConfirm).toHaveBeenCalledOnce());
    await waitFor(() => expect(pauseMutate).toHaveBeenCalledOnce());
    const request = pauseMutate.mock.calls[0]?.[0] as {
      params: { path: { ad_id: string }; header: Record<string, string> };
      body: { expected_delivery_status: string; expected_as_of: string };
    };
    expect(request.params.path.ad_id).toBe("111");
    expect(request.params.header["Idempotency-Key"]).toMatch(/^[0-9a-f-]{36}$/i);
    expect(request.params.header["X-Operator-Principal"]).toBe("operator:tma");
    expect(request.body).toEqual({
      expected_delivery_status: "ACTIVE",
      expected_as_of: "2026-07-19T10:00:00Z",
    });
    await waitFor(() =>
      expect(navigate).toHaveBeenCalledWith({
        to: "/actions/$actionId",
        params: { actionId: "1842" },
      }),
    );
  });

  it("shows a confirmed empty cabinet ads list without inventing a zero", () => {
    setAdsQuery(adsResponse([]));
    renderCabinet();

    expect(screen.getByText("В кабинете нет объявлений")).toBeInTheDocument();
  });
});
