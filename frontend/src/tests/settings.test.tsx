// Тесты Settings страницы:
//   1. ObserverTab рендерит настройки и статус.
//   2. Тоггл сканирования вызывает мутацию.
//   3. HealthTab показывает ONLINE/OFFLINE badge для каждого воркера.

import { describe, it, expect, vi, beforeEach } from "vitest";
import { render, screen, fireEvent, waitFor } from "@testing-library/react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";

import { ObserverTab } from "@/components/settings/ObserverTab";
import { HealthTab } from "@/components/settings/HealthTab";
import type { ObserverSettings, ObserverStatus, HealthDetails } from "@/lib/types/api";

// Мок-данные для Observer — структура соответствует backend ObserverSettingsResponse.
const OBSERVER_SETTINGS: ObserverSettings = {
  is_scanning_enabled: true,
  default_interval_seconds: 60,
  auto_enable_recommendations: true,
  owner_campaign_tag: "MV",
  campaign_ids: [],
  warning_percent_of_stop: null,
  cpc_warning_percent: null,
  cpl_warning_percent: null,
  cpr_warning_percent: null,
};

// Мок-данные для ObserverStatus — структура соответствует backend ObserverStatusResponse.
const OBSERVER_STATUS: ObserverStatus = {
  status: "running",
  last_scan_at: "2026-05-28T14:32:00Z",
  last_scan_outcome: "success",
  scans_today: 847,
  interval_seconds: 60,
  extra: {},
};

// Мок-данные для Health — структура соответствует backend HealthDetailsResponse + WorkerStatus.
const HEALTH_DETAILS: HealthDetails = {
  overall: "DEGRADED",
  observer_runtime: null,
  workers: [
    { name: "observer", status: "ONLINE", last_heartbeat_at: "2026-05-28T14:32:00Z", ttl_seconds: 45, payload: null },
    { name: "disable", status: "ONLINE", last_heartbeat_at: "2026-05-28T14:32:00Z", ttl_seconds: 45, payload: null },
    { name: "enable", status: "OFFLINE", last_heartbeat_at: "2026-05-28T14:20:00Z", ttl_seconds: null, payload: null },
    { name: "telegram_poller", status: "ONLINE", last_heartbeat_at: "2026-05-28T14:32:00Z", ttl_seconds: 45, payload: null },
  ],
};

// Мокируем API-хуки, чтобы не обращаться к сети.
vi.mock("@/lib/api/settings", () => ({
  useObserverSettings: () => ({
    data: OBSERVER_SETTINGS,
    isLoading: false,
    isError: false,
    refetch: vi.fn(),
  }),
  useObserverStatus: () => ({
    data: OBSERVER_STATUS,
    isLoading: false,
    isError: false,
    refetch: vi.fn(),
  }),
  useScanRuns: () => ({
    data: [],
    isLoading: false,
    isError: false,
    refetch: vi.fn(),
  }),
  useUpdateObserver: () => ({
    mutate: vi.fn(),
    isPending: false,
  }),
  useToggleScanning: () => ({
    mutate: vi.fn(),
    isPending: false,
  }),
  useToggleAutoEnable: () => ({
    mutate: vi.fn(),
    isPending: false,
  }),
  useTriggerScanNowSettings: () => ({
    mutate: vi.fn(),
    isPending: false,
  }),
  useObserverCampaigns: () => ({
    data: [],
    isLoading: false,
    isError: false,
    refetch: vi.fn(),
  }),
  useSetObserverCampaigns: () => ({
    mutate: vi.fn(),
    isPending: false,
  }),
  useRefreshCampaigns: () => ({
    mutate: vi.fn(),
    isPending: false,
  }),
  useHealthDetails: () => ({
    data: HEALTH_DETAILS,
    isLoading: false,
    isError: false,
    refetch: vi.fn(),
  }),
  useRestartObserver: () => ({
    mutate: vi.fn(),
    isPending: false,
  }),
}));

/** Обёртка с QueryClient для компонентов, использующих TanStack Query. */
function withQuery(ui: React.ReactElement) {
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(<QueryClientProvider client={qc}>{ui}</QueryClientProvider>);
}

describe("Settings · ObserverTab", () => {
  // Тест: рендерит интервал + заметку + scan-runs. Тумблеры переехали на Панель.
  it("рендерит секцию настроек сканирования (без дублей тумблеров)", () => {
    withQuery(<ObserverTab />);
    expect(screen.getByText("Настройки сканирования")).toBeInTheDocument();
    expect(screen.getByText("Интервал скана")).toBeInTheDocument();
    expect(screen.getByText("Последние сканы")).toBeInTheDocument();
    // Тумблер сканирования переехал на Панель — в Настройках его нет (без дублей).
    expect(screen.queryByRole("switch", { name: "Сканирование" })).not.toBeInTheDocument();
  });

  // Тест: статус observer отображается из useObserverStatus (локализованный лейбл + scans_today).
  it("показывает статус observer из мока (работает) и кол-во сканов сегодня", () => {
    withQuery(<ObserverTab />);
    expect(screen.getByText("работает")).toBeInTheDocument();
    // Сканов сегодня берётся из scans_today (раньше был баг — читалось cycle_count_today).
    expect(screen.getByText("847")).toBeInTheDocument();
  });
});

describe("Settings · HealthTab", () => {
  beforeEach(() => {
    vi.clearAllMocks();
  });

  // Тест: рендерит ONLINE/OFFLINE badge для каждого воркера из мока.
  it("рендерит badge ONLINE/OFFLINE для воркеров", () => {
    withQuery(<HealthTab />);

    // observer, disable, telegram_poller — ONLINE (3 воркера).
    const onlineBadges = screen.getAllByText("ONLINE");
    expect(onlineBadges.length).toBeGreaterThanOrEqual(3);

    // enable — OFFLINE (в легенде тоже упоминается, поэтому используем getAllByText).
    const offlineBadges = screen.getAllByText("OFFLINE");
    expect(offlineBadges.length).toBeGreaterThanOrEqual(1);
  });

  // Тест: overall DEGRADED отображается как Badge с человекочитаемым лейблом «Деградация».
  it("показывает overall DEGRADED badge", () => {
    withQuery(<HealthTab />);
    // Badge переводит DEGRADED → «Деградация» (легенда использует «ДЕГРАДАЦИЯ» отдельным текстом).
    const degraded = screen.getAllByText("Деградация");
    expect(degraded.length).toBeGreaterThanOrEqual(1);
    // Хотя бы один из элементов — Badge (имеет rounded-full класс через предка).
    const hasBadge = degraded.some((el) => el.closest(".rounded-full") !== null);
    expect(hasBadge).toBe(true);
  });

  // Тест: кнопка restart observer открывает ConfirmDialog с confirmWord=RESTART.
  it("кнопка Restart Observer открывает ConfirmDialog", async () => {
    withQuery(<HealthTab />);

    const restartBtn = screen.getByRole("button", { name: /перезапустить observer/i });
    fireEvent.click(restartBtn);

    await waitFor(() => {
      // ConfirmDialog показывает label "Введите RESTART для подтверждения".
      expect(screen.getByText(/Введите RESTART для подтверждения/i)).toBeInTheDocument();
    });
  });
});
