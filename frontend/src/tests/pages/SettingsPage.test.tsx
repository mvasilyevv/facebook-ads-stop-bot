/**
 * Тесты Settings-страницы:
 * - Tabs переключение (Observer / Telegram)
 * - ObserverTab: toggle scanning
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
const mockToggleScanning = vi.fn().mockResolvedValue(mockObserverData);
const mockToggleAutoEnable = vi.fn().mockResolvedValue(mockObserverData);
const mockCreateOwnerInvite = vi.fn().mockResolvedValue({
  code: "OWNER123",
  expires_at: "2026-07-17T08:00:00Z",
  role: "owner",
  auth_deep_link: "https://t.me/test_bot?start=OWNER123",
  activation_command: "/start OWNER123",
});

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
  useAutoEnableExclusions: () => ({ data: [], isLoading: false, error: null }),
  useRemoveAutoEnableExclusion: () => ({ mutateAsync: vi.fn(), isPending: false }),
  useObserverCampaigns: () => ({ data: [], isLoading: false }),
  useRefreshObserverCampaigns: () => ({
    mutateAsync: vi.fn().mockResolvedValue([]),
    isPending: false,
  }),
  useSetCampaignAllowlist: () => ({
    mutateAsync: vi.fn().mockResolvedValue(mockObserverData),
    isPending: false,
  }),
  useTelegramSettings: () => ({
    data: {
      is_authorized: true,
      bot_username: "test_bot",
      auth_deep_link: "https://t.me/test_bot?start=OWNER123",
      activation_command: "/start OWNER123",
      auth_invite_expires_at: "2026-07-17T08:00:00Z",
      web_app_url: null,
    },
    isLoading: false,
    error: null,
    refetch: vi.fn(),
  }),
  useCreateTelegramOwnerInvite: () => ({
    mutateAsync: mockCreateOwnerInvite,
    isPending: false,
  }),
  useTelegramNotificationDiagnostics: () => ({
    data: {
      as_of: "2026-07-21T10:00:00Z",
      webhook_state: "configured",
      gateway_state: "configured",
      outbox_state: "idle",
      last_webhook_update_at: null,
      inbox_counts: {},
      delivery_counts: {},
      command_reply_counts: {},
      oldest_pending_at: null,
      active_recipients: 1,
      enabled_recipients: 1,
      auth_incident_active: false,
      recent_errors: [],
    },
    isLoading: false,
    isError: false,
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
      channel_status: "READY",
      channel_message: null,
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
}));

// Импортируем компоненты ПОСЛЕ моков
import { Tabs, TabsList, TabsContent, type TabItem } from "@/components/ui/Tabs";
import { ObserverTab } from "@/components/settings/ObserverTab";
import { TelegramTab } from "@/components/settings/TelegramTab";

// ─── Хелперы ──────────────────────────────────────────────────────────────────

function makeQueryClient() {
  return new QueryClient({ defaultOptions: { queries: { retry: false } } });
}

function wrap(ui: React.ReactElement) {
  return <QueryClientProvider client={makeQueryClient()}>{ui}</QueryClientProvider>;
}

const TABS: TabItem[] = [
  { value: "observer", label: "Observer" },
  { value: "telegram", label: "Telegram" },
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
    </Tabs>
  );
}

// ─── Тесты Tabs переключения ─────────────────────────────────────────────────

describe("Settings — переключение табов", () => {
  // По умолчанию открыт таб Observer
  it("первый таб Observer активен по умолчанию", () => {
    render(wrap(<TestSettingsPage />));
    expect(screen.getByRole("tab", { name: "Observer" })).toHaveAttribute("aria-selected", "true");
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
    expect(screen.getByRole("switch", { name: "Включить сканирование" })).toHaveAttribute(
      "aria-checked",
      "true",
    );
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

// ─── TelegramTab ─────────────────────────────────────────────────────────────

describe("TelegramTab", () => {
  // Показывает статус авторизации
  it("отображает настроенный токен без false-green", () => {
    render(wrap(<TelegramTab />));
    expect(screen.getAllByText("Настроен").length).toBeGreaterThan(0);
  });

  // Показывает username бота
  it("отображает username бота", () => {
    render(wrap(<TelegramTab />));
    expect(screen.getByText("@test_bot")).toBeInTheDocument();
  });

  it("показывает webhook, gateway и outbox вместо retired poller", () => {
    render(wrap(<TelegramTab />));
    expect(screen.getByText("Webhook")).toBeInTheDocument();
    expect(screen.getByText("Gateway")).toBeInTheDocument();
    expect(screen.getByText("Outbox")).toBeInTheDocument();
    expect(screen.getByText("Очередь пуста")).toBeInTheDocument();
    expect(screen.queryByText("Poller")).not.toBeInTheDocument();
  });

  it("показывает owner-код в ссылке и команде", () => {
    render(wrap(<TelegramTab />));
    expect(screen.getByText("/start OWNER123")).toBeInTheDocument();
    expect(screen.getByRole("link", { name: "Открыть в Telegram" })).toHaveAttribute(
      "href",
      "https://t.me/test_bot?start=OWNER123",
    );
  });

  // Поле токена присутствует
  it("поле Bot Token присутствует", () => {
    render(wrap(<TelegramTab />));
    expect(screen.getByLabelText("Bot Token")).toBeInTheDocument();
  });
});
