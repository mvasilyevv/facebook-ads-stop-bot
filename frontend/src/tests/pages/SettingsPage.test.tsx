/**
 * Тесты Settings-страницы:
 * - Tabs переключение (Observer / Telegram / Vision / Health)
 * - ObserverTab: toggle scanning
 * - HealthTab: вердикт HEALTHY/DEGRADED/CRITICAL по данным
 */

import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, it, expect, vi } from "vitest";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { useState } from "react";

// ─── Моки ─────────────────────────────────────────────────────────────────────

const mockObserverData = {
  is_scanning_enabled: true,
  default_interval_seconds: 60,
  auto_enable_recommendations: false,
  owner_campaign_tag: "MV",
  act_via_api: true,
  campaign_ids: [],
  warning_percent_of_stop: null,
  cpc_warning_percent: null,
  cpl_warning_percent: null,
  cpr_warning_percent: null,
};

const mockUpdateObserver = vi.fn().mockResolvedValue(mockObserverData);
const mockScanNow = vi.fn().mockResolvedValue({ ok: true });
const mockToggleScanning = vi.fn().mockResolvedValue(mockObserverData);
const mockToggleAutoEnable = vi.fn().mockResolvedValue(mockObserverData);

vi.mock("@/lib/api/settings", () => ({
  useObserverSettings: () => ({
    data: mockObserverData,
    isLoading: false,
    error: null,
    refetch: vi.fn(),
  }),
  useUpdateObserverSettings: () => ({
    mutateAsync: mockUpdateObserver,
    isPending: false,
  }),
  useToggleScanning: () => ({
    mutateAsync: mockToggleScanning,
    isPending: false,
  }),
  useToggleAutoEnable: () => ({
    mutateAsync: mockToggleAutoEnable,
    isPending: false,
  }),
  useObserverCampaigns: () => ({ data: [], isLoading: false }),
  useRefreshObserverCampaigns: () => ({
    mutateAsync: vi.fn().mockResolvedValue([]),
    isPending: false,
  }),
  useSetCampaignAllowlist: () => ({
    mutateAsync: vi.fn().mockResolvedValue(mockObserverData),
    isPending: false,
  }),
  useScanNow: () => ({
    mutateAsync: mockScanNow,
    isPending: false,
  }),
  useRestartObserver: () => ({
    mutateAsync: vi.fn().mockResolvedValue({ status: "ok", channel: "restart" }),
    isPending: false,
  }),
  useStartNewCabinetDay: () => ({
    mutateAsync: vi.fn().mockResolvedValue({ status: "ok", archived_date: "2026-06-08" }),
    isPending: false,
  }),
  useTelegramSettings: () => ({
    data: {
      is_authorized: true,
      poller_status: "ONLINE",
      bot_username: "test_bot",
      auth_deep_link: "https://t.me/test_bot?start=auth",
      activation_command: "/start auth",
      chat_id: "12345",
      web_app_url: null,
    },
    isLoading: false,
    error: null,
    refetch: vi.fn(),
  }),
  useUpdateTelegramToken: () => ({
    mutateAsync: vi.fn().mockResolvedValue({}),
    isPending: false,
  }),
  useDeleteTelegramToken: () => ({
    mutateAsync: vi.fn().mockResolvedValue({}),
    isPending: false,
  }),
  useVisionSettings: () => ({
    data: {
      has_token: true,
      profile_id: "profile_001",
      auto_restart_on_missing_cdp: true,
      runtime_status: "READY",
      runtime_status_message: null,
      cdp_ready: true,
      cdp_port: 9222,
    },
    isLoading: false,
    error: null,
    refetch: vi.fn(),
  }),
  useUpdateVisionSettings: () => ({
    mutateAsync: vi.fn().mockResolvedValue({}),
    isPending: false,
  }),
  useReconnectVision: () => ({
    mutateAsync: vi.fn().mockResolvedValue({ ok: true }),
    isPending: false,
  }),
  useHealthDetails: () => ({
    data: {
      overall: "HEALTHY",
      workers: [
        { name: "observer", status: "ONLINE", last_heartbeat_at: new Date().toISOString() },
        { name: "meta_api", status: "ONLINE", last_heartbeat_at: new Date().toISOString() },
        { name: "telegram_poller", status: "OFFLINE", last_heartbeat_at: null },
      ],
      observer_runtime: null,
    },
    isLoading: false,
    error: null,
    refetch: vi.fn(),
    isFetching: false,
  }),
  useObserverStatus: () => ({
    data: {
      status: "running",
      last_scan_at: new Date().toISOString(),
      interval_seconds: 60,
    },
    isLoading: false,
    error: null,
  }),
}));

// Импортируем компоненты ПОСЛЕ моков
import { Tabs, TabsList, TabsContent, type TabItem } from "@/components/ui/Tabs";
import { ObserverTab } from "@/components/settings/ObserverTab";
import { TelegramTab } from "@/components/settings/TelegramTab";
import { HealthTab } from "@/components/settings/HealthTab";

// ─── Хелперы ──────────────────────────────────────────────────────────────────

function makeQueryClient() {
  return new QueryClient({ defaultOptions: { queries: { retry: false } } });
}

function wrap(ui: React.ReactElement) {
  return (
    <QueryClientProvider client={makeQueryClient()}>{ui}</QueryClientProvider>
  );
}

const TABS: TabItem[] = [
  { value: "observer", label: "Observer" },
  { value: "telegram", label: "Telegram" },
  { value: "health", label: "Health" },
];

/** Settings-страница для тестирования переключения табов. */
function TestSettingsPage() {
  const [tab, setTab] = useState("observer");
  return (
    <Tabs value={tab} onValueChange={setTab}>
      <TabsList items={TABS} />
      <TabsContent value="observer">
        <ObserverTab />
      </TabsContent>
      <TabsContent value="telegram">
        <TelegramTab />
      </TabsContent>
      <TabsContent value="health">
        <HealthTab />
      </TabsContent>
    </Tabs>
  );
}

// ─── Тесты Tabs переключения ─────────────────────────────────────────────────

describe("Settings — переключение табов", () => {
  // По умолчанию открыт таб Observer
  it("первый таб Observer активен по умолчанию", () => {
    render(wrap(<TestSettingsPage />));
    expect(screen.getByRole("tab", { name: "Observer" })).toHaveAttribute(
      "aria-selected",
      "true",
    );
  });

  // Клик на Telegram — переходим на таб Telegram
  it("клик Telegram → контент Telegram виден", async () => {
    const user = userEvent.setup();
    render(wrap(<TestSettingsPage />));
    await user.click(screen.getByRole("tab", { name: "Telegram" }));
    // Telegram tab содержит секцию Статус бота
    await waitFor(() => {
      expect(screen.getByText("Статус бота")).toBeInTheDocument();
    });
  });

  // Keyboard: ArrowRight переходит к следующему табу
  it("ArrowRight переходит к следующему табу", async () => {
    const user = userEvent.setup();
    render(wrap(<TestSettingsPage />));
    screen.getByRole("tab", { name: "Observer" }).focus();
    await user.keyboard("{ArrowRight}");
    expect(screen.getByRole("tab", { name: "Telegram" })).toHaveFocus();
  });
});

// ─── ObserverTab ──────────────────────────────────────────────────────────────

describe("ObserverTab", () => {
  // Отображает switch сканирования
  it("рендерит switch сканирования", () => {
    render(wrap(<ObserverTab />));
    expect(screen.getByRole("switch", { name: "Включить сканирование" })).toBeInTheDocument();
  });

  // Switch в положении ON когда is_scanning_enabled=true
  it("switch ON когда is_scanning_enabled=true", () => {
    render(wrap(<ObserverTab />));
    expect(
      screen.getByRole("switch", { name: "Включить сканирование" }),
    ).toHaveAttribute("aria-checked", "true");
  });

  // Клик на switch вызывает точечный PATCH useToggleScanning (не partial PUT — фикс бага 422)
  it("клик toggle вызывает useToggleScanning с новым значением", async () => {
    const user = userEvent.setup();
    render(wrap(<ObserverTab />));
    await user.click(screen.getByRole("switch", { name: "Включить сканирование" }));
    expect(mockToggleScanning).toHaveBeenCalledWith(false);
  });

  // owner tag и «Сканировать сейчас» вынесены: owner tag → страница «Кампании»,
  // scan-now → главная Панель. В ObserverTab их больше нет.
});

// ─── HealthTab — вердикт HEALTHY/DEGRADED/CRITICAL ───────────────────────────

describe("HealthTab — вердикт", () => {
  // Вердикт HEALTHY отображается как badge
  it("отображает HEALTHY вердикт", () => {
    render(wrap(<HealthTab />));
    expect(screen.getByText("HEALTHY")).toBeInTheDocument();
  });

  // Список воркеров отображается
  it("рендерит список воркеров", () => {
    render(wrap(<HealthTab />));
    // observer, meta_api, telegram_poller — из мока
    expect(screen.getByText("Observer")).toBeInTheDocument();
    expect(screen.getByText("Meta API Worker")).toBeInTheDocument();
  });

  // ONLINE/OFFLINE статусы
  it("показывает ONLINE и OFFLINE статусы", () => {
    render(wrap(<HealthTab />));
    const onlineBadges = screen.getAllByText("ONLINE");
    const offlineBadges = screen.getAllByText("OFFLINE");
    expect(onlineBadges.length).toBeGreaterThan(0);
    expect(offlineBadges.length).toBeGreaterThan(0);
  });

  // Кнопка обновления присутствует
  it("кнопка Refresh присутствует", () => {
    render(wrap(<HealthTab />));
    expect(screen.getByRole("button", { name: "Обновить статус" })).toBeInTheDocument();
  });
});

// ─── TelegramTab ─────────────────────────────────────────────────────────────

describe("TelegramTab", () => {
  // Показывает статус авторизации
  it("отображает статус авторизован", () => {
    render(wrap(<TelegramTab />));
    expect(screen.getByText("Авторизован")).toBeInTheDocument();
  });

  // Показывает username бота
  it("отображает username бота", () => {
    render(wrap(<TelegramTab />));
    expect(screen.getByText("@test_bot")).toBeInTheDocument();
  });

  // Поле токена присутствует
  it("поле Bot Token присутствует", () => {
    render(wrap(<TelegramTab />));
    expect(screen.getByLabelText("Bot Token")).toBeInTheDocument();
  });
});
