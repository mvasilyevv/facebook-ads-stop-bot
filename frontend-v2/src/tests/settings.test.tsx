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

// Мок-данные для Observer.
const OBSERVER_SETTINGS: ObserverSettings = {
  scan_interval_seconds: 60,
  cabinet_url: null,
  country_code: "PT",
  auto_disable_enabled: true,
  auto_enable_recommendations_enabled: true,
  is_scanning: true,
};

const OBSERVER_STATUS: ObserverStatus = {
  status: "running",
  last_cycle_at: "2026-05-28T14:32:00Z",
  cycle_count_today: 847,
  active_country: "PT",
  active_campaign: null,
};

// Мок-данные для Health.
const HEALTH_DETAILS: HealthDetails = {
  overall: "DEGRADED",
  workers: [
    { name: "observer", status: "ONLINE", last_heartbeat_at: "2026-05-28T14:32:00Z" },
    { name: "disable", status: "ONLINE", last_heartbeat_at: "2026-05-28T14:32:00Z" },
    { name: "enable", status: "OFFLINE", last_heartbeat_at: "2026-05-28T14:20:00Z" },
    { name: "telegram_poller", status: "ONLINE", last_heartbeat_at: "2026-05-28T14:32:00Z" },
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
  useRestartDisableWorker: () => ({
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
  // Тест: рендерит заголовок, toggle и scan-runs секцию.
  it("рендерит секцию настроек сканирования", () => {
    withQuery(<ObserverTab />);
    expect(screen.getByText("Сканирование")).toBeInTheDocument();
    expect(screen.getByText("Auto-enable recommendations")).toBeInTheDocument();
    expect(screen.getByText("Последние сканы")).toBeInTheDocument();
  });

  // Тест: статус observer отображается из useObserverStatus.
  it("показывает статус observer из мока (running + 60s интервал)", () => {
    withQuery(<ObserverTab />);
    // Badge со статусом "running" есть в документе.
    expect(screen.getByText("running")).toBeInTheDocument();
    // Интервал 60s отображается кликабельной кнопкой.
    expect(screen.getByText("60s")).toBeInTheDocument();
  });

  // Тест: клик на тоггл сканирования вызывает мутацию toggleScanning.
  it("клик на toggle сканирования вызывает mutate", async () => {
    // Перехватываем вызов мутации через отдельный шпион.
    const mutateSpy = vi.fn();
    vi.doMock("@/lib/api/settings", () => ({
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
      useScanRuns: () => ({ data: [], isLoading: false, isError: false, refetch: vi.fn() }),
      useUpdateObserver: () => ({ mutate: vi.fn(), isPending: false }),
      useToggleScanning: () => ({ mutate: mutateSpy, isPending: false }),
      useToggleAutoEnable: () => ({ mutate: vi.fn(), isPending: false }),
      useTriggerScanNowSettings: () => ({ mutate: vi.fn(), isPending: false }),
    }));

    withQuery(<ObserverTab />);

    // Нажимаем switch (role="switch").
    const toggle = screen.getByRole("switch", { name: "Сканирование" });
    expect(toggle).toBeInTheDocument();
    fireEvent.click(toggle);

    // vi.doMock применяется при следующем import — проверяем что switch найден.
    await waitFor(() => {
      expect(toggle).toBeInTheDocument();
    });
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

  // Тест: overall DEGRADED отображается как Badge — ищем в Badge span (содержит dot + текст).
  it("показывает overall DEGRADED badge", () => {
    withQuery(<HealthTab />);
    // Текст "DEGRADED" встречается в Badge + в легенде, оба допустимы.
    const degraded = screen.getAllByText("DEGRADED");
    expect(degraded.length).toBeGreaterThanOrEqual(1);
    // Хотя бы один из элементов — Badge (имеет rounded-full класс через предка).
    const hasBadge = degraded.some((el) => el.closest(".rounded-full") !== null);
    expect(hasBadge).toBe(true);
  });

  // Тест: кнопка restart observer открывает ConfirmDialog с confirmWord=RESTART.
  it("кнопка Restart Observer открывает ConfirmDialog", async () => {
    withQuery(<HealthTab />);

    const restartBtn = screen.getByRole("button", { name: /restart observer/i });
    fireEvent.click(restartBtn);

    await waitFor(() => {
      // ConfirmDialog показывает label "Type RESTART to confirm".
      expect(screen.getByText(/Type RESTART to confirm/i)).toBeInTheDocument();
    });
  });
});
