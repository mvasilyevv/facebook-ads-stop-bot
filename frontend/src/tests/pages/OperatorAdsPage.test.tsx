import type { ComponentType, ReactNode } from "react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { beforeEach, describe, expect, it, vi } from "vitest";

import type {
  OperatorAdRow,
  OperatorAdsQuery,
  OperatorAdsResponse,
} from "@fb/shared/operator/contracts";
import { makeOperatorScopeEvidence, makeOperatorSnapshot } from "@fb/shared/operator/testFixture";
import { OperatorRealtimeStatusProvider, type OperatorRealtimeStatus } from "@fb/operator-api";

const navigate = vi.fn();
let routeSearch: Record<string, unknown> = {};

vi.mock("@tanstack/react-router", () => ({
  createFileRoute: () => (options: { component: ComponentType }) => ({
    ...options,
    useSearch: () => routeSearch,
  }),
  useNavigate: () => navigate,
  Link: ({ children }: { children: ReactNode }) => <a href="#detail">{children}</a>,
}));

const useOperatorAds = vi.fn();
const useOperatorSnapshot = vi.fn();
const fetchOperatorAdForCommand = vi.fn();
const pauseMutate = vi.fn();
const activateMutate = vi.fn();
let pausePending = false;
let activatePending = false;

vi.mock("@/lib/api/operator", () => ({
  useOperatorAds: (...args: unknown[]) => useOperatorAds(...args),
  useOperatorSnapshot: (...args: unknown[]) => useOperatorSnapshot(...args),
  fetchOperatorAdForCommand: (...args: unknown[]) => fetchOperatorAdForCommand(...args),
  usePauseOperatorAd: () => ({ mutateAsync: pauseMutate, isPending: pausePending }),
  useActivateOperatorAd: () => ({ mutateAsync: activateMutate, isPending: activatePending }),
  operatorProblemMessage: (error: unknown) =>
    error instanceof Error ? error.message : "Неизвестная ошибка",
}));

const commandToast = vi.hoisted(() => ({
  success: vi.fn(),
  error: vi.fn(),
  info: vi.fn(),
  warning: vi.fn(),
}));

vi.mock("@/components/ui/Toast", () => ({
  toast: commandToast,
  ToastViewport: () => null,
}));

import { Route } from "@/routes/ads/index";

const AdsPage = (Route as unknown as { component: ComponentType }).component;

function makeAd(id: string, overrides: Partial<OperatorAdRow> = {}): OperatorAdRow {
  return {
    id: `row-${id}`,
    fb_ad_id: id,
    name: `Объявление ${id}`,
    campaign_id: `campaign-${id}`,
    campaign_name: `Кампания ${id}`,
    adset_id: `adset-${id}`,
    adset_name: `Адсет ${id}`,
    account_id: "act_1",
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

function response(rows: OperatorAdRow[] = [makeAd("111"), makeAd("222")]): OperatorAdsResponse {
  return {
    state: rows.length ? "ready" : "empty",
    as_of: "2026-07-19T10:00:00Z",
    freshness_seconds: 5,
    sources: ["meta"],
    issues: [],
    scope: makeOperatorScopeEvidence(),
    rows,
    page: 1,
    page_size: 50,
    total: rows.length,
    pages: rows.length ? 1 : 0,
  };
}

function setQuery(
  data: OperatorAdsResponse | undefined,
  options: { isPending?: boolean; error?: Error } = {},
) {
  currentAdsResponse = data;
  useOperatorAds.mockReturnValue({
    data,
    isPending: options.isPending ?? false,
    isFetching: false,
    isError: Boolean(options.error),
    error: options.error ?? null,
    refetch: vi.fn(),
  });
}

let currentAdsResponse: OperatorAdsResponse | undefined;

function renderPage(status: OperatorRealtimeStatus = "connected") {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(
    <QueryClientProvider client={client}>
      <OperatorRealtimeStatusProvider status={status}>
        <AdsPage />
      </OperatorRealtimeStatusProvider>
    </QueryClientProvider>,
  );
}

async function confirmRowCommand(
  user: ReturnType<typeof userEvent.setup>,
  label: "Отключить" | "Включить",
) {
  await user.click(screen.getAllByRole("button", { name: label })[0]!);
  const dialog = await screen.findByRole("dialog", { name: `${label} объявление?` });
  await user.click(within(dialog).getByRole("button", { name: label }));
}

describe("typed operator ads page", () => {
  beforeEach(() => {
    vi.clearAllMocks();
    routeSearch = {};
    pausePending = false;
    activatePending = false;
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
      const row = currentAdsResponse?.rows.find((candidate) => candidate.fb_ad_id === id);
      if (!row || !row.as_of || !row.delivery_status) throw new Error("row unavailable");
      return row;
    });
    setQuery(response());
    useOperatorSnapshot.mockReturnValue({
      data: makeOperatorSnapshot(),
      isError: false,
    });
  });

  it("passes server search, severity, sort and page without client-side overfetch", () => {
    routeSearch = {
      q: "CR2",
      account_id: "123",
      severity: "critical",
      sort: "spend",
      direction: "asc",
      page: 3,
    };
    setQuery({ ...response(), page: 3, pages: 5, total: 202 });
    renderPage();

    expect(useOperatorAds).toHaveBeenCalledWith({
      search: "CR2",
      account_id: "123",
      severity: "critical",
      sort: "spend",
      direction: "asc",
      page: 3,
      page_size: 50,
    } satisfies OperatorAdsQuery);
    expect(screen.getByText("Страница 3 из 5")).toBeInTheDocument();
  });

  it("shows the rule, its threshold and the distance to stop in the list", () => {
    renderPage();

    const tableRow = within(screen.getByRole("table")).getByText("Объявление 111").closest("tr")!;
    expect(within(tableRow).getByText("Подходит к стопу")).toBeInTheDocument();
    expect(within(tableRow).getByText("85.4%")).toBeInTheDocument();
    expect(within(tableRow).getByText("Дорогая рега · $0.41 из $0.48")).toBeInTheDocument();
  });

  it("keeps warning and stop distinguishable without colour", () => {
    setQuery(
      response([
        makeAd("111"),
        makeAd("222", {
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
      ]),
    );
    renderPage();

    const table = screen.getByRole("table");
    const warningRow = within(table).getByText("Объявление 111").closest("tr")!;
    const stopRow = within(table).getByText("Объявление 222").closest("tr")!;
    const warningBadge = within(warningRow)
      .getByText("Подходит к стопу")
      .closest(".operator-stop-proximity")!;
    const stopBadge = within(stopRow)
      .getByText("Порог пройден")
      .closest(".operator-stop-proximity")!;

    // Разными обязаны быть форма и текст, а не только тон: цвет может быть
    // недоступен в ч/б, forced-colors и при дальтонизме.
    expect(warningBadge.getAttribute("data-shape")).not.toBe(stopBadge.getAttribute("data-shape"));
    expect(warningBadge.getAttribute("data-stage")).toBe("warning");
    expect(stopBadge.getAttribute("data-stage")).toBe("stop");
    expect(warningBadge).toHaveTextContent("▲");
    expect(stopBadge).toHaveTextContent("■");
  });

  it("renders an unconfirmed rule context as a dash instead of zero percent", () => {
    setQuery(
      response([
        makeAd("111", {
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
      ]),
    );
    renderPage();

    const tableRow = within(screen.getByRole("table")).getByText("Объявление 111").closest("tr")!;
    expect(within(tableRow).getByText("Не подтверждено")).toBeInTheDocument();
    expect(tableRow).not.toHaveTextContent("0%");
    expect(tableRow).not.toHaveTextContent("В пределах порога");
  });

  it("marks an ad without a matched offer as unprotected by the rule", () => {
    setQuery(
      response([
        makeAd("111", {
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
      ]),
    );
    renderPage();

    const tableRow = within(screen.getByRole("table")).getByText("Объявление 111").closest("tr")!;
    expect(within(tableRow).getByText("Правило не применяется")).toBeInTheDocument();
    expect(
      within(tableRow).getByText(/авто-стоп его не остановит/, { exact: false }),
    ).toBeInTheDocument();
  });

  it("ranks rows by stop proximity without asking the server for an unsupported sort", () => {
    routeSearch = { sort: "stop_proximity" };
    setQuery(
      response([
        makeAd("111", {
          rule_context: { ...makeAd("111").rule_context, percent_to_stop: "41.00" },
        }),
        makeAd("222", {
          rule_context: { ...makeAd("222").rule_context, percent_to_stop: "97.10" },
        }),
        makeAd("333", {
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
      ]),
    );
    renderPage();

    // Порядок считает БД: клиент видит только текущую страницу, и самое
    // опасное объявление может лежать на следующей.
    expect(useOperatorAds).toHaveBeenCalledWith(
      expect.objectContaining({ sort: "percent_to_stop" }) as unknown as OperatorAdsQuery,
    );
    const names = within(screen.getByRole("table"))
      .getAllByRole("row")
      .slice(1)
      .map((row) => within(row).getByText(/Объявление \d+/).textContent);
    // Ответ сервера отрисовывается как есть, без переупорядочивания в браузере.
    expect(names).toEqual(["Объявление 111", "Объявление 222", "Объявление 333"]);
  });

  it("uses one row view-model for desktop and mobile without changing zero into unknown", () => {
    renderPage();

    expect(screen.getAllByText("Объявление 111").length).toBeGreaterThanOrEqual(2);
    const table = screen.getByRole("table");
    const firstRow = within(table).getAllByRole("row")[1];
    expect(firstRow).toHaveTextContent("0");
    expect(firstRow).toHaveTextContent("—");
  });

  it("hides ad money when a supposedly single scope is not USD", () => {
    const data = response([
      makeAd("111", {
        metrics: {
          ...makeAd("111").metrics,
          spend: "1.234",
        },
      }),
    ]);
    data.scope = {
      ...data.scope,
      currency: "KWD",
      currency_state: "single",
    };
    setQuery(data);

    renderPage();

    const firstRow = within(screen.getByRole("table")).getAllByRole("row")[1]!;
    expect(firstRow).not.toHaveTextContent(/KWD|1\.234/);
    expect(firstRow).toHaveTextContent("—");
  });

  it("submits an operator search through route state", async () => {
    const user = userEvent.setup();
    renderPage();

    await user.type(screen.getByRole("textbox", { name: "Поиск по объявлениям" }), "new ad");
    await user.click(screen.getByRole("button", { name: "Найти" }));

    expect(navigate).toHaveBeenCalledOnce();
    const navigation = navigate.mock.calls[0]?.[0] as {
      search: (previous: Record<string, unknown>) => Record<string, unknown>;
      replace: boolean;
    };
    expect(navigation.replace).toBe(true);
    expect(navigation.search({ page: 4 })).toEqual({ page: undefined, q: "new ad" });
  });

  it("queues pause once with an idempotency key and opens the exact task", async () => {
    const user = userEvent.setup();
    renderPage();

    await confirmRowCommand(user, "Отключить");

    await waitFor(() => expect(pauseMutate).toHaveBeenCalledOnce());
    const request = pauseMutate.mock.calls[0]?.[0] as {
      params: { path: { ad_id: string }; header: Record<string, string> };
    };
    expect(request.params.path.ad_id).toBe("111");
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

  it("never paints a queued pause as a completed success", async () => {
    const user = userEvent.setup();
    renderPage();

    await confirmRowCommand(user, "Отключить");

    // HTTP 202 = queued: команда принята, но объявление ещё тратит деньги.
    await waitFor(() => expect(commandToast.info).toHaveBeenCalledOnce());
    expect(commandToast.success).not.toHaveBeenCalled();
    expect(commandToast.info).toHaveBeenCalledWith(
      "#1842: В очереди",
      "Команда принята и ожидает выполнения.",
    );
  });

  it("paints only a confirmed command result green", async () => {
    pauseMutate.mockResolvedValue({
      task_id: 1842,
      public_id: "#1842",
      created: true,
      state: "confirmed",
    });
    const user = userEvent.setup();
    renderPage();

    await confirmRowCommand(user, "Отключить");

    await waitFor(() => expect(commandToast.success).toHaveBeenCalledOnce());
    expect(commandToast.success).toHaveBeenCalledWith(
      "#1842: Подтверждено",
      "Результат команды подтверждён.",
    );
    expect(commandToast.info).not.toHaveBeenCalled();
  });

  it("warns instead of celebrating when the command result is unknown", async () => {
    pauseMutate.mockResolvedValue({
      task_id: 1842,
      public_id: "#1842",
      created: false,
      state: "unknown",
    });
    const user = userEvent.setup();
    renderPage();

    await confirmRowCommand(user, "Отключить");

    await waitFor(() => expect(commandToast.warning).toHaveBeenCalledOnce());
    expect(commandToast.success).not.toHaveBeenCalled();
    expect(commandToast.warning.mock.calls[0]?.[1]).toContain(
      "Задача уже существует — не повторяйте команду.",
    );
  });

  it("offers activate only for confirmed inactive delivery", async () => {
    setQuery(response([makeAd("111", { delivery_status: "PAUSED" })]));
    const user = userEvent.setup();
    renderPage();

    await confirmRowCommand(user, "Включить");
    await waitFor(() => expect(activateMutate).toHaveBeenCalledOnce());
    expect(pauseMutate).not.toHaveBeenCalled();
  });

  it("marks resume-spend as a warning, not as a neutral utility", () => {
    setQuery(response([makeAd("111", { delivery_status: "PAUSED" })]));
    renderPage();

    // «Включить» возобновляет реальный спенд и не должно выглядеть как «Обновить».
    const resume = screen.getAllByRole("button", { name: "Включить" })[0]!;
    expect(resume.className).toContain("border-warning");
    expect(resume.className).toContain("bg-warning-bg");
    expect(resume.className).not.toContain("border-[var(--color-hairline-strong)]");
    expect(resume.className).not.toContain("border-danger");
  });

  it("cancels a confirmed intent when the fresh row no longer matches", async () => {
    fetchOperatorAdForCommand.mockResolvedValue(
      makeAd("111", { delivery_status: "PAUSED", as_of: "2026-07-19T10:00:01Z" }),
    );
    const user = userEvent.setup();
    renderPage();

    await confirmRowCommand(user, "Отключить");

    await waitFor(() => expect(fetchOperatorAdForCommand).toHaveBeenCalledOnce());
    expect(pauseMutate).not.toHaveBeenCalled();
  });

  it("fails closed for unknown delivery and exposes degraded data", () => {
    setQuery({
      ...response([makeAd("111", { delivery_status: null, data_state: "partial" })]),
      state: "partial",
      issues: [
        {
          code: "META_PARTIAL",
          title: "Meta ответила частично",
          detail: "Команды скрыты до сверки.",
          severity: "warning",
          correlation_id: "corr-partial",
        },
      ],
    });
    renderPage();

    expect(screen.getAllByText("Обновите данные перед действием").length).toBeGreaterThan(0);
    expect(screen.getByText("Meta ответила частично")).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: /Отключить|Включить/ })).not.toBeInTheDocument();
  });

  it("propagates an unavailable collection state and blocks ready-looking rows", () => {
    setQuery({
      ...response([makeAd("111", { data_state: "ready" })]),
      state: "unavailable",
    });
    renderPage();

    expect(screen.getAllByText("Источник недоступен").length).toBeGreaterThan(0);
    expect(screen.getAllByText("Обновите данные перед действием").length).toBeGreaterThan(0);
    expect(screen.queryByRole("button", { name: /Отключить|Включить/ })).not.toBeInTheDocument();
    const tableRow = within(screen.getByRole("table")).getByText("Объявление 111").closest("tr");
    expect(tableRow).not.toBeNull();
    // Четыре метрики плюс доля до стопа: кэшированные «85.4%» на строке без
    // подтверждённых данных выглядели бы как актуальная оценка риска.
    expect(within(tableRow as HTMLElement).getAllByText("—")).toHaveLength(5);
    expect(within(tableRow as HTMLElement).getByText("Не подтверждено")).toBeInTheDocument();
    expect(tableRow).not.toHaveTextContent("12,50");
  });

  it("neutralizes stale health and blocks money commands until refresh", () => {
    setQuery(response([makeAd("111", { data_state: "stale", severity: "ok" })]));
    renderPage();

    expect(screen.getAllByText("Неизвестно").length).toBeGreaterThan(0);
    expect(screen.getAllByText("Обновите данные перед действием").length).toBeGreaterThan(0);
    expect(screen.queryByRole("button", { name: /Отключить|Включить/ })).not.toBeInTheDocument();
  });

  it("blocks money commands while realtime snapshot reconciliation is pending", () => {
    renderPage("reconnecting");

    const messages = screen.getAllByText("Действие недоступно до сверки live-снимка");
    expect(messages.length).toBeGreaterThan(0);
    const mobileCard = screen
      .getAllByText("Объявление 111")
      .map((node) => node.closest("article"))
      .find((node): node is HTMLElement => node !== null);
    expect(mobileCard).toBeDefined();
    const mobileMessage = within(mobileCard as HTMLElement).getByText(
      "Действие недоступно до сверки live-снимка",
    );
    expect(mobileMessage).toHaveClass("w-full", "min-w-0", "whitespace-normal", "break-words");
    expect(mobileMessage.parentElement).toHaveClass("min-w-0", "flex-col", "items-start");
    expect(screen.queryByRole("button", { name: /Отключить|Включить/ })).not.toBeInTheDocument();
  });

  it("does not render a cached empty page as a confirmed zero while reconnecting", () => {
    setQuery(response([]));
    renderPage("reconnecting");

    expect(screen.getByText("Список не подтверждён")).toBeInTheDocument();
    expect(screen.getByText("— строк")).toBeInTheDocument();
    expect(screen.queryByText("Объявлений не найдено")).not.toBeInTheDocument();
  });

  it("renders an ambiguous active action explicitly instead of calling it queued", () => {
    setQuery(
      response([
        makeAd("111", {
          active_action: {
            id: "1842",
            public_id: "#1842",
            kind: "pause",
            state: "unknown",
            title: "Отключение объявления",
            target_label: "Объявление 111",
            requested_at: "2026-07-19T10:00:00Z",
            updated_at: "2026-07-19T10:00:01Z",
            requested_by: "operator:web",
            reason: "Ответ Meta неоднозначен",
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
    renderPage();

    expect(screen.getAllByText("#1842 · результат не подтверждён")).not.toHaveLength(0);
    expect(screen.queryByText("#1842 · в очереди")).not.toBeInTheDocument();
  });

  it("renders confirmed empty, loading and unavailable states explicitly", () => {
    setQuery(response([]));
    const { rerender } = renderPage();
    expect(screen.getByText("Объявлений не найдено")).toBeInTheDocument();

    setQuery(undefined, { isPending: true });
    const loadingClient = new QueryClient();
    rerender(
      <QueryClientProvider client={loadingClient}>
        <AdsPage />
      </QueryClientProvider>,
    );
    expect(screen.getByRole("status", { name: "Загрузка объявлений" })).toBeInTheDocument();

    setQuery(undefined, { error: new Error("Каталог недоступен") });
    rerender(
      <QueryClientProvider client={loadingClient}>
        <AdsPage />
      </QueryClientProvider>,
    );
    expect(screen.getByRole("alert")).toHaveTextContent("Каталог недоступен");
  });
});
