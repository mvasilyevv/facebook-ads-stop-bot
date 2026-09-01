import type { ComponentType, ReactNode } from "react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import type {
  OperatorAdRow,
  OperatorAdsResponse,
} from "@fb/shared/operator/contracts";
import { makeOperatorScopeEvidence } from "@fb/shared/operator/testFixture";
import {
  OperatorRealtimeStatusProvider,
  type OperatorRealtimeStatus,
} from "@fb/operator-api";

let mockFbAdId = "ad_stop_001";
const navigate = vi.fn();

vi.mock("@tanstack/react-router", () => ({
  createFileRoute: () => (options: { component: ComponentType }) => ({
    ...options,
    useParams: () => ({ fbAdId: mockFbAdId }),
  }),
  useNavigate: () => navigate,
  Link: ({ children }: { children: ReactNode }) => <span>{children}</span>,
}));

const tgConfirm = vi.fn();
const tgAlert = vi.fn();

vi.mock("@/lib/tg", () => ({
  haptic: { impact: vi.fn(), notify: vi.fn(), selection: vi.fn() },
  tgConfirm: (...args: unknown[]) => tgConfirm(...args),
  tgAlert: (...args: unknown[]) => tgAlert(...args),
}));

const pauseMutate = vi.fn();
const activateMutate = vi.fn();
const fetchOperatorAdForCommand = vi.fn();
let pausePending = false;
let activatePending = false;
let adsData: ReturnType<typeof operatorAdsResponse> | undefined;
let adsLoading = false;
let adsError: Error | null = null;

vi.mock("@/lib/operatorApi", () => ({
  useOperatorAds: () => ({
    data: adsData,
    isPending: adsLoading,
    isError: adsError !== null,
    error: adsError,
    refetch: vi.fn(),
  }),
  usePauseOperatorAd: () => ({
    mutateAsync: pauseMutate,
    isPending: pausePending,
  }),
  useActivateOperatorAd: () => ({
    mutateAsync: activateMutate,
    isPending: activatePending,
  }),
  fetchOperatorAdForCommand: (...args: unknown[]) =>
    fetchOperatorAdForCommand(...args),
  operatorProblemMessage: (error: unknown) =>
    error instanceof Error ? error.message : "Неизвестная ошибка",
}));

import { MiniAdDetail } from "@/features/operator/OperatorAdDetail";
import { MiniOperatorAdCard } from "@/features/operator/OperatorAds";

const AdDetail = () => <MiniAdDetail fbAdId={mockFbAdId} />;

function makeAd(overrides: Partial<OperatorAdRow> = {}): OperatorAdRow {
  return {
    id: "row-1",
    fb_ad_id: "ad_stop_001",
    name: "CR2 | GH | Stop Test",
    campaign_id: "campaign-1",
    campaign_name: "CR2 | GH | MV | 07.06",
    adset_id: "adset-1",
    adset_name: "CR2-adset-1",
    account_id: "act_123456",
    delivery_status: "ACTIVE",
    data_state: "ready",
    severity: "warning",
    as_of: "2026-07-19T10:00:00Z",
    metrics: {
      spend: "150.50",
      impressions: 1_000,
      clicks: 12,
      registrations: 0,
      ftd: null,
      confirmed_deposits: 0,
      cpc: "1.20",
      cost_per_registration: null,
      frequency: "1.8412",
      cost_per_ftd: "30.10",
    },
    // Доля до стопа приходит в процентных единицах: 0.41 из 0.48 — это 85.41%.
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

function operatorAdsResponse(
  row: OperatorAdRow = makeAd(),
): OperatorAdsResponse {
  return {
    state: "ready" as const,
    as_of: row.as_of,
    freshness_seconds: 5,
    sources: ["meta"],
    issues: [],
    scope: makeOperatorScopeEvidence(),
    rows: [row],
    page: 1,
    page_size: 10,
    total: 1,
    pages: 1,
  };
}

function actionAccountContext() {
  return {
    account_id: "act_123456",
    currency: "USD",
    cabinet_timezone: "Europe/Kaliningrad",
    account_context_observed_at: "2026-07-19T09:59:00Z",
    account_context_issues: [],
  };
}

function renderDetail(status: OperatorRealtimeStatus = "connected") {
  const client = new QueryClient({
    defaultOptions: { queries: { retry: false } },
  });
  return render(
    <QueryClientProvider client={client}>
      <OperatorRealtimeStatusProvider status={status}>
        <AdDetail />
      </OperatorRealtimeStatusProvider>
    </QueryClientProvider>,
  );
}

function renderMobileCard(status: OperatorRealtimeStatus = "connected") {
  const client = new QueryClient({
    defaultOptions: { queries: { retry: false } },
  });
  return render(
    <QueryClientProvider client={client}>
      <OperatorRealtimeStatusProvider status={status}>
        <MiniOperatorAdCard ad={makeAd()} currency="USD" />
      </OperatorRealtimeStatusProvider>
    </QueryClientProvider>,
  );
}

describe("TMA typed operator ad detail", () => {
  beforeEach(() => {
    vi.clearAllMocks();
    globalThis.localStorage.clear();
    mockFbAdId = "ad_stop_001";
    adsData = operatorAdsResponse();
    adsLoading = false;
    adsError = null;
    pausePending = false;
    activatePending = false;
    tgConfirm.mockResolvedValue(true);
    tgAlert.mockResolvedValue(undefined);
    pauseMutate.mockResolvedValue({
      task_id: 1842,
      public_id: "#1842",
      manual_review_available: false,
      created: true,
    });
    activateMutate.mockResolvedValue({
      task_id: 1843,
      public_id: "#1843",
      manual_review_available: false,
      created: true,
    });
    fetchOperatorAdForCommand.mockImplementation(
      async (_client: unknown, id: string) => {
        const row = adsData?.rows.find(
          (candidate) => candidate.fb_ad_id === id,
        );
        if (!row || !row.as_of || !row.delivery_status) {
          throw new Error("row unavailable");
        }
        return row;
      },
    );
  });

  afterEach(() => {
    vi.restoreAllMocks();
  });

  it("renders the typed row, cabinet context and exact zero/unknown semantics", () => {
    renderDetail();

    expect(screen.getByRole("heading", { level: 1 })).toHaveTextContent(
      "CR2 | GH | Stop Test",
    );
    expect(screen.getByText("Внимание")).toBeInTheDocument();
    expect(screen.getByText("Регистрации").parentElement).toHaveTextContent(
      "0",
    );
    expect(screen.getByText("FTD").parentElement).toHaveTextContent("—");
    expect(screen.getByText("Часовой пояс").parentElement).toHaveTextContent(
      "Europe/Kaliningrad",
    );
  });

  it("shows the rule, its threshold and the distance to stop on the card", () => {
    renderDetail();

    const section = screen.getByRole("region", { name: "До стопа" });
    expect(within(section).getByText("Подходит к стопу")).toBeInTheDocument();
    expect(within(section).getByText("85.4%")).toBeInTheDocument();
    expect(
      within(section).getByText("Дорогая рега · $0.41 из $0.48"),
    ).toBeInTheDocument();
  });

  it("keeps warning and stop distinguishable without colour", () => {
    renderDetail();
    const warningBadge = screen
      .getByText("Подходит к стопу")
      .closest(".operator-stop-proximity")!;

    adsData = operatorAdsResponse(
      makeAd({
        rule_context: {
          offer_code: "GH_CR2",
          rule_code: "cpr_stop",
          rule_title: "Дорогая рега",
          value: "0.52",
          threshold: "0.48",
          percent_to_stop: "108.33",
          stage: "stop",
        },
      }),
    );
    renderDetail();
    const stopBadge = screen
      .getByText("Порог пройден")
      .closest(".operator-stop-proximity")!;

    expect(warningBadge.getAttribute("data-shape")).not.toBe(
      stopBadge.getAttribute("data-shape"),
    );
    expect(warningBadge).toHaveTextContent("▲");
    expect(stopBadge).toHaveTextContent("■");
  });

  it("renders an unconfirmed rule context as a dash instead of zero percent", () => {
    adsData = operatorAdsResponse(
      makeAd({
        rule_context: {
          offer_code: null,
          rule_code: null,
          rule_title: null,
          value: null,
          threshold: null,
          percent_to_stop: null,
          stage: null,
        },
      }),
    );

    renderDetail();

    const section = screen.getByRole("region", { name: "До стопа" });
    expect(within(section).getByText("Не подтверждено")).toBeInTheDocument();
    expect(within(section).getByText("—")).toBeInTheDocument();
    expect(section).not.toHaveTextContent("0%");
  });

  it("says explicitly that an unmatched ad is not protected by the rule", () => {
    adsData = operatorAdsResponse(
      makeAd({
        rule_context: {
          offer_code: null,
          rule_code: null,
          rule_title: null,
          value: null,
          threshold: null,
          percent_to_stop: null,
          stage: "none",
        },
      }),
    );

    renderDetail();

    const section = screen.getByRole("region", { name: "До стопа" });
    expect(
      within(section).getByText("Правило не применяется"),
    ).toBeInTheDocument();
    expect(section).toHaveTextContent("авто-стоп его не остановит");
  });

  it("shows frequency and deposit cost with the same unknown semantics", () => {
    renderDetail();

    expect(screen.getByText("Частота").parentElement).toHaveTextContent("1.84");
    expect(screen.getByText("Цена деп.").parentElement).toHaveTextContent(
      "$30.10",
    );

    adsData = operatorAdsResponse(
      makeAd({
        metrics: { ...makeAd().metrics, frequency: null, cost_per_ftd: null },
      }),
    );
    renderDetail();

    expect(screen.getAllByText("Частота")[1]!.parentElement).toHaveTextContent(
      "—",
    );
    expect(
      screen.getAllByText("Цена деп.")[1]!.parentElement,
    ).toHaveTextContent("—");
  });

  it("keeps timezone evidence but hides money for a non-USD scope", () => {
    const data = operatorAdsResponse(
      makeAd({
        metrics: {
          ...makeAd().metrics,
          spend: "1.234",
          cpc: "0.001",
        },
      }),
    );
    data.scope = {
      ...data.scope,
      currency: "KWD",
      currency_state: "single",
      cabinet_timezone: "Asia/Tokyo",
      cabinet_timezone_state: "single",
      display_timezone: "Europe/Moscow",
    };
    adsData = data;

    renderDetail();

    expect(screen.getByText("Расход").parentElement).toHaveTextContent("—");
    expect(screen.getByText("CPC").parentElement).toHaveTextContent("—");
    expect(document.body).not.toHaveTextContent(/KWD|1\.234|0\.001/);
    expect(screen.getByText("Часовой пояс").parentElement).toHaveTextContent(
      "Asia/Tokyo",
    );
    expect(screen.getByText("Данные на").parentElement).toHaveTextContent(
      "19.07.2026, 19:00",
    );
  });

  it("renders mixed scope explicitly and never invents UTC or a currency", () => {
    const row = makeAd({
      data_state: "partial",
      metrics: {
        ...makeAd().metrics,
        spend: null,
        cpc: null,
        cost_per_registration: null,
        frequency: null,
        cost_per_ftd: null,
      },
    });
    adsData = {
      ...operatorAdsResponse(row),
      state: "partial",
      scope: {
        ...makeOperatorScopeEvidence(),
        cabinet_timezone: null,
        cabinet_timezone_state: "mixed",
        currency: null,
        currency_state: "mixed",
        display_timezone: "Europe/Moscow",
      },
    };

    renderDetail();

    expect(screen.getByText("Часовой пояс").parentElement).toHaveTextContent(
      "Несколько часовых поясов · границы по каждому кабинету",
    );
    expect(screen.getByText("Часовой пояс").parentElement).toHaveTextContent(
      "отображение Europe/Moscow",
    );
    expect(screen.getByText("Расход").parentElement).toHaveTextContent("—");
    expect(screen.queryByText("UTC")).not.toBeInTheDocument();
  });

  it("queues pause with a dedicated idempotency header and opens its lifecycle", async () => {
    const user = userEvent.setup();
    renderDetail();

    await user.click(screen.getByRole("button", { name: "Отключить" }));

    await waitFor(() =>
      expect(tgConfirm).toHaveBeenCalledWith(
        expect.stringContaining("Результат будет подтверждён"),
      ),
    );
    await waitFor(() => expect(pauseMutate).toHaveBeenCalledOnce());
    const request = pauseMutate.mock.calls[0]?.[0] as {
      params: { path: { ad_id: string }; header: Record<string, string> };
    };
    expect(request.params.path.ad_id).toBe("ad_stop_001");
    expect(request.params.header["Idempotency-Key"]).toMatch(
      /^[0-9a-f-]{36}$/i,
    );
    expect(request.params.header["X-Operator-Principal"]).toBe("operator:tma");
    expect(request).toMatchObject({
      body: {
        expected_delivery_status: "ACTIVE",
        expected_as_of: "2026-07-19T10:00:00Z",
      },
    });
    await waitFor(() =>
      expect(navigate).toHaveBeenCalledWith({
        to: "/actions/$actionId",
        params: { actionId: "1842" },
      }),
    );
  });

  it("reuses the same intent key after an ambiguous network failure", async () => {
    mockFbAdId = "ambiguous-tma-ad";
    adsData = operatorAdsResponse(makeAd({ fb_ad_id: mockFbAdId }));
    pauseMutate
      .mockRejectedValueOnce(new TypeError("network response lost"))
      .mockResolvedValueOnce({
        task_id: 1842,
        public_id: "#1842",
        manual_review_available: false,
        created: false,
      });
    const user = userEvent.setup();
    renderDetail();

    await user.click(screen.getByRole("button", { name: "Отключить" }));
    await waitFor(() => expect(pauseMutate).toHaveBeenCalledTimes(1));
    await user.click(screen.getByRole("button", { name: "Отключить" }));
    await waitFor(() => expect(pauseMutate).toHaveBeenCalledTimes(2));

    const first = pauseMutate.mock.calls[0]?.[0] as {
      params: { header: Record<string, string> };
    };
    const retry = pauseMutate.mock.calls[1]?.[0] as {
      params: { header: Record<string, string> };
    };
    expect(retry.params.header["Idempotency-Key"]).toBe(
      first.params.header["Idempotency-Key"],
    );
  });

  it("fails closed and shows durable-storage recovery before sending a money command", async () => {
    vi.spyOn(globalThis.localStorage, "getItem").mockImplementation(() => {
      throw new Error("storage blocked");
    });
    const user = userEvent.setup();
    renderDetail();

    await user.click(screen.getByRole("button", { name: "Отключить" }));

    await waitFor(() =>
      expect(fetchOperatorAdForCommand).toHaveBeenCalledOnce(),
    );
    expect(pauseMutate).not.toHaveBeenCalled();
    await waitFor(() => {
      expect(tgAlert).toHaveBeenCalledWith(
        expect.stringContaining("Безопасное действие заблокировано"),
      );
      expect(tgAlert).toHaveBeenCalledWith(
        expect.stringContaining("Перезагрузите приложение"),
      );
    });
  });

  it("opens the committed lifecycle and warns against retry when intent cleanup fails", async () => {
    vi.spyOn(globalThis.localStorage, "removeItem").mockImplementation(
      () => undefined,
    );
    const user = userEvent.setup();
    renderDetail();

    await user.click(screen.getByRole("button", { name: "Отключить" }));

    await waitFor(() => expect(pauseMutate).toHaveBeenCalledOnce());
    expect(tgAlert).toHaveBeenCalledWith(
      expect.stringContaining(
        "#1842: задача уже создана — не повторяйте команду",
      ),
    );
    await waitFor(() =>
      expect(navigate).toHaveBeenCalledWith({
        to: "/actions/$actionId",
        params: { actionId: "1842" },
      }),
    );
  });

  it("does not queue a money action after confirmation is cancelled", async () => {
    tgConfirm.mockResolvedValue(false);
    const user = userEvent.setup();
    renderDetail();

    await user.click(screen.getByRole("button", { name: "Отключить" }));
    await waitFor(() => expect(tgConfirm).toHaveBeenCalledOnce());
    expect(pauseMutate).not.toHaveBeenCalled();
  });

  it("does not open a pointless confirm when the ad drifted before confirmation", async () => {
    // Сверка происходит ДО tgConfirm: оператор не подтверждает команду, чтобы
    // затем получить отказ на разошедшийся снимок.
    fetchOperatorAdForCommand.mockResolvedValue(
      makeAd({ delivery_status: "PAUSED", as_of: "2026-07-19T10:00:01Z" }),
    );
    const user = userEvent.setup();
    renderDetail();

    await user.click(screen.getByRole("button", { name: "Отключить" }));

    await waitFor(() =>
      expect(fetchOperatorAdForCommand).toHaveBeenCalledOnce(),
    );
    expect(tgConfirm).not.toHaveBeenCalled();
    expect(pauseMutate).not.toHaveBeenCalled();
    expect(tgAlert).toHaveBeenCalledWith(
      expect.stringContaining("Обновите данные перед действием"),
    );
  });

  it("shows activate for a confirmed inactive delivery state", async () => {
    adsData = operatorAdsResponse(makeAd({ delivery_status: "PAUSED" }));
    const user = userEvent.setup();
    renderDetail();

    await user.click(screen.getByRole("button", { name: "Включить" }));
    await waitFor(() => expect(activateMutate).toHaveBeenCalledOnce());
  });

  it("fails closed when delivery state is unknown", () => {
    adsData = operatorAdsResponse(makeAd({ delivery_status: null }));
    renderDetail();

    expect(screen.getByText("Статус доставки неизвестен")).toBeInTheDocument();
    expect(
      screen.queryByRole("button", { name: /Отключить|Включить/ }),
    ).not.toBeInTheDocument();
  });

  it("shows moderation rejection explicitly and blocks a status command", () => {
    adsData = operatorAdsResponse(
      makeAd({ delivery_status: "DISAPPROVED", severity: "critical" }),
    );
    renderDetail();

    expect(screen.getByText("Доставка").parentElement).toHaveTextContent(
      "Отклонено модерацией",
    );
    expect(
      screen.getByText(
        "Исправьте объявление или запросите повторную проверку в Ads Manager",
      ),
    ).toBeInTheDocument();
    expect(
      screen.queryByRole("button", { name: /Отключить|Включить/ }),
    ).not.toBeInTheDocument();
  });

  it("neutralizes stale health and blocks money commands until refresh", () => {
    adsData = operatorAdsResponse(
      makeAd({ data_state: "stale", severity: "ok" }),
    );
    renderDetail();

    expect(screen.getByText("Неизвестно")).toBeInTheDocument();
    expect(
      screen.getByText("Обновите данные перед действием"),
    ).toBeInTheDocument();
    expect(
      screen.queryByRole("button", { name: /Отключить|Включить/ }),
    ).not.toBeInTheDocument();
  });

  it("hides cached metrics and delivery when the row is unavailable", () => {
    adsData = {
      ...operatorAdsResponse(
        makeAd({
          data_state: "unavailable",
          delivery_status: "ACTIVE",
          metrics: {
            spend: "0",
            impressions: 0,
            clicks: 0,
            registrations: 0,
            ftd: 0,
            confirmed_deposits: 0,
            cpc: "0",
            cost_per_registration: "0",
            frequency: null,
            cost_per_ftd: null,
          },
        }),
      ),
      state: "unavailable",
      as_of: null,
      freshness_seconds: null,
    };

    renderDetail();

    for (const label of [
      "Расход",
      "Показы",
      "Клики",
      "Регистрации",
      "FTD",
      "Депозиты",
      "CPC",
      "Цена рег.",
    ]) {
      expect(screen.getByText(label).parentElement).toHaveTextContent("—");
    }
    expect(screen.getByText("Доставка").parentElement).toHaveTextContent(
      "Статус не подтверждён",
    );
  });

  it("blocks money commands while realtime snapshot reconciliation is pending", () => {
    renderDetail("reconnecting");

    expect(
      screen.getByText("Действие недоступно до сверки live-снимка"),
    ).toBeInTheDocument();
    expect(
      screen.queryByRole("button", { name: /Отключить|Включить/ }),
    ).not.toBeInTheDocument();
  });

  it("keeps the full reconciliation message readable in the mobile ad card", () => {
    const { container } = renderMobileCard("reconnecting");

    const message = screen.getByText(
      "Действие недоступно до сверки live-снимка",
    );
    expect(container).toHaveTextContent(
      "Действие недоступно до сверки live-снимка",
    );
    expect(message).toHaveClass(
      "w-full",
      "min-w-0",
      "whitespace-normal",
      "break-words",
    );
    expect(message.parentElement).toHaveClass(
      "min-w-0",
      "flex-col",
      "items-start",
    );
  });

  it("links an in-flight command to the exact lifecycle", () => {
    adsData = operatorAdsResponse(
      makeAd({
        active_action: {
          id: "1842",
          public_id: "#1842",
          manual_review_available: false,
          kind: "pause",
          state: "running",
          title: "Отключение объявления",
          target_label: "CR2 | GH | Stop Test",
          requested_at: "2026-07-19T10:00:00Z",
          updated_at: "2026-07-19T10:00:01Z",
          requested_by: "operator:tma",
          reason: null,
          correlation_id: "corr-1842",
          ...actionAccountContext(),
        },
      }),
    );
    renderDetail();

    expect(screen.getByText("#1842 · выполняется")).toBeInTheDocument();
    expect(
      screen.queryByRole("button", { name: /Отключить|Включить/ }),
    ).not.toBeInTheDocument();
  });

  it("keeps a confirmed command blocked while fresh delivery data is pending", () => {
    adsData = operatorAdsResponse(
      makeAd({
        active_action: {
          id: "1842",
          public_id: "#1842",
          manual_review_available: false,
          kind: "pause",
          state: "confirmed",
          title: "Отключение объявления",
          target_label: "CR2 | GH | Stop Test",
          requested_at: "2026-07-19T10:00:00Z",
          updated_at: "2026-07-19T10:00:01Z",
          requested_by: "operator:tma",
          reason: null,
          correlation_id: "corr-1842",
          ...actionAccountContext(),
        },
      }),
    );
    renderDetail();

    expect(
      screen.getByText("#1842 · Подтверждено · сверяем данные"),
    ).toBeInTheDocument();
    expect(
      screen.queryByRole("button", { name: /Отключить|Включить/ }),
    ).not.toBeInTheDocument();
  });

  it("shows an ambiguous command result as unconfirmed", () => {
    adsData = operatorAdsResponse(
      makeAd({
        active_action: {
          id: "1842",
          public_id: "#1842",
          manual_review_available: false,
          kind: "pause",
          state: "unknown",
          title: "Отключение объявления",
          target_label: "CR2 | GH | Stop Test",
          requested_at: "2026-07-19T10:00:00Z",
          updated_at: "2026-07-19T10:00:01Z",
          requested_by: "operator:tma",
          reason: "Требуется сверка",
          correlation_id: "corr-1842",
          ...actionAccountContext(),
        },
      }),
    );
    renderDetail();

    expect(
      screen.getByText("#1842 · результат не подтверждён"),
    ).toBeInTheDocument();
    expect(screen.queryByText("#1842 · в очереди")).not.toBeInTheDocument();
  });

  it("renders loading and explicit error states", () => {
    adsData = undefined;
    adsLoading = true;
    const { rerender } = renderDetail();
    expect(
      screen.getByRole("status", { name: "Загрузка объявления" }),
    ).toBeInTheDocument();

    adsLoading = false;
    adsError = new Error("Ошибка сети");
    const client = new QueryClient();
    rerender(
      <QueryClientProvider client={client}>
        <AdDetail />
      </QueryClientProvider>,
    );
    expect(screen.getByText("Ошибка сети")).toBeInTheDocument();
  });
});
