/**
 * Тесты Dashboard страницы (канон design_handoff).
 * Моки: useDashboardBatch, useChartData, useDisableTasks, useEnableTasks,
 *       useObserverSettings, useToggleScanning, useRealtimeInvalidation, apiSend.
 */

import { render, screen } from "@testing-library/react";
import { describe, it, expect, vi, beforeEach } from "vitest";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";

// ─── Моки модулей ─────────────────────────────────────────────────────────────

// Мокаем TanStack Router — DashboardPage использует useRouter/createFileRoute
vi.mock("@tanstack/react-router", () => ({
  createFileRoute: (_path: string) => (opts: { component: unknown }) => opts,
  useRouter: () => ({ navigate: vi.fn() }),
  useParams: () => ({}),
  Outlet: () => null,
}));

vi.mock("@/lib/api/dashboard", () => ({
  useDashboardBatch: vi.fn(),
  useChartData: vi.fn(() => ({ data: [], isLoading: false, isError: false, refetch: vi.fn() })),
}));

vi.mock("@/lib/api/stats", () => ({
  useStatsToday: vi.fn(() => ({
    data: undefined,
    isLoading: false,
    isError: false,
    refetch: vi.fn(),
  })),
}));

vi.mock("@/lib/api/ads", () => ({
  useDisableTasks: vi.fn(() => ({ data: [], isLoading: false, isError: false, refetch: vi.fn() })),
  useEnableTasks: vi.fn(() => ({ data: [], isLoading: false, isError: false, refetch: vi.fn() })),
}));

vi.mock("@/lib/api/settings", () => ({
  useObserverSettings: vi.fn(() => ({
    data: { is_scanning_enabled: true, default_interval_seconds: 30 },
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
  useRealtimeInvalidation: vi.fn(() => ({
    status: "connected",
    pollingFallback: false,
    reconnectAttempt: 0,
    forceReconnect: vi.fn(),
  })),
}));

vi.mock("@/lib/api/client", () => ({
  apiSend: vi.fn().mockResolvedValue(undefined),
}));

// ─── Импорты после моков ──────────────────────────────────────────────────────

import { useDashboardBatch } from "@/lib/api/dashboard";
import { useHealthDetails, useObserverSettings } from "@/lib/api/settings";
import type { DashboardBatch } from "@fb/shared";

// ─── Фабрика мок-данных ───────────────────────────────────────────────────────

function makeBatch(overrides: Partial<DashboardBatch> = {}): DashboardBatch {
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
      pending_disable_tasks: 3,
      pending_enable_tasks: 1,
      failed_tasks_24h: 0,
    },
    recent_incidents: [],
    recent_alerts: [],
    recent_disable_tasks: [],
    enable_recommendations_pending: [],
    ...overrides,
  };
}

// ─── Хелперы ─────────────────────────────────────────────────────────────────

function createQueryClient() {
  return new QueryClient({
    defaultOptions: { queries: { retry: false } },
  });
}

/** Рендерит DashboardPage напрямую (без Router, через мок). */
async function renderDashboard() {
  const { DashboardPage } = await import("../../routes/index").then((m) => {
    const route = m.Route as unknown as { component: React.FC };
    return { DashboardPage: route.component };
  });

  const qc = createQueryClient();
  return render(
    <QueryClientProvider client={qc}>
      <DashboardPage />
    </QueryClientProvider>,
  );
}

// ─── Тесты ────────────────────────────────────────────────────────────────────

describe("DashboardPage", () => {
  beforeEach(() => {
    vi.clearAllMocks();
    vi.mocked(useObserverSettings).mockReturnValue({
      data: { is_scanning_enabled: true, default_interval_seconds: 30 },
      isLoading: false,
      isError: false,
    } as unknown as ReturnType<typeof useObserverSettings>);
    vi.mocked(useHealthDetails).mockReturnValue({
      data: {
        workers: [{ name: "observer", status: "ONLINE" }],
        observer_runtime: { status: "running" },
        meta_api_channel: { status: "ONLINE" },
        overall: "HEALTHY",
      },
      isLoading: false,
      isError: false,
    } as unknown as ReturnType<typeof useHealthDetails>);
  });

  // Заголовок страницы — «Панель» (русский, без точки), а не «Dashboard».
  it("рендерит h1 «Панель»", async () => {
    vi.mocked(useDashboardBatch).mockReturnValue({
      data: makeBatch(),
      isLoading: false,
      isError: false,
      error: null,
      refetch: vi.fn(),
    } as unknown as ReturnType<typeof useDashboardBatch>);

    await renderDashboard();
    const h1 = screen.getByRole("heading", { level: 1 });
    expect(h1).toHaveTextContent("Панель");
  });

  // Скелетон KPI при загрузке (нет stats).
  it("рендерит skeleton при isLoading=true", async () => {
    vi.mocked(useDashboardBatch).mockReturnValue({
      data: undefined,
      isLoading: true,
      isError: false,
      error: null,
      refetch: vi.fn(),
    } as unknown as ReturnType<typeof useDashboardBatch>);

    const { container } = await renderDashboard();
    const skeletons = container.querySelectorAll('[role="status"]');
    expect(skeletons.length).toBeGreaterThan(0);
  });

  // KPI-ячейки + hero рендерятся. Hero-число = total_ads_monitored=100 (ВСЁ под
  // контролем, включая отключённые), а HealthBar показывает долю «Отключено».
  it("рендерит KPI и hero из stats", async () => {
    vi.mocked(useDashboardBatch).mockReturnValue({
      data: makeBatch(),
      isLoading: false,
      isError: false,
      error: null,
      refetch: vi.fn(),
    } as unknown as ReturnType<typeof useDashboardBatch>);

    await renderDashboard();

    // 4 KPI-ячейки по aria-label списка
    const kpiGroup = screen.getByRole("list", { name: "Ключевые показатели" });
    expect(kpiGroup).toBeInTheDocument();
    // Eyebrow'ы KPI
    expect(screen.getByText("ACTIVE")).toBeInTheDocument();
    expect(screen.getByText("DISABLED")).toBeInTheDocument();
    // hero-подпись
    expect(screen.getByText(/объявлений под контролем/i)).toBeInTheDocument();
    expect(screen.getByText("ТРЕБУЕТ ВНИМАНИЯ")).toBeInTheDocument();
    // HealthBar теперь несёт сегмент «Отключено» — disabled (12) не выпадает из портфеля.
    expect(screen.getByRole("img", { name: /Отключено 12/i })).toBeInTheDocument();
  });

  // Калм-empty live-tail: когда алертов нет — редакционная заглушка.
  it("рендерит калм-empty live-tail когда алертов нет", async () => {
    vi.mocked(useDashboardBatch).mockReturnValue({
      data: makeBatch({ recent_alerts: [] }),
      isLoading: false,
      isError: false,
      error: null,
      refetch: vi.fn(),
    } as unknown as ReturnType<typeof useDashboardBatch>);

    await renderDashboard();
    expect(screen.getByText(/Алертов за 24ч нет/i)).toBeInTheDocument();
  });

  // Paused: observer выключен → дашборд показывает паузу, а CTA живёт в едином global status bar.
  it("показывает paused-состояние когда observer выключен", async () => {
    vi.mocked(useDashboardBatch).mockReturnValue({
      data: makeBatch(),
      isLoading: false,
      isError: false,
      error: null,
      refetch: vi.fn(),
    } as unknown as ReturnType<typeof useDashboardBatch>);
    vi.mocked(useObserverSettings).mockReturnValue({
      data: { is_scanning_enabled: false, default_interval_seconds: 30 },
      isLoading: false,
      isError: false,
    } as unknown as ReturnType<typeof useObserverSettings>);

    await renderDashboard();
    expect(screen.getByText(/ПАУЗА/)).toBeInTheDocument();
    expect(screen.getByText("Мониторинг на паузе")).toBeInTheDocument();
    expect(screen.getByText("СКАН ВЫКЛЮЧЕН")).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "Включить" })).not.toBeInTheDocument();
  });

  it("не выдаёт offline-контур за здоровую систему и спокойный live-tail", async () => {
    vi.mocked(useDashboardBatch).mockReturnValue({
      data: makeBatch({ recent_alerts: [] }),
      isLoading: false,
      isError: false,
      error: null,
      refetch: vi.fn(),
    } as unknown as ReturnType<typeof useDashboardBatch>);
    vi.mocked(useHealthDetails).mockReturnValue({
      data: {
        workers: [
          { name: "observer", status: "OFFLINE" },
          { name: "browser-agent", status: "OFFLINE" },
        ],
        observer_runtime: null,
        meta_api_channel: { status: "UNKNOWN" },
        overall: "CRITICAL",
      },
      isLoading: false,
      isError: false,
    } as unknown as ReturnType<typeof useHealthDetails>);

    await renderDashboard();

    expect(screen.getByText("СИСТЕМА НЕДОСТУПНА")).toBeInTheDocument();
    expect(screen.getByText("Мониторинг недоступен")).toBeInTheDocument();
    expect(screen.queryByText("СИСТЕМА В НОРМЕ")).not.toBeInTheDocument();
    expect(screen.queryByText("Алертов за 24ч нет")).not.toBeInTheDocument();
  });

  it("показывает billing CRITICAL в вебе без Telegram и не скрывает scan controls", async () => {
    vi.mocked(useDashboardBatch).mockReturnValue({
      data: makeBatch({ recent_alerts: [] }),
      isLoading: false,
      isError: false,
      error: null,
      refetch: vi.fn(),
    } as unknown as ReturnType<typeof useDashboardBatch>);
    vi.mocked(useHealthDetails).mockReturnValue({
      data: {
        workers: [{ name: "observer", status: "ONLINE" }],
        observer_runtime: { status: "running" },
        meta_api_channel: { status: "ONLINE" },
        critical_alerts: [
          {
            id: "shadow_spend:1855748448431929",
            kind: "shadow_spend",
            severity: "CRITICAL",
            title: "Meta списывает быстрее отчётности",
            message: "Биллинг вырос на $0.34, а per-ad отчётность стоит.",
            account_id: "1855748448431929",
            detected_at: new Date().toISOString(),
            details: { billing_delta_cents: 34 },
          },
        ],
        overall: "CRITICAL",
      },
      isLoading: false,
      isError: false,
    } as unknown as ReturnType<typeof useHealthDetails>);

    await renderDashboard();

    expect(screen.getByText("CRITICAL · MONEY")).toBeInTheDocument();
    expect(screen.getByText("Meta списывает быстрее отчётности")).toBeInTheDocument();
    expect(screen.getByRole("link", { name: /Открыть Ads Manager/i })).toHaveAttribute(
      "href",
      expect.stringContaining("act=1855748448431929"),
    );
    expect(screen.queryByText("СКАН НЕДОСТУПЕН")).not.toBeInTheDocument();
  });

  // Ошибка загрузки batch → ErrorState.
  it("рендерит ErrorState при isError=true", async () => {
    vi.mocked(useDashboardBatch).mockReturnValue({
      data: undefined,
      isLoading: false,
      isError: true,
      error: new Error("Network error"),
      refetch: vi.fn(),
    } as unknown as ReturnType<typeof useDashboardBatch>);

    await renderDashboard();

    expect(screen.getByRole("alert")).toBeInTheDocument();
    expect(screen.getByText(/Не удалось загрузить данные Dashboard/i)).toBeInTheDocument();
  });

  // Headline-спенд берётся из stats.current_day_spend, а не суммируется из серии.
  it("показывает current_day_spend из stats как headline-спенд", async () => {
    vi.mocked(useDashboardBatch).mockReturnValue({
      data: makeBatch({
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
          pending_disable_tasks: 3,
          pending_enable_tasks: 1,
          failed_tasks_24h: 0,
          current_day_spend: "123.45",
        },
      }),
      isLoading: false,
      isError: false,
      error: null,
      refetch: vi.fn(),
    } as unknown as ReturnType<typeof useDashboardBatch>);

    await renderDashboard();
    // formatSpend(123.45) → "$123.45"
    expect(screen.getByText("$123.45")).toBeInTheDocument();
  });

  // Graceful при current_day_spend=null — показывает $0.00, не падает.
  it("graceful при current_day_spend=null — рендерит $0.00", async () => {
    vi.mocked(useDashboardBatch).mockReturnValue({
      data: makeBatch({
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
          pending_disable_tasks: 3,
          pending_enable_tasks: 1,
          failed_tasks_24h: 0,
          current_day_spend: null,
        },
      }),
      isLoading: false,
      isError: false,
      error: null,
      refetch: vi.fn(),
    } as unknown as ReturnType<typeof useDashboardBatch>);

    await renderDashboard();
    expect(screen.getByText("$0.00")).toBeInTheDocument();
  });
});
