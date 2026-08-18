import type { ComponentType, ReactNode } from "react";
import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { beforeEach, describe, expect, it, vi } from "vitest";

import { AnalyticsPayloadError } from "@fb/shared/analytics/runtime";

import { makeAnalyticsPerformanceFixture } from "./analyticsFixture";

const navigate = vi.fn();
const refetch = vi.fn();
const useAnalyticsPerformance = vi.fn();
const useOperatorRealtimeStatus = vi.fn(() => "connected");
const useOperatorEvents = vi.fn();
const useOperatorDisplayPreference = vi.fn();
const routeSearch = {
  tab: "uploads" as "uploads" | "events",
  period: "today" as const,
  preset: "economy" as const,
  sort: "spend" as const,
  direction: "desc" as const,
  page: 1,
};

vi.mock("@fb/operator-api", () => ({
  useOperatorRealtimeStatus: () => useOperatorRealtimeStatus(),
  safeApiProblemMessage: (_error: unknown, fallback: string) => fallback,
}));

vi.mock("@tanstack/react-router", () => ({
  createFileRoute: () => (options: { component: ComponentType }) => ({
    ...options,
    useSearch: () => routeSearch,
  }),
  useNavigate: () => navigate,
  Link: ({ children }: { children: ReactNode }) => <a href="#detail">{children}</a>,
}));

vi.mock("@/lib/api/analytics", () => ({
  useAnalyticsPerformance: (...args: unknown[]) => useAnalyticsPerformance(...args),
  useAnalyticsLiveBudget: () => ({ data: undefined, isLoading: false, isError: false }),
  useAnalyticsDaypart: () => ({ data: undefined, isLoading: false, isError: false }),
}));

vi.mock("@/lib/api/operator", () => ({
  useOperatorEvents: (...args: unknown[]) => useOperatorEvents(...args),
}));

vi.mock("@/lib/api/settings", () => ({
  useOperatorDisplayPreference: () => useOperatorDisplayPreference(),
}));

import { Route } from "@/routes/analytics/index";

const AnalyticsPage = (Route as unknown as { component: ComponentType }).component;

describe("analytics route fail-closed state", () => {
  beforeEach(() => {
    vi.clearAllMocks();
    routeSearch.tab = "uploads";
    useOperatorRealtimeStatus.mockReturnValue("connected");
    useOperatorEvents.mockReturnValue({
      data: [],
      isLoading: false,
      isFetching: false,
      isError: false,
      error: null,
      refetch: vi.fn(),
    });
    useOperatorDisplayPreference.mockReturnValue({
      data: {
        timezone_name: "Europe/Kaliningrad",
        updated_at: "2026-08-09T10:00:00Z",
      },
      isPending: false,
      isError: false,
      error: null,
      refetch: vi.fn(),
    });
    useAnalyticsPerformance.mockReturnValue({
      // Simulate keepPreviousData from the last valid response plus a failed
      // validation on refresh. The route must not render this stale snapshot.
      data: makeAnalyticsPerformanceFixture(),
      isLoading: false,
      isFetching: false,
      isPlaceholderData: false,
      isError: true,
      error: new AnalyticsPayloadError(),
      refetch,
    });
  });

  it("marks placeholder evidence stale and never paints its sources green", () => {
    useAnalyticsPerformance.mockReturnValue({
      data: makeAnalyticsPerformanceFixture(),
      isLoading: false,
      isFetching: true,
      isPlaceholderData: true,
      isError: false,
      error: null,
      refetch,
    });

    render(<AnalyticsPage />);

    expect(screen.getAllByText("Устарело").length).toBeGreaterThan(0);
    expect(screen.getAllByText("снимок устарел")).toHaveLength(2);
    expect(screen.getByText(/снимок устарел · Europe\/Kaliningrad/)).toBeInTheDocument();
    expect(document.querySelectorAll('[data-source-status="good"]')).toHaveLength(0);
    expect(document.querySelectorAll('[data-source-status="unknown"]')).toHaveLength(5);
  });

  it("fails closed when the server display timezone is unavailable", () => {
    useOperatorDisplayPreference.mockReturnValue({
      data: undefined,
      isPending: false,
      isError: true,
      error: new Error("traceback postgres://secret 00000000-0000-0000-0000-000000000099"),
      refetch: vi.fn(),
    });

    render(<AnalyticsPage />);

    expect(screen.getByText(/Данные не показаны в другом часовом поясе/i)).toBeInTheDocument();
    expect(
      screen.getByText("Откройте настройки отображения или повторите запрос"),
    ).toBeInTheDocument();
    expect(screen.queryByText(/traceback|postgres|00000000-/i)).not.toBeInTheDocument();
  });

  it("does not send the presentation timezone into server analytics boundaries", () => {
    render(<AnalyticsPage />);

    expect(useAnalyticsPerformance).toHaveBeenCalledWith(
      expect.not.objectContaining({
        timezone: expect.anything(),
        timezone_name: expect.anything(),
      }),
    );
  });

  it("exposes the selected analytics view and period without relying on color", async () => {
    render(<AnalyticsPage />);

    const viewGroup = screen.getByRole("tablist", {
      name: "Раздел аналитики",
    });
    const uploadsTab = screen.getByRole("tab", { name: "Заливы" });
    const eventsTab = screen.getByRole("tab", { name: "События" });
    expect(uploadsTab).toHaveAttribute("aria-selected", "true");
    expect(eventsTab).toHaveAttribute("aria-selected", "false");
    expect(viewGroup).toContainElement(uploadsTab);
    expect(screen.getByRole("tabpanel")).toHaveAttribute(
      "aria-labelledby",
      "analytics-tab-uploads",
    );

    uploadsTab.focus();
    await userEvent.keyboard("{ArrowRight}");
    expect(eventsTab).toHaveFocus();
    expect(navigate).toHaveBeenCalledWith(expect.objectContaining({ replace: true }));

    const periodGroup = screen.getByRole("group", {
      name: "Период аналитики",
    });
    expect(screen.getByRole("button", { name: "Сегодня" })).toHaveAttribute("aria-pressed", "true");
    expect(screen.getByRole("button", { name: "7d" })).toHaveAttribute("aria-pressed", "false");
    expect(screen.getByRole("button", { name: "7d" })).toHaveClass("min-w-11");
    expect(periodGroup).toContainElement(screen.getByRole("button", { name: "Сегодня" }));
  });

  it("keeps a mounted analytics refetch stale even after the socket is connected", () => {
    useAnalyticsPerformance.mockReturnValue({
      data: makeAnalyticsPerformanceFixture(),
      isLoading: false,
      isFetching: true,
      isPlaceholderData: false,
      isError: false,
      error: null,
      refetch,
    });

    render(<AnalyticsPage />);

    expect(screen.getAllByText("Устарело").length).toBeGreaterThan(0);
    expect(document.querySelectorAll('[data-source-status="good"]')).toHaveLength(0);
  });

  it.each([
    {
      state: "partial" as const,
      fetching: false,
      currencyLabel: "USD · снимок неполный",
    },
    {
      state: "stale" as const,
      fetching: true,
      currencyLabel: "USD · снимок устарел",
    },
  ])(
    "keeps $state analytics values neutral and exposes snapshot freshness",
    ({ state, fetching, currencyLabel }) => {
      const data = makeAnalyticsPerformanceFixture();
      data.state = state === "partial" ? "partial" : "ready";
      data.issues = state === "partial" ? ["Tracker coverage is incomplete"] : [];
      useAnalyticsPerformance.mockReturnValue({
        data,
        isLoading: false,
        isFetching: fetching,
        isPlaceholderData: false,
        isError: false,
        error: null,
        refetch,
      });

      render(<AnalyticsPage />);

      expect(screen.getByTestId("analytics-freshness")).toHaveTextContent("свежесть 20 сек");
      expect(screen.getByText(currencyLabel)).toBeInTheDocument();
      expect(screen.queryByText("$ · подтверждена")).not.toBeInTheDocument();
      expect(document.querySelectorAll(".text-success, .text-danger")).toHaveLength(0);
      expect(document.querySelectorAll('[data-source-status="good"]')).toHaveLength(0);
    },
  );

  it("labels mixed cabinet timezones explicitly without rendering null", () => {
    const data = makeAnalyticsPerformanceFixture();
    data.scope = {
      ...data.scope,
      cabinet_timezone: null,
      cabinet_timezone_state: "mixed",
    };
    data.window = {
      ...data.window,
      timezone: null,
      timezone_known: true,
      timezone_state: "mixed",
    };
    data.rows[0] = {
      ...data.rows[0]!,
      cabinet_timezone: null,
      timezone_known: false,
      timezone_state: "mixed",
    };
    useAnalyticsPerformance.mockReturnValue({
      data,
      isLoading: false,
      isFetching: false,
      isPlaceholderData: false,
      isError: false,
      error: null,
      refetch,
    });

    render(<AnalyticsPage />);

    expect(
      screen.getAllByText(/Несколько часовых поясов · границы по каждому кабинету/).length,
    ).toBeGreaterThan(0);
    expect(document.body).not.toHaveTextContent(/\bnull\b/i);
  });

  it("downgrades current-looking source evidence while realtime reconnects", () => {
    useOperatorRealtimeStatus.mockReturnValue("reconnecting");
    useAnalyticsPerformance.mockReturnValue({
      data: makeAnalyticsPerformanceFixture(),
      isLoading: false,
      isFetching: false,
      isPlaceholderData: false,
      isError: false,
      error: null,
      refetch,
    });

    render(<AnalyticsPage />);

    expect(screen.getAllByText("Устарело").length).toBeGreaterThan(0);
    expect(document.querySelectorAll('[data-source-status="good"]')).toHaveLength(0);
    expect(document.querySelectorAll('[data-source-status="unknown"]')).toHaveLength(5);
  });

  it("renders an explicit unavailable state and hides cached confirmed metrics", () => {
    render(<AnalyticsPage />);

    const unavailable = screen.getByText("Источник недоступен");
    expect(unavailable).toBeInTheDocument();
    expect(unavailable.closest('[data-state="unavailable"]')).not.toBeNull();
    expect(screen.getByRole("alert")).toHaveTextContent(
      "Аналитика недоступна. Неподтверждённые данные скрыты.",
    );
    // Тело алерта — только recovery-копия маршрута. Сырой текст исключения
    // (в том числе AnalyticsPayloadError) в operator UI не попадает.
    expect(screen.getByRole("alert")).toHaveTextContent(
      "Подтверждённых значений нет. Повторите запрос.",
    );
    expect(screen.getByRole("alert")).not.toHaveTextContent("Ответ аналитики неполный");
    expect(screen.queryByText("META")).not.toBeInTheDocument();
    expect(screen.queryByText("$18.40")).not.toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Повторить" })).toBeInTheDocument();
  });

  it("marks the operator event feed stale while realtime refetch is pending", () => {
    routeSearch.tab = "events";
    useOperatorEvents.mockReturnValue({
      data: [],
      isLoading: false,
      isFetching: true,
      isError: false,
      error: null,
      refetch: vi.fn(),
    });

    render(<AnalyticsPage />);

    expect(
      screen.getByText(
        "Лента сверяется с журналом событий. Показанные записи считаются устаревшими.",
      ),
    ).toBeInTheDocument();
    expect(screen.getByText("Данные устарели")).toBeInTheDocument();
    expect(document.querySelector('[data-state="stale"]')).not.toBeNull();
  });
});
