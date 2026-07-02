/**
 * Тест Dashboard: рендер РЕАЛЬНОГО компонента DashboardPage (routes/index.tsx)
 * поверх мокнутого @tanstack/react-router и @/lib/api — паттерн StatsPage
 * (именованный экспорт компонента, без отдельного test.helper.tsx).
 *
 * MID-23 аудита 02.07: добавлен isError-рендер (недоступный batch → видимое
 * состояние ошибки, не пустой экран) — этого сценария раньше не было.
 */
import { describe, it, expect, vi } from "vitest";
import { render, screen } from "@testing-library/react";
import type { DashboardBatch } from "@fb/shared";

vi.mock("@tanstack/react-router", () => ({
  createFileRoute: () => (opts: { component: unknown }) => ({ options: opts, component: opts.component }),
  useNavigate: () => vi.fn(),
  useRouter: () => ({ navigate: vi.fn() }),
  useLocation: () => ({ pathname: "/" }),
}));

vi.mock("@/lib/tg", () => ({
  haptic: { impact: vi.fn(), notify: vi.fn(), selection: vi.fn() },
  tgConfirm: vi.fn().mockResolvedValue(true),
  tgAlert: vi.fn().mockResolvedValue(undefined),
  initTheme: vi.fn(),
  getInitData: () => "",
  registerBackButton: vi.fn(() => () => {}),
  hideBackButton: vi.fn(),
}));

// Мок-данные: используем реальный тип DashboardBatch из @fb/shared.
// recent_incidents — Record<string, unknown>[] (так как API-схема использует unknown).
const MOCK_BATCH: DashboardBatch = {
  stats: {
    total_ads_monitored: 42,
    ads_in_stop: 3,
    ads_in_warning: 7,
    ads_in_disabled: 12,
    ads_in_normal: 20,
    ads_in_claimed: 0,
    active_incidents: 3,
    observer_status: "running",
    last_scan_at: new Date().toISOString(),
    last_scan_outcome: null,
    scans_today: 48,
    scans_today_with_errors: 0,
    pending_disable_tasks: 1,
    pending_enable_tasks: 0,
    failed_tasks_24h: 0,
  },
  recent_incidents: [
    {
      fb_ad_id: "ad123",
      ad_name: "Test Ad Stop",
      alert_state: "stop_sent",
      stop_rule_codes: ["spend_no_event"],
      warning_rule_codes: [],
    } as unknown as Record<string, unknown>,
  ],
  recent_disable_tasks: [],
  recent_alerts: [],
  enable_recommendations_pending: [],
};

const mockUseDashboardBatch = vi.fn();

vi.mock("@/lib/api", () => ({
  useDashboardBatch: (...args: unknown[]) => mockUseDashboardBatch(...args),
  useObserverSettings: () => ({
    data: { is_scanning_enabled: true, default_interval_seconds: 60 },
  }),
  useObserverStatus: () => ({ data: undefined, isLoading: false, isError: false }),
  useToggleScanning: () => ({ mutateAsync: vi.fn(), isPending: false }),
  useTriggerScan: () => ({ mutateAsync: vi.fn(), isPending: false }),
  useSpendSeries: () => ({ data: [] }),
  useStatsToday: () => ({ data: undefined, isLoading: false }),
}));

import { DashboardPage } from "@/routes/index";

describe("DashboardPage", () => {
  beforeEachSetup();

  function beforeEachSetup() {
    // Дефолт — happy path; переопределяется точечно в isError-тесте.
    mockUseDashboardBatch.mockReturnValue({
      data: MOCK_BATCH,
      isLoading: false,
      isError: false,
      error: null,
      refetch: vi.fn(),
    });
  }

  // KPI-плитки отображают числа (42 встречается дважды: hero-число + KPI «ВСЕГО»)
  it("показывает KPI: 42 активных, 3 стоп", () => {
    mockUseDashboardBatch.mockReturnValue({
      data: MOCK_BATCH,
      isLoading: false,
      isError: false,
      error: null,
      refetch: vi.fn(),
    });
    render(<DashboardPage />);
    expect(screen.getAllByText("42").length).toBeGreaterThan(0);
    expect(screen.getAllByText("3").length).toBeGreaterThan(0);
    expect(screen.getAllByText("7").length).toBeGreaterThan(0);
  });

  // Активный сигнал отображается в списке
  it("показывает инцидент 'Test Ad Stop'", () => {
    mockUseDashboardBatch.mockReturnValue({
      data: MOCK_BATCH,
      isLoading: false,
      isError: false,
      error: null,
      refetch: vi.fn(),
    });
    render(<DashboardPage />);
    expect(screen.getByText("Test Ad Stop")).toBeInTheDocument();
  });

  // Кнопка "Сканировать сейчас" есть (aria-label на кнопке, RefreshCw-иконка)
  it("показывает кнопку 'Сканировать сейчас'", () => {
    mockUseDashboardBatch.mockReturnValue({
      data: MOCK_BATCH,
      isLoading: false,
      isError: false,
      error: null,
      refetch: vi.fn(),
    });
    render(<DashboardPage />);
    expect(screen.getByLabelText("Сканировать сейчас")).toBeInTheDocument();
  });

  // MID-23: недоступность batch (ошибка сети/сервера) → видимое error-состояние,
  // не пустой/белый экран. Владелец должен понять, что данные не загрузились.
  it("при ошибке batch показывает видимое состояние ошибки, а не пустой экран", () => {
    mockUseDashboardBatch.mockReturnValue({
      data: undefined,
      isLoading: false,
      isError: true,
      error: new Error("Сеть недоступна"),
      refetch: vi.fn(),
    });
    render(<DashboardPage />);
    expect(screen.getByText("Ошибка загрузки")).toBeInTheDocument();
    expect(screen.getByText("Сеть недоступна")).toBeInTheDocument();
    // KPI из happy-path не должны просочиться на error-рендере.
    expect(screen.queryByText("Test Ad Stop")).not.toBeInTheDocument();
  });
});
