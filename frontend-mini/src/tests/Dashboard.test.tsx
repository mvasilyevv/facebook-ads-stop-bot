/**
 * Тест Dashboard: рендер с мок-данными useDashboardBatch.
 */
import { describe, it, expect, vi } from "vitest";
import { render, screen } from "@testing-library/react";
import type { DashboardBatch } from "@fb/shared";

vi.mock("@tanstack/react-router", () => ({
  createFileRoute: () => ({ component: (c: unknown) => c }),
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

vi.mock("@/lib/api", () => ({
  useDashboardBatch: () => ({
    data: MOCK_BATCH,
    isLoading: false,
    isError: false,
    error: null,
    refetch: vi.fn(),
  }),
  useObserverSettings: () => ({
    data: { is_scanning_enabled: true },
  }),
  useToggleScanning: () => ({ mutateAsync: vi.fn(), isPending: false }),
  useTriggerScan: () => ({ mutateAsync: vi.fn(), isPending: false }),
  useTmaDisable: () => ({ mutateAsync: vi.fn(), isPending: false }),
}));

import DashboardPageModule from "./Dashboard.test.helper";

describe("DashboardPage", () => {
  // KPI-плитки отображают числа
  it("показывает KPI: 42 активных, 3 стоп", () => {
    render(<DashboardPageModule />);
    expect(screen.getByText("42")).toBeInTheDocument();
    expect(screen.getByText("3")).toBeInTheDocument();
    expect(screen.getByText("7")).toBeInTheDocument();
  });

  // Активный сигнал отображается в списке
  it("показывает инцидент 'Test Ad Stop'", () => {
    render(<DashboardPageModule />);
    expect(screen.getByText("Test Ad Stop")).toBeInTheDocument();
  });

  // Кнопка "Сканировать сейчас" есть
  it("показывает кнопку 'Сканировать сейчас'", () => {
    render(<DashboardPageModule />);
    expect(screen.getByText("Сканировать сейчас")).toBeInTheDocument();
  });
});
