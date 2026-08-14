import type { ComponentType, ReactNode } from "react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import type { OperatorAdRow, OperatorAdsResponse } from "@fb/shared/operator/contracts";
import { makeOperatorScopeEvidence } from "@fb/shared/operator/testFixture";
import { OperatorRealtimeStatusProvider, type OperatorRealtimeStatus } from "@fb/operator-api";

import { useToastStore } from "@/components/ui/toastStore";

const navigate = vi.fn();
let fbAdId = "120211984573_8761";
const originalLocalStorage = Object.getOwnPropertyDescriptor(globalThis, "localStorage");

vi.mock("@tanstack/react-router", () => ({
  createFileRoute: () => (options: { component: ComponentType }) => ({
    ...options,
    useParams: () => ({ fbAdId }),
  }),
  useNavigate: () => navigate,
  Link: ({ children }: { children: ReactNode }) => <a href="#route">{children}</a>,
}));

const useOperatorAds = vi.fn();
const fetchOperatorAdForCommand = vi.fn();
const pauseMutate = vi.fn();
const activateMutate = vi.fn();

vi.mock("@/lib/api/operator", () => ({
  useOperatorAds: (...args: unknown[]) => useOperatorAds(...args),
  fetchOperatorAdForCommand: (...args: unknown[]) => fetchOperatorAdForCommand(...args),
  usePauseOperatorAd: () => ({ mutateAsync: pauseMutate, isPending: false }),
  useActivateOperatorAd: () => ({ mutateAsync: activateMutate, isPending: false }),
  operatorProblemMessage: (error: unknown) =>
    error instanceof Error ? error.message : "Неизвестная ошибка",
}));

import { Route } from "@/routes/ads/$fbAdId";

const AdDetail = (Route as unknown as { component: ComponentType }).component;

function makeAd(overrides: Partial<OperatorAdRow> = {}): OperatorAdRow {
  return {
    id: "operator-row-1",
    fb_ad_id: fbAdId,
    name: "UA17 | SP | MV | Krov | 24.03",
    campaign_id: "campaign-1",
    campaign_name: "GH_CR | 18.06",
    adset_id: "adset-1",
    adset_name: "adset-android",
    account_id: "act_1",
    delivery_status: "ACTIVE",
    data_state: "ready",
    severity: "critical",
    as_of: "2026-07-19T10:00:00Z",
    metrics: {
      spend: "891.23",
      impressions: 10_000,
      clicks: 21,
      registrations: 7,
      ftd: 5,
      confirmed_deposits: 0,
      cpc: "42.10",
      cost_per_registration: null,
      frequency: null,
      cost_per_ftd: null,
    },    rule_context: {
      offer_code: "GH_CR2",
      rule_code: "cpr_stop",
      rule_title: "Цена регистрации",
      value: "0.41",
      threshold: "0.48",
      percent_to_stop: "0.854",
      stage: "warning",
    },
    active_action: null,
    ...overrides,
  };
}

function response(rows: OperatorAdRow[] = [makeAd()]): OperatorAdsResponse {
  return {
    state: rows.length ? "ready" : "empty",
    as_of: "2026-07-19T10:00:00Z",
    freshness_seconds: 5,
    sources: ["meta", "tracker"],
    issues: [],
    scope: makeOperatorScopeEvidence(),
    rows,
    page: 1,
    page_size: 10,
    total: rows.length,
    pages: rows.length ? 1 : 0,
  };
}

function mockAds(
  data: OperatorAdsResponse | undefined,
  options: { pending?: boolean; error?: Error } = {},
) {
  useOperatorAds.mockReturnValue({
    data,
    isPending: options.pending ?? false,
    isError: Boolean(options.error),
    error: options.error ?? null,
    refetch: vi.fn(),
  });
}

function renderDetail(status: OperatorRealtimeStatus = "connected") {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(
    <QueryClientProvider client={client}>
      <OperatorRealtimeStatusProvider status={status}>
        <AdDetail />
      </OperatorRealtimeStatusProvider>
    </QueryClientProvider>,
  );
}

async function confirmAdCommand(
  user: ReturnType<typeof userEvent.setup>,
  label: "Отключить" | "Включить" = "Отключить",
) {
  await user.click(screen.getByRole("button", { name: label }));
  const dialog = await screen.findByRole("dialog", { name: `${label} объявление?` });
  await user.click(within(dialog).getByRole("button", { name: label }));
}

describe("typed operator ad detail", () => {
  beforeEach(() => {
    vi.clearAllMocks();
    globalThis.localStorage.clear();
    useToastStore.setState({ toasts: [] });
    fbAdId = "120211984573_8761";
    mockAds(response());
    pauseMutate.mockResolvedValue({ task_id: 1842, public_id: "#1842", created: true });
    activateMutate.mockResolvedValue({ task_id: 1843, public_id: "#1843", created: true });
    fetchOperatorAdForCommand.mockImplementation(async (_client: unknown, id: string) =>
      makeAd({ fb_ad_id: id }),
    );
  });

  afterEach(() => {
    vi.restoreAllMocks();
    if (originalLocalStorage) {
      Object.defineProperty(globalThis, "localStorage", originalLocalStorage);
    } else {
      Reflect.deleteProperty(globalThis, "localStorage");
    }
  });

  it("renders exact typed identity, hierarchy and cabinet-day context", () => {
    renderDetail();

    expect(screen.getByRole("heading", { level: 1 })).toHaveTextContent(
      "UA17 | SP | MV | Krov | 24.03",
    );
    expect(screen.getByText("GH_CR | 18.06")).toBeInTheDocument();
    expect(screen.getByText("adset-android")).toBeInTheDocument();
    expect(screen.getByText("Часовой пояс").parentElement).toHaveTextContent("Europe/Kaliningrad");
  });

  it("preserves confirmed zero and unknown in the same metric grid", () => {
    renderDetail();

    expect(screen.getByText("Депозиты").parentElement).toHaveTextContent("0");
    expect(screen.getByText("Цена регистрации").parentElement).toHaveTextContent("—");
  });

  it("keeps timezone evidence but hides money for a non-USD scope", () => {
    const data = response([
      makeAd({
        metrics: {
          ...makeAd().metrics,
          spend: "1.234",
          cpc: "0.001",
        },
      }),
    ]);
    data.scope = {
      ...data.scope,
      currency: "KWD",
      currency_state: "single",
      cabinet_timezone: "Asia/Tokyo",
      cabinet_timezone_state: "single",
      display_timezone: "Europe/Moscow",
    };
    mockAds(data);

    renderDetail();

    expect(screen.getByText("Расход").parentElement).toHaveTextContent("—");
    expect(screen.getByText("CPC").parentElement).toHaveTextContent("—");
    expect(document.body).not.toHaveTextContent(/KWD|1\.234|0\.001/);
    expect(screen.getByText("Часовой пояс").parentElement).toHaveTextContent("Asia/Tokyo");
    expect(screen.getByText("Данные на").parentElement).toHaveTextContent("19.07.2026, 19:00");
  });

  it("renders mixed ad scope explicitly and hides unlabeled money", () => {
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
    const data = {
      ...response([row]),
      state: "partial" as const,
      scope: {
        ...makeOperatorScopeEvidence(),
        cabinet_timezone: null,
        cabinet_timezone_state: "mixed" as const,
        currency: null,
        currency_state: "mixed" as const,
        display_timezone: "Europe/Moscow",
      },
    };
    mockAds(data);

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

  it("queues pause with explicit idempotency and navigates to the lifecycle", async () => {
    const user = userEvent.setup();
    renderDetail();

    await confirmAdCommand(user);

    await waitFor(() => expect(pauseMutate).toHaveBeenCalledOnce());
    const request = pauseMutate.mock.calls[0]?.[0] as {
      params: { path: { ad_id: string }; header: Record<string, string> };
    };
    expect(request.params.path.ad_id).toBe(fbAdId);
    expect(request.params.header["Idempotency-Key"]).toMatch(/^[0-9a-f-]{36}$/i);
    expect(request.params.header["X-Operator-Principal"]).toBe("operator:web");
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
    fbAdId = "ambiguous-response-ad";
    mockAds(response());
    pauseMutate
      .mockRejectedValueOnce(new TypeError("network response lost"))
      .mockResolvedValueOnce({ task_id: 1842, public_id: "#1842", created: false });
    const user = userEvent.setup();
    renderDetail();

    await confirmAdCommand(user);
    await waitFor(() => expect(pauseMutate).toHaveBeenCalledTimes(1));
    const retryDialog = await screen.findByRole("dialog", { name: "Отключить объявление?" });
    const retryButton = within(retryDialog).getByRole("button", { name: "Отключить" });
    await waitFor(() => expect(retryButton).not.toBeDisabled());
    await user.click(retryButton);
    await waitFor(() => expect(pauseMutate).toHaveBeenCalledTimes(2));

    const first = pauseMutate.mock.calls[0]?.[0] as {
      params: { header: Record<string, string> };
    };
    const retry = pauseMutate.mock.calls[1]?.[0] as {
      params: { header: Record<string, string> };
    };
    expect(retry.params.header["Idempotency-Key"]).toBe(first.params.header["Idempotency-Key"]);
  });

  it("fails closed and explains how to recover when durable intent storage is unavailable", async () => {
    installLocalStorage(
      makeStorage({
        getItem: () => {
          throw new Error("storage blocked");
        },
      }),
    );
    const user = userEvent.setup();
    renderDetail();

    await confirmAdCommand(user);

    await waitFor(() => expect(fetchOperatorAdForCommand).toHaveBeenCalledOnce());
    expect(pauseMutate).not.toHaveBeenCalled();
    await waitFor(() => {
      const message = useToastStore.getState().toasts.at(-1);
      expect(message).toMatchObject({
        title: "Отключить не удалось",
        variant: "error",
      });
      expect(String(message?.description)).toContain("Безопасное действие заблокировано");
      expect(String(message?.description)).toContain("Перезагрузите приложение");
    });
  });

  it("opens the committed lifecycle and warns against retry when intent cleanup fails", async () => {
    fbAdId = "cleanup-failure-ad";
    mockAds(response());
    installLocalStorage(makeStorage({ removeItem: () => undefined }));
    const user = userEvent.setup();
    renderDetail();

    await confirmAdCommand(user);

    await waitFor(() => expect(pauseMutate).toHaveBeenCalledOnce());
    await waitFor(() =>
      expect(navigate).toHaveBeenCalledWith({
        to: "/actions/$actionId",
        params: { actionId: "1842" },
      }),
    );
    const warning = useToastStore
      .getState()
      .toasts.find((message) => message.title === "#1842: ключ защиты не очищен");
    expect(String(warning?.description)).toContain("Задача уже создана — не повторяйте команду");
  });

  it("does not issue a money command when confirmation is rejected", async () => {
    const user = userEvent.setup();
    renderDetail();

    await user.click(screen.getByRole("button", { name: "Отключить" }));
    const dialog = await screen.findByRole("dialog", { name: "Отключить объявление?" });
    await user.click(within(dialog).getByRole("button", { name: "Отмена" }));

    expect(screen.queryByRole("dialog", { name: "Отключить объявление?" })).not.toBeInTheDocument();
    expect(pauseMutate).not.toHaveBeenCalled();
  });

  it("does not issue a money command when the post-confirmation row changed", async () => {
    fetchOperatorAdForCommand.mockResolvedValue(
      makeAd({ delivery_status: "PAUSED", as_of: "2026-07-19T10:00:01Z" }),
    );
    const user = userEvent.setup();
    renderDetail();

    await confirmAdCommand(user);

    await waitFor(() => expect(fetchOperatorAdForCommand).toHaveBeenCalledOnce());
    expect(pauseMutate).not.toHaveBeenCalled();
  });

  it("shows an existing task instead of a second command", () => {
    mockAds(
      response([
        makeAd({
          active_action: {
            id: "1842",
            public_id: "#1842",
            kind: "pause",
            state: "running",
            title: "Отключение объявления",
            target_label: "UA17",
            requested_at: "2026-07-19T10:00:00Z",
            updated_at: "2026-07-19T10:00:01Z",
            requested_by: "operator:web",
            reason: null,
            correlation_id: "corr-1842",
            account_id: "act_1",
            currency: "USD",
            cabinet_timezone: "Europe/Kaliningrad",
            account_context_observed_at: "2026-07-19T10:00:00Z",
            account_context_issues: [],
          },
        }),
      ]),
    );
    renderDetail();

    expect(screen.getByText("#1842 · выполняется")).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: /Отключить|Включить/ })).not.toBeInTheDocument();
  });

  it("keeps a confirmed command blocked while fresh delivery data is pending", () => {
    mockAds(
      response([
        makeAd({
          active_action: {
            id: "1842",
            public_id: "#1842",
            kind: "pause",
            state: "confirmed",
            title: "Отключение объявления",
            target_label: "UA17",
            requested_at: "2026-07-19T10:00:00Z",
            updated_at: "2026-07-19T10:00:01Z",
            requested_by: "operator:web",
            reason: null,
            correlation_id: "corr-1842",
            account_id: "act_1",
            currency: "USD",
            cabinet_timezone: "Europe/Kaliningrad",
            account_context_observed_at: "2026-07-19T10:00:00Z",
            account_context_issues: [],
          },
        }),
      ]),
    );
    renderDetail();

    expect(screen.getByText("#1842 · Подтверждено · сверяем данные")).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: /Отключить|Включить/ })).not.toBeInTheDocument();
  });

  it("does not relabel an unknown task result as queued", () => {
    mockAds(
      response([
        makeAd({
          active_action: {
            id: "1842",
            public_id: "#1842",
            kind: "pause",
            state: "unknown",
            title: "Отключение объявления",
            target_label: "UA17",
            requested_at: "2026-07-19T10:00:00Z",
            updated_at: "2026-07-19T10:00:01Z",
            requested_by: "operator:web",
            reason: "Требуется сверка",
            correlation_id: "corr-1842",
            account_id: "act_1",
            currency: "USD",
            cabinet_timezone: "Europe/Kaliningrad",
            account_context_observed_at: "2026-07-19T10:00:00Z",
            account_context_issues: [],
          },
        }),
      ]),
    );
    renderDetail();

    expect(screen.getByText("#1842 · результат не подтверждён")).toBeInTheDocument();
    expect(screen.queryByText("#1842 · в очереди")).not.toBeInTheDocument();
  });

  it("fails closed for unknown delivery status", () => {
    mockAds(response([makeAd({ delivery_status: null, data_state: "partial" })]));
    renderDetail();

    expect(screen.getByText("Обновите данные перед действием")).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: /Отключить|Включить/ })).not.toBeInTheDocument();
  });

  it("blocks money commands until the live snapshot is reconciled", () => {
    renderDetail("reconnecting");

    expect(screen.getByText("Действие недоступно до сверки live-снимка")).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: /Отключить|Включить/ })).not.toBeInTheDocument();
  });

  it("renders loading, unavailable and confirmed-not-found distinctly", () => {
    mockAds(undefined, { pending: true });
    const { rerender } = renderDetail();
    expect(screen.getByRole("status", { name: "Загрузка объявления" })).toBeInTheDocument();

    mockAds(undefined, { error: new Error("Карточка недоступна") });
    const client = new QueryClient();
    rerender(
      <QueryClientProvider client={client}>
        <AdDetail />
      </QueryClientProvider>,
    );
    expect(screen.getByRole("alert")).toHaveTextContent("Карточка недоступна");

    mockAds(response([]));
    rerender(
      <QueryClientProvider client={client}>
        <OperatorRealtimeStatusProvider status="connected">
          <AdDetail />
        </OperatorRealtimeStatusProvider>
      </QueryClientProvider>,
    );
    expect(screen.getByText("Объявление не найдено")).toBeInTheDocument();
  });
});

function installLocalStorage(storage: Storage): void {
  Object.defineProperty(globalThis, "localStorage", {
    value: storage,
    configurable: true,
    writable: true,
  });
}

function makeStorage(overrides: Partial<Storage> = {}): Storage {
  const values = new Map<string, string>();
  return {
    get length() {
      return values.size;
    },
    clear: () => values.clear(),
    getItem: (key) => values.get(key) ?? null,
    key: (index) => [...values.keys()][index] ?? null,
    removeItem: (key) => void values.delete(key),
    setItem: (key, value) => void values.set(key, String(value)),
    ...overrides,
  };
}
