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
import { useObserverSettings } from "@/lib/api/settings";
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

  // KPI-ячейки + hero-число (под контролем = 80+5+2+1 = 88) рендерятся.
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

  // Paused: observer выключен → eyebrow «ПАУЗА» + warning-баннер.
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
    expect(screen.getByText(/Observer выключен/i)).toBeInTheDocument();
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
});
