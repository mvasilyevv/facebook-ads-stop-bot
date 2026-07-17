import { render, screen } from "@testing-library/react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { beforeEach, describe, expect, it, vi } from "vitest";

vi.mock("@tanstack/react-router", () => ({
  createFileRoute: (_path: string) => (options: { component: unknown }) => options,
  useRouter: () => ({ navigate: vi.fn() }),
}));

vi.mock("@/lib/api/dashboard", () => ({
  useDashboardBatch: vi.fn(),
  useEnableRecommendations: vi.fn(() => ({ data: [], isLoading: false })),
  useConfirmEnableRecommendation: vi.fn(() => ({
    mutate: vi.fn(),
    isPending: false,
    variables: undefined,
  })),
}));

vi.mock("@/lib/api/analytics", () => ({
  useAnalyticsPerformance: vi.fn(() => ({
    data: {
      totals: {
        spend: "123.45",
        impressions: 2000,
        clicks: 100,
        registrations: 12,
        ftds: 4,
        confirmed_deposits: 3,
        redeposits: 1,
        revenue: "250.00",
      },
      total_live_budget: {
        base_budget: "100.00",
        stop_budget: "80.00",
        base_delta: "23.45",
        stop_delta: "43.45",
      },
      rows: [
        {
          id: "00000000-0000-0000-0000-000000000001",
          name: "Campaign A",
          offer_code: "OFF-A",
          live_budget: { base_budget: "100.00", base_delta: "23.45" },
        },
      ],
    },
    isLoading: false,
  })),
  useAnalyticsLiveBudget: vi.fn(() => ({
    data: { points: [] },
    isLoading: false,
  })),
}));

vi.mock("@/lib/api/ads", () => ({
  useDisableTasks: vi.fn(() => ({ data: [], isLoading: false, isError: false, refetch: vi.fn() })),
  useEnableTasks: vi.fn(() => ({ data: [], isLoading: false, isError: false, refetch: vi.fn() })),
}));

vi.mock("@/lib/api/settings", () => ({
  useObserverSettings: vi.fn(() => ({
    data: {
      is_scanning_enabled: true,
      default_interval_seconds: 30,
      auto_enable_recommendations: false,
    },
    isLoading: false,
    isError: false,
  })),
  useToggleScanning: vi.fn(() => ({ mutate: vi.fn(), isPending: false })),
  useObserverStatus: vi.fn(() => ({ data: undefined, isLoading: false, isError: false })),
  useHealthDetails: vi.fn(() => ({
    data: {
      workers: [{ name: "observer", status: "ONLINE" }],
      observer_runtime: { status: "running" },
      meta_api_channel: { status: "ONLINE" },
      overall: "HEALTHY",
    },
    isLoading: false,
    isError: false,
  })),
}));

vi.mock("@/lib/websocket/useRealtimeInvalidation", () => ({
  useRealtimeInvalidation: vi.fn(() => undefined),
}));

vi.mock("@/lib/api/client", () => ({ apiSend: vi.fn().mockResolvedValue(undefined) }));

import { useDashboardBatch } from "@/lib/api/dashboard";
import { useHealthDetails, useObserverSettings } from "@/lib/api/settings";

function makeBatch(overrides: Record<string, unknown> = {}) {
  return {
    stats: {
      total_ads_monitored: 100,
      ads_in_normal: 80,
      ads_in_warning: 5,
      ads_in_stop: 2,
      ads_in_claimed: 1,
      ads_in_disabled: 12,
      active_incidents: 7,
      last_scan_at: new Date().toISOString(),
      last_scan_outcome: "ok",
      scans_today: 42,
      scans_today_with_errors: 0,
      observer_status: "running",
      pending_disable_tasks: 0,
      pending_enable_tasks: 0,
      failed_tasks_24h: 0,
    },
    recent_incidents: [],
    recent_alerts: [],
    recent_disable_tasks: [],
    recent_enable_tasks: [],
    enable_recommendations_pending: [],
    ...overrides,
  };
}

async function renderDashboard() {
  const routeModule = await import("../../routes/index");
  const Page = (routeModule.Route as unknown as { component: React.FC }).component;
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(
    <QueryClientProvider client={client}>
      <Page />
    </QueryClientProvider>,
  );
}

describe("operator Dashboard", () => {
  beforeEach(() => {
    vi.clearAllMocks();
    vi.mocked(useDashboardBatch).mockReturnValue({
      data: makeBatch(),
      isLoading: false,
      isError: false,
      error: null,
      refetch: vi.fn(),
    } as never);
  });

  it("shows attention status and live budget economics in the first viewport", async () => {
    await renderDashboard();
    expect(screen.getByRole("heading", { level: 1 })).toHaveTextContent("Что требует внимания");
    expect(screen.getByText("ЭКОНОМИКА СЕГОДНЯ")).toBeInTheDocument();
    expect(screen.getByText("РИСКИ И РЕШЕНИЯ")).toBeInTheDocument();
    expect(screen.getByText("$123.45")).toBeInTheDocument();
    expect(screen.getAllByText("+$23.45").length).toBeGreaterThan(0);
  });

  it("keeps ad states in a compact status strip", async () => {
    await renderDashboard();
    expect(screen.getByText("Норма").parentElement).toHaveTextContent("80");
    expect(screen.getByText("Warning").parentElement).toHaveTextContent("5");
    expect(screen.getAllByText("Stop")[0]?.parentElement).toHaveTextContent("2");
    expect(screen.queryByText(/объявлений под контролем/i)).not.toBeInTheDocument();
  });

  it("shows manual auto-enable mode and recent automatic actions", async () => {
    vi.mocked(useDashboardBatch).mockReturnValue({
      data: makeBatch({
        recent_enable_tasks: [
          {
            id: "task-1",
            ad_name: "Recovered ad",
            fb_ad_id: "123",
            status: "SUCCEEDED",
            requested_by: "auto_enable_recommendation_worker",
            created_at: "2026-07-17T10:00:00Z",
          },
        ],
      }),
      isLoading: false,
      isError: false,
      error: null,
      refetch: vi.fn(),
    } as never);
    await renderDashboard();
    expect(screen.getByText("Только вручную")).toBeInTheDocument();
    expect(screen.getByText("Recovered ad")).toBeInTheDocument();
  });

  it("shows paused monitoring without presenting it as healthy", async () => {
    vi.mocked(useObserverSettings).mockReturnValue({
      data: {
        is_scanning_enabled: false,
        default_interval_seconds: 30,
        auto_enable_recommendations: false,
      },
      isLoading: false,
      isError: false,
    } as never);
    await renderDashboard();
    expect(screen.getByText("Мониторинг на паузе")).toBeInTheDocument();
    expect(screen.queryByText("Система в норме")).not.toBeInTheDocument();
  });

  it("surfaces a critical billing warning and leaves scan controls visible", async () => {
    vi.mocked(useHealthDetails).mockReturnValue({
      data: {
        workers: [{ name: "observer", status: "ONLINE" }],
        observer_runtime: { status: "running" },
        meta_api_channel: { status: "ONLINE" },
        critical_alerts: [
          {
            id: "shadow_spend:123",
            kind: "shadow_spend",
            severity: "CRITICAL",
            title: "Meta списывает быстрее отчётности",
            message: "Биллинг вырос.",
            account_id: "123",
            detected_at: new Date().toISOString(),
            details: {},
          },
        ],
        overall: "CRITICAL",
      },
      isLoading: false,
      isError: false,
    } as never);
    await renderDashboard();
    expect(screen.getByText("Meta списывает быстрее отчётности")).toBeInTheDocument();
    expect(screen.getByText("ПОСЛЕДНИЙ СКАН")).toBeInTheDocument();
    expect(screen.getByRole("link", { name: "Открыть Ads Manager" })).toBeInTheDocument();
  });

  it("renders an actionable error state when the overview fails", async () => {
    vi.mocked(useDashboardBatch).mockReturnValue({
      data: undefined,
      isLoading: false,
      isError: true,
      error: new Error("Network error"),
      refetch: vi.fn(),
    } as never);
    await renderDashboard();
    expect(screen.getByRole("alert")).toHaveTextContent("Не удалось загрузить операторский обзор");
  });
});
