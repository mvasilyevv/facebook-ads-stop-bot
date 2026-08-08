import type { ComponentType } from "react";
import { fireEvent, render, screen, within } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";
import type {
  AnalyticsDaypart,
  AnalyticsLiveBudgetSeries,
  AnalyticsPerformance,
} from "@fb/shared";
import { makeOperatorScopeEvidence } from "@fb/shared/operator/testFixture";

vi.mock("@tanstack/react-router", () => ({
  createFileRoute: () => (options: { component: unknown }) => ({
    ...options,
    component: options.component,
  }),
  Link: ({ children }: { children: React.ReactNode }) => children,
  useNavigate: () => vi.fn(),
}));

const mockRealtimeStatus = vi.fn(() => "connected");
vi.mock("@fb/operator-api", () => ({
  useOperatorRealtimeStatus: () => mockRealtimeStatus(),
}));

vi.mock("@/lib/tg", () => ({
  haptic: { selection: vi.fn(), impact: vi.fn(), notify: vi.fn() },
  tgAlert: vi.fn(),
}));

const mockUsePerformance = vi.fn();
const mockUseLiveBudget = vi.fn();
const mockUseEvents = vi.fn();
const mockUseDaypart = vi.fn();
vi.mock("@/features/analytics/api", () => ({
  useTmaAnalyticsPerformance: (...args: unknown[]) =>
    mockUsePerformance(...args),
  useTmaAnalyticsLiveBudget: (...args: unknown[]) => mockUseLiveBudget(...args),
  useTmaAnalyticsEvents: (...args: unknown[]) => mockUseEvents(...args),
  useTmaAnalyticsDaypart: (...args: unknown[]) => mockUseDaypart(...args),
}));

vi.mock("@/lib/operatorApi", () => ({
  operatorProblemMessage: (error: unknown) =>
    error instanceof Error ? error.message : "Аналитика недоступна",
}));

import { Route } from "@/routes/analytics/index";

const AnalyticsPage = (Route as unknown as { component: ComponentType })
  .component;

const performanceRefetch = vi.fn();
const liveBudgetRefetch = vi.fn();
const eventsRefetch = vi.fn();
const daypartRefetch = vi.fn();

describe("mobile performance analytics", () => {
  beforeEach(() => {
    vi.clearAllMocks();
    mockRealtimeStatus.mockReturnValue("connected");
    mockUsePerformance.mockReturnValue(
      queryResult(makePerformanceFixture(), performanceRefetch),
    );
    mockUseLiveBudget.mockReturnValue(
      queryResult(makeLiveBudgetFixture(), liveBudgetRefetch),
    );
    mockUseEvents.mockReturnValue(
      queryResult(makeEventsFixture(), eventsRefetch),
    );
    mockUseDaypart.mockReturnValue(
      queryResult(makeDaypartFixture(), daypartRefetch),
    );
  });

  it("renders source evidence and responsive campaign cards without a desktop table", () => {
    render(<AnalyticsPage />);

    expect(screen.getByText("Качество источников")).toBeInTheDocument();
    expect(screen.getAllByText(/Последнее событие:/)).toHaveLength(2);
    expect(screen.getAllByText(/Снимок · 42 сек назад/).length).toBeGreaterThan(
      0,
    );
    expect(
      screen.getByRole("heading", { name: "Campaign Alpha" }),
    ).toBeInTheDocument();
    expect(
      screen.getByRole("heading", { name: "Campaign Beta" }),
    ).toBeInTheDocument();

    const cards = screen.getByTestId("performance-cards");
    expect(within(cards).queryByRole("table")).not.toBeInTheDocument();
    expect(within(cards).getAllByRole("article")).toHaveLength(2);
  });

  it("renders live thresholds, the funnel and the typed event feed", () => {
    render(<AnalyticsPage />);

    expect(
      screen.getByRole("heading", { name: "Накопительный расход" }),
    ).toBeInTheDocument();
    expect(
      screen.getByRole("button", { name: /Факт USD.*184\.20/ }),
    ).toBeInTheDocument();
    expect(screen.getAllByText("Воронка").length).toBeGreaterThanOrEqual(2);
    const funnel = screen.getByRole("list", { name: "Этапы воронки" });
    expect(
      within(funnel).getByText(/CR — · стоимость USD.*0\.30/),
    ).toBeInTheDocument();
    expect(
      within(funnel).getByText(/CR 11\.94% · стоимость USD.*2\.49/),
    ).toBeInTheDocument();
    expect(
      within(funnel).getByText(/CR 14\.86% · стоимость USD.*16\.75/),
    ).toBeInTheDocument();
    expect(
      screen.getByText("GH_CR2: meta_api_mutation · выполнено"),
    ).toBeInTheDocument();
    expect(screen.getByText("Открыть")).toBeInTheDocument();
  });

  it("keeps every analytics control at least 44px and exposes semantic labels", () => {
    render(<AnalyticsPage />);

    for (const button of screen.getAllByRole("button")) {
      expect(button.className).toMatch(/min-h-(?:11|\[44px\])|size-11/);
    }
    for (const field of [
      screen.getByLabelText("Поиск в аналитике"),
      screen.getByLabelText("Кабинет"),
      screen.getByLabelText("Оффер"),
      screen.getByLabelText("Кампания"),
    ]) {
      expect(field.className).toContain("min-h-[44px]");
    }
    for (const summary of document.querySelectorAll("summary")) {
      if (summary.className) {
        expect(summary.className).toContain("min-h-11");
      } else {
        // Shared AccessibleChartFrame owns this 44px rule in
        // `.operator-chart-table summary`.
        expect(summary.closest(".operator-chart-table")).not.toBeNull();
      }
    }
    expect(
      screen.getByRole("img", {
        name: /Пунктиром отмечены часы без подтверждённых данных/,
      }),
    ).toBeInTheDocument();
  });

  it("keeps period, search and account filters action-first and typed", () => {
    render(<AnalyticsPage />);

    const today = screen.getByRole("button", { name: "Сегодня" });
    expect(today).toHaveAttribute("aria-pressed", "true");
    expect(today).toHaveClass("min-h-11");

    fireEvent.click(screen.getByRole("button", { name: "7 дней" }));
    expect(mockUsePerformance).toHaveBeenLastCalledWith(
      expect.objectContaining({
        period: "7d",
        level: "campaign",
        page: 1,
        page_size: 20,
      }),
    );

    fireEvent.change(screen.getByLabelText("Поиск в аналитике"), {
      target: { value: "alpha" },
    });
    expect(mockUsePerformance).toHaveBeenLastCalledWith(
      expect.objectContaining({ search: "alpha" }),
    );

    fireEvent.change(screen.getByLabelText("Кабинет"), {
      target: { value: "act_1" },
    });
    expect(mockUsePerformance).toHaveBeenLastCalledWith(
      expect.objectContaining({
        account_id: "act_1",
        offer_id: undefined,
        campaign_id: undefined,
      }),
    );
  });

  it("renders selected-day x24 bars with explicit unknown gaps and an accessible table", () => {
    render(<AnalyticsPage />);

    expect(screen.getByRole("button", { name: "Понедельник" })).toHaveAttribute(
      "aria-pressed",
      "true",
    );
    const unknownHour = document.querySelector('[data-unknown-hour="5"]');
    expect(unknownHour).toBeInTheDocument();
    expect(unknownHour).toHaveAttribute("stroke", "var(--color-bg-8)");
    const hourInspector = screen.getByRole("slider", {
      name: "Час для просмотра",
    });
    expect(hourInspector).toHaveClass("min-h-11");
    expect(hourInspector).toHaveAttribute("aria-valuetext", "00:00 · FTD: 0");
    fireEvent.change(hourInspector, { target: { value: "5" } });
    expect(hourInspector).toHaveValue("5");
    expect(hourInspector).toHaveAttribute(
      "aria-valuetext",
      "05:00 · FTD: неизвестно",
    );
    expect(screen.getByTestId("daypart-hour-inspection")).toHaveTextContent(
      "05:00 · FTD: неизвестно",
    );
    expect(
      document.querySelector('[data-selected-hour="5"]'),
    ).toBeInTheDocument();

    const daypartSection = screen
      .getByRole("heading", { name: "Когда трафик конвертит" })
      .closest("section");
    expect(daypartSection).not.toBeNull();
    const disclosure = within(daypartSection as HTMLElement).getByText(
      "Данные графика",
    );
    const details = disclosure.closest("details");
    expect(details).not.toBeNull();
    const table = within(details as HTMLElement).getByRole("table");
    expect(within(table).getAllByRole("row")).toHaveLength(25);
    const confirmedZeroRow = within(table).getByText("00:00").closest("tr");
    const unknownRow = within(table).getByText("05:00").closest("tr");
    expect(confirmedZeroRow).not.toBeNull();
    expect(unknownRow).not.toBeNull();
    expect(
      within(confirmedZeroRow as HTMLElement).getAllByText("0"),
    ).toHaveLength(3);
    expect(within(unknownRow as HTMLElement).getAllByText("—")).toHaveLength(3);

    fireEvent.click(screen.getByRole("button", { name: "Вторник" }));
    expect(screen.getByRole("button", { name: "Вторник" })).toHaveAttribute(
      "aria-pressed",
      "true",
    );
    expect(screen.getByText(/Вторник · 24 часа/)).toBeInTheDocument();

    fireEvent.click(screen.getByRole("button", { name: "Среда" }));
    expect(screen.getByRole("button", { name: "Среда" })).toHaveAttribute(
      "aria-pressed",
      "true",
    );
    expect(document.querySelectorAll("[data-unknown-hour]")).toHaveLength(24);
  });

  it("does not label raw source statuses current when daypart evidence is stale", () => {
    const stale = makeDaypartFixture();
    stale.state = "stale";
    stale.issues = ["Почасовой снимок устарел"];
    mockUseDaypart.mockReturnValue(queryResult(stale, daypartRefetch));

    render(<AnalyticsPage />);

    const daypartFigure = screen
      .getByText("Понедельник · 24 часа")
      .closest("figure");
    expect(daypartFigure).not.toBeNull();
    expect(daypartFigure).toHaveTextContent("Meta — снимок устарел");
    expect(daypartFigure).toHaveTextContent("AdSet.pro — снимок устарел");
    expect(daypartFigure).not.toHaveTextContent("Meta — актуально");
  });

  it("labels mixed cabinet timezones explicitly without rendering null", () => {
    const mixed = makePerformanceFixture();
    mixed.scope = {
      ...mixed.scope,
      cabinet_timezone: null,
      cabinet_timezone_state: "mixed",
    };
    mixed.window = {
      ...mixed.window,
      timezone: null,
      timezone_known: true,
      timezone_state: "mixed",
    };
    mixed.rows[0] = {
      ...mixed.rows[0]!,
      cabinet_timezone: null,
      timezone_known: false,
      timezone_state: "mixed",
    };
    mockUsePerformance.mockReturnValue(queryResult(mixed, performanceRefetch));

    render(<AnalyticsPage />);

    expect(
      screen.getAllByText(
        /Несколько часовых поясов · границы по каждому кабинету/,
      ).length,
    ).toBeGreaterThan(0);
    expect(document.body).not.toHaveTextContent(/\bnull\b/i);
  });

  it("keeps three-decimal funnel cost exact for KWD", () => {
    const kwd = makePerformanceFixture();
    kwd.scope = {
      ...kwd.scope,
      currency: "KWD",
      currency_state: "single",
    };
    kwd.totals = {
      ...kwd.totals,
      spend: "1.234",
      clicks: 1,
      registrations: 1,
      ftds: 1,
      confirmed_deposits: 1,
    };
    mockUsePerformance.mockReturnValue(queryResult(kwd, performanceRefetch));

    render(<AnalyticsPage />);

    const funnel = screen
      .getByRole("heading", { name: "Воронка", level: 2 })
      .closest("section");
    expect(funnel).not.toBeNull();
    expect(funnel).toHaveTextContent(/KWD.*1\.234/);
    expect(funnel).not.toHaveTextContent("KWD 1.230");
  });

  it("shows partial and stale evidence instead of a green/current projection", () => {
    const partial = makePerformanceFixture();
    partial.state = "partial";
    partial.issues = ["Tracker lag exceeds the selected freshness window"];
    partial.window.timezone_known = false;
    partial.total_live_budget = {
      ...partial.total_live_budget!,
      stop_delta: "12.00",
    };
    partial.rows = partial.rows.map((row) => ({
      ...row,
      live_budget: row.live_budget
        ? { ...row.live_budget, stop_delta: "12.00" }
        : null,
    }));
    mockUsePerformance.mockReturnValue(
      queryResult(partial, performanceRefetch),
    );

    const { rerender } = render(<AnalyticsPage />);
    expect(screen.getAllByText("Частично").length).toBeGreaterThan(0);
    expect(screen.getByText(/Tracker lag exceeds/)).toBeInTheDocument();
    expect(screen.getByText(/Сутки: оценка/)).toBeInTheDocument();
    expect(
      screen.getByText("Meta").closest("[data-source-status]"),
    ).toHaveAttribute("data-source-status", "degraded");
    expect(screen.getByText(/USD · снимок неполный/)).toBeInTheDocument();
    expect(screen.queryByText("USD · подтверждена")).not.toBeInTheDocument();
    expect(screen.getAllByText(/Снимок · 42 сек назад/).length).toBeGreaterThan(
      0,
    );
    for (const label of screen.getAllByText("Δ stop")) {
      expect(label.closest("div")?.querySelector("dd")).not.toHaveClass(
        "text-danger",
      );
    }
    expect(
      document.querySelectorAll('path[stroke="var(--color-danger)"]'),
    ).toHaveLength(0);

    mockRealtimeStatus.mockReturnValue("reconnecting");
    rerender(<AnalyticsPage />);
    expect(screen.getAllByText("Устарело").length).toBeGreaterThan(0);
    expect(
      screen.getByText("Meta").closest("[data-source-status]"),
    ).toHaveAttribute("data-source-status", "unknown");
    expect(
      screen.getByText("AdSet.pro").closest("[data-source-status]"),
    ).toHaveAttribute("data-source-status", "unknown");
    expect(screen.getByText(/Сутки: снимок устарел/)).toBeInTheDocument();
    expect(screen.getByText(/USD · снимок устарел/)).toBeInTheDocument();
    expect(screen.queryByText("USD · подтверждена")).not.toBeInTheDocument();
    for (const label of screen.getAllByText("Δ stop")) {
      expect(label.closest("div")?.querySelector("dd")).not.toHaveClass(
        "text-danger",
      );
    }
  });

  it("keeps cached analytics stale for the full mounted refetch", () => {
    mockUsePerformance.mockReturnValue({
      ...queryResult(makePerformanceFixture(), performanceRefetch),
      isFetching: true,
    });

    render(<AnalyticsPage />);

    expect(screen.getAllByText("Устарело").length).toBeGreaterThan(0);
    expect(
      screen.getByText("Meta").closest("[data-source-status]"),
    ).toHaveAttribute("data-source-status", "unknown");
    expect(screen.getByText(/Сутки: снимок устарел/)).toBeInTheDocument();
  });

  it("marks the event feed stale for the full realtime refetch", () => {
    mockUseEvents.mockReturnValue({
      ...queryResult(
        [
          {
            event_type: "task",
            ts: "2026-07-26T10:09:00.000Z",
            fb_ad_id: "120001",
            ad_name: "GH_CR2",
            campaign_id: "campaign_1",
            campaign_name: "Campaign Alpha",
            stage: null,
            rule_codes: null,
            task_type: "meta_api_mutation",
            task_status: "SUCCEEDED",
          },
        ],
        eventsRefetch,
      ),
      isFetching: true,
    });

    render(<AnalyticsPage />);

    expect(screen.getByTestId("events-state")).toHaveAttribute(
      "data-state",
      "stale",
    );
    expect(screen.getByTestId("events-state")).toHaveTextContent(
      "Лента сверяется с журналом",
    );
  });

  it("does not turn a degraded zero-row response into a confirmed empty result", () => {
    const degraded = makePerformanceFixture();
    degraded.state = "partial";
    degraded.issues = ["Tracker coverage is incomplete"];
    degraded.rows = [];
    degraded.pagination = { page: 1, page_size: 20, total: 0, pages: 0 };
    mockUsePerformance.mockReturnValue(
      queryResult(degraded, performanceRefetch),
    );

    render(<AnalyticsPage />);

    expect(screen.getByText("Количество не подтверждено")).toBeInTheDocument();
    expect(
      screen.queryByText(
        "Сервер подтвердил, что по выбранным фильтрам строк нет.",
      ),
    ).not.toBeInTheDocument();
    expect(screen.getAllByText("Частично").length).toBeGreaterThan(0);
  });

  it("labels zero rows as confirmed only for the explicit empty state", () => {
    const empty = makePerformanceFixture();
    empty.state = "empty";
    empty.rows = [];
    empty.pagination = { page: 1, page_size: 20, total: 0, pages: 0 };
    mockUsePerformance.mockReturnValue(queryResult(empty, performanceRefetch));

    render(<AnalyticsPage />);

    expect(screen.getByText("0 строк")).toBeInTheDocument();
    expect(
      screen.getByText(
        "Сервер подтвердил, что по выбранным фильтрам строк нет.",
      ),
    ).toBeInTheDocument();
  });

  it("fails closed for unavailable payloads and request errors", () => {
    const unavailable = makePerformanceFixture();
    unavailable.state = "unavailable";
    unavailable.issues = ["Meta source unavailable"];
    unavailable.totals.spend = "0.00";
    unavailable.rows[0]!.spend = "0.00";
    mockUsePerformance.mockReturnValue(
      queryResult(unavailable, performanceRefetch),
    );

    const { rerender } = render(<AnalyticsPage />);
    expect(screen.getAllByText("Недоступно").length).toBeGreaterThan(0);
    expect(screen.queryByText("$0.00")).not.toBeInTheDocument();
    expect(
      screen.getAllByText(/неподтверждённые значения скрыты/i).length,
    ).toBeGreaterThan(0);

    mockUsePerformance.mockReturnValue({
      data: undefined,
      isPending: false,
      isError: true,
      isPlaceholderData: false,
      isFetching: false,
      error: new Error("Источник аналитики не отвечает"),
      refetch: performanceRefetch,
    });
    rerender(<AnalyticsPage />);
    expect(
      screen.getByText(/Источник аналитики не отвечает/),
    ).toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: "Повторить" }));
    expect(performanceRefetch).toHaveBeenCalledOnce();
  });

  it("hides live spend and threshold values for an unavailable series", () => {
    const unavailable = makeLiveBudgetFixture();
    unavailable.state = "unavailable";
    unavailable.issues = ["Meta budget series unavailable"];
    mockUseLiveBudget.mockReturnValue(
      queryResult(unavailable, liveBudgetRefetch),
    );

    render(<AnalyticsPage />);

    const heading = screen.getByRole("heading", {
      name: "Накопительный расход",
    });
    const chart = heading.closest("figure");
    expect(chart).not.toBeNull();
    expect(chart).toHaveTextContent(
      "Значения расхода и порогов не подтверждены и скрыты.",
    );
    expect(
      within(chart as HTMLElement).queryByText(/184,20/),
    ).not.toBeInTheDocument();
    expect(
      within(chart as HTMLElement).getAllByText("—").length,
    ).toBeGreaterThan(0);
  });
});

function queryResult<T>(data: T, refetch: () => void) {
  return {
    data,
    isPending: false,
    isError: false,
    isPlaceholderData: false,
    isFetching: false,
    error: null,
    refetch,
  };
}

function makeLiveBudgetFixture(): AnalyticsLiveBudgetSeries {
  const performance = makePerformanceFixture();
  return {
    state: "ready",
    as_of: performance.as_of,
    freshness_seconds: performance.freshness_seconds,
    issues: [],
    sources: performance.sources,
    scope: performance.scope,
    window: performance.window,
    points: [
      {
        ts: "2026-07-26T08:00:00.000Z",
        actual: "120.00",
        base: "100.00",
        stop: "150.00",
        available_ads: 2,
        unavailable_ads: 0,
      },
      {
        ts: "2026-07-26T10:00:00.000Z",
        actual: "184.20",
        base: "148.00",
        stop: "222.00",
        available_ads: 2,
        unavailable_ads: 0,
      },
    ],
  };
}

function makeEventsFixture() {
  return [
    {
      event_type: "task" as const,
      ts: "2026-07-26T10:09:00.000Z",
      fb_ad_id: "120001",
      ad_name: "GH_CR2",
      campaign_id: "campaign_1",
      campaign_name: "Campaign Alpha",
      stage: null,
      rule_codes: null,
      task_type: "meta_api_mutation",
      task_status: "SUCCEEDED",
    },
  ];
}

function makePerformanceFixture(): AnalyticsPerformance {
  return {
    state: "ready",
    as_of: "2026-07-26T10:10:00.000Z",
    freshness_seconds: 42,
    issues: [],
    scope: makeOperatorScopeEvidence(),
    window: {
      from_iso: "2026-07-26T00:00:00.000Z",
      to_iso: "2026-07-26T10:10:00.000Z",
      is_live: true,
      timezone: "Europe/Kaliningrad",
      timezone_known: true,
      timezone_state: "single",
      missing_timezone_account_ids: [],
      issues: [],
      cabinet_day_note: null,
    },
    sources: {
      meta: {
        source: "meta",
        status: "good",
        last_event_at: "2026-07-26T10:09:30.000Z",
        lag_seconds: 30,
        unmatched_events: 0,
        timezone_known: true,
        missing_timezone_account_ids: [],
        issues: [],
        note: null,
      },
      tracker: {
        source: "tracker",
        status: "degraded",
        last_event_at: "2026-07-26T10:08:00.000Z",
        lag_seconds: 120,
        unmatched_events: 2,
        timezone_known: true,
        missing_timezone_account_ids: [],
        issues: ["2 unmatched events"],
        note: "Есть несопоставленные события",
      },
    },
    totals: {
      spend: "184.20",
      impressions: 12_400,
      clicks: 620,
      leads: 91,
      registrations: 74,
      ftds: 11,
      confirmed_deposits: 9,
      redeposits: 3,
      revenue: "430.00",
      cpc: "0.30",
      ctr_pct: "5.00",
      click_registration_cr_pct: "11.94",
      registration_ftd_cr_pct: "14.86",
      cost_per_registration: "2.49",
      cost_per_ftd: "16.75",
      roi_pct: "133.44",
      roas: "2.33",
    },
    total_live_budget: {
      stage: "registration",
      base_unit: "2.00",
      stop_unit: "3.00",
      quantity: 74,
      base_budget: "148.00",
      stop_budget: "222.00",
      base_delta: "36.20",
      stop_delta: "-37.80",
    },
    total_budget_unavailable_reason: null,
    pagination: { page: 1, page_size: 20, total: 2, pages: 1 },
    filter_options: {
      accounts: [{ value: "act_1", label: "Main cabinet" }],
      offers: [{ value: "offer_1", label: "AVI" }],
      campaigns: [
        { value: "campaign_1", label: "Campaign Alpha" },
        { value: "campaign_2", label: "Campaign Beta" },
      ],
    },
    rows: [
      makeRow({
        id: "campaign_1",
        fbId: "120001",
        name: "Campaign Alpha",
        spend: "130.20",
        clicks: 420,
        registrations: 50,
        ftds: 8,
      }),
      makeRow({
        id: "campaign_2",
        fbId: "120002",
        name: "Campaign Beta",
        spend: "54.00",
        clicks: 200,
        registrations: 24,
        ftds: 3,
      }),
    ],
  };
}

function makeRow({
  id,
  fbId,
  name,
  spend,
  clicks,
  registrations,
  ftds,
}: {
  id: string;
  fbId: string;
  name: string;
  spend: string;
  clicks: number;
  registrations: number;
  ftds: number;
}): AnalyticsPerformance["rows"][number] {
  return {
    id,
    fb_id: fbId,
    name,
    level: "campaign",
    parent_id: null,
    parent_name: null,
    has_children: true,
    ad_account_id: "act_1",
    cabinet_timezone: "Europe/Kaliningrad",
    timezone_known: true,
    timezone_state: "single",
    offer_id: "offer_1",
    offer_code: "AVI",
    state: "ready",
    issues: [],
    spend,
    impressions: 5_000,
    clicks,
    leads: registrations + 10,
    registrations,
    ftds,
    confirmed_deposits: ftds,
    redeposits: 1,
    revenue: "200.00",
    cpc: "0.30",
    ctr_pct: "5.00",
    click_registration_cr_pct: "12.00",
    registration_ftd_cr_pct: "16.00",
    cost_per_registration: "2.60",
    cost_per_ftd: "16.28",
    roi_pct: "53.61",
    roas: "1.54",
    live_budget: {
      stage: "registration",
      base_unit: "2.00",
      stop_unit: "3.00",
      quantity: registrations,
      base_budget: String(registrations * 2),
      stop_budget: String(registrations * 3),
      base_delta: "10.20",
      stop_delta: "-39.80",
    },
    budget_unavailable_reason: null,
  };
}

function makeDaypartFixture(): AnalyticsDaypart {
  return {
    state: "partial",
    as_of: "2026-07-26T10:10:00.000Z",
    freshness_seconds: 42,
    issues: ["Один час не подтверждён"],
    timezone: "Europe/Kaliningrad",
    from_iso: "2026-07-20T00:00:00.000Z",
    to_iso: "2026-07-26T10:10:00.000Z",
    sources: makePerformanceFixture().sources,
    scope: makeOperatorScopeEvidence(),
    cells: [
      ...Array.from({ length: 24 }, (_, hour) => ({
        weekday: 1,
        hour,
        clicks: hour === 5 ? null : hour * 2,
        registrations: hour === 5 ? null : hour,
        ftds: hour === 5 ? null : Math.floor(hour / 4),
      })),
      {
        weekday: 2,
        hour: 9,
        clicks: 12,
        registrations: 4,
        ftds: 1,
      },
    ],
  };
}
