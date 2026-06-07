/**
 * Тесты Dashboard страницы.
 * Моки: useDashboardBatch, useDisableTasks, useEnableTasks, useRealtimeInvalidation.
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
  useAds: vi.fn(() => ({ data: { data: [], total: 0 }, isLoading: false, isError: false, refetch: vi.fn() })),
  useBulkDisable: vi.fn(() => ({ mutateAsync: vi.fn(), isPending: false })),
  useBulkSnooze: vi.fn(() => ({ mutateAsync: vi.fn(), isPending: false })),
  useAdTimeline: vi.fn(() => ({ data: null, isLoading: false, isError: false })),
  useSnoozeAd: vi.fn(() => ({ mutateAsync: vi.fn(), isPending: false })),
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
  // Импорт после установки моков
  const { DashboardPage } = await import("../../routes/index").then((m) => {
    // TanStack Router createFileRoute возвращает { component } — берём компонент
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

  // Скелетон при загрузке
  it("рендерит skeleton при isLoading=true", async () => {
    vi.mocked(useDashboardBatch).mockReturnValue({
      data: undefined,
      isLoading: true,
      isError: false,
      error: null,
      refetch: vi.fn(),
    } as unknown as ReturnType<typeof useDashboardBatch>);

    const { container } = await renderDashboard();
    // Skeleton-заглушки (role=status aria-label=Загрузка) должны быть видны
    const skeletons = container.querySelectorAll('[role="status"]');
    expect(skeletons.length).toBeGreaterThan(0);
  });

  // KPI рендерятся при наличии данных
  it("рендерит KPI из stats при наличии данных", async () => {
    vi.mocked(useDashboardBatch).mockReturnValue({
      data: makeBatch(),
      isLoading: false,
      isError: false,
      error: null,
      refetch: vi.fn(),
    } as unknown as ReturnType<typeof useDashboardBatch>);

    await renderDashboard();

    // 4 KPI-карточки по aria-label группы
    const kpiGroup = screen.getByRole("list", { name: "Ключевые показатели" });
    expect(kpiGroup).toBeInTheDocument();
    // Проверяем значение "Активны" = 80
    expect(screen.getByText("80")).toBeInTheDocument();
    // Observer status
    expect(screen.getByText(/Observer online/i)).toBeInTheDocument();
  });

  // Empty state инцидентов
  it("рендерит empty state когда инцидентов нет", async () => {
    vi.mocked(useDashboardBatch).mockReturnValue({
      data: makeBatch({ recent_incidents: [] }),
      isLoading: false,
      isError: false,
      error: null,
      refetch: vi.fn(),
    } as unknown as ReturnType<typeof useDashboardBatch>);

    await renderDashboard();

    // Empty state текст из ActiveIncidents (оба компонента используют одну строку)
    const emptyEls = screen.getAllByText(/Алертов за 24ч нет/i);
    expect(emptyEls.length).toBeGreaterThan(0);
  });

  // Ошибка загрузки
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
