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
  owner_campaign_tag: "MV",
  act_via_api: true,
  campaign_ids: [],
  am_columns: ["name", "spend"],
  am_columns_use_default: true,
  am_column_options: [
    { id: "name", label: "Название" },
    { id: "spend", label: "Затраты" },
  ],
  warning_percent_of_stop: null,
  cpc_warning_percent: null,
  cpl_warning_percent: null,
  cpr_warning_percent: null,
};

const mockToggleScanning = vi.fn().mockResolvedValue(mockObserverData);
const mockUpdateObserverInterval = vi.fn().mockResolvedValue(mockObserverData);
const mockScanNow = vi.fn().mockResolvedValue({ status: "queued" });
const mockUpdateAdsManagerColumns = vi.fn().mockResolvedValue(mockObserverData);
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
  useToggleScanning: () => ({
    mutateAsync: mockToggleScanning,
    isPending: false,
  }),
  useUpdateObserverInterval: () => ({
    mutateAsync: mockUpdateObserverInterval,
    isPending: false,
  }),
  useUpdateOwnerTag: () => ({
    mutateAsync: vi.fn().mockResolvedValue(mockObserverData),
    isPending: false,
  }),
  useScanObserverNow: () => ({
    mutateAsync: mockScanNow,
    isPending: false,
  }),
  useObserverCampaigns: () => ({
    data: [],
    isLoading: false,
    isError: false,
    error: null,
    refetch: vi.fn(),
  }),
  useRefreshObserverCampaigns: () => ({
    mutateAsync: vi.fn().mockResolvedValue([]),
    isPending: false,
  }),
  useSetCampaignAllowlist: () => ({
    mutateAsync: vi.fn().mockResolvedValue(mockObserverData),
    isPending: false,
  }),
  useUpdateAdsManagerColumns: () => ({
    mutateAsync: mockUpdateAdsManagerColumns,
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
  useUpdateTelegramWebAppUrl: () => ({
    mutateAsync: vi.fn().mockResolvedValue({}),
    isPending: false,
  }),
  useTelegramRecipients: () => ({
    data: { recipients: [], total: 0 },
    isLoading: false,
    isError: false,
    error: null,
    refetch: vi.fn(),
  }),
  useCreateTelegramRecipientInvite: () => ({
    mutateAsync: vi.fn().mockResolvedValue({}),
    isPending: false,
  }),
  useTelegramRecipientPreferences: () => ({
    data: undefined,
    isLoading: false,
    isError: false,
    error: null,
    refetch: vi.fn(),
  }),
  useUpdateTelegramRecipientPreferences: () => ({
    mutateAsync: vi.fn().mockResolvedValue({}),
    isPending: false,
  }),
  useDeleteTelegramRecipient: () => ({
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
    expect(
      screen.getByRole("switch", { name: "Остановить периодическое сканирование" }),
    ).toBeInTheDocument();
  });

  // Switch в положении ON когда is_scanning_enabled=true
  it("switch ON когда is_scanning_enabled=true", () => {
    render(wrap(<ObserverTab />));
    expect(
      screen.getByRole("switch", { name: "Остановить периодическое сканирование" }),
    ).toHaveAttribute("aria-checked", "true");
  });

  // Выключение снимает защитный контур с кабинетов — не одним кликом (#343).
  it("клик toggle открывает подтверждение и не выключает сканирование сразу", async () => {
    const user = userEvent.setup();
    render(wrap(<ObserverTab />));
    await user.click(screen.getByRole("switch", { name: "Остановить периодическое сканирование" }));

    expect(mockToggleScanning).not.toHaveBeenCalled();
    expect(
      screen.getByText("Авто-стоп перестанет следить за кабинетами до включения."),
    ).toBeInTheDocument();
  });

  // Клик на switch → confirm вызывает точечный PATCH useToggleScanning (не partial PUT — фикс бага 422)
  it("подтверждение диалога вызывает useToggleScanning с новым значением", async () => {
    const user = userEvent.setup();
    render(wrap(<ObserverTab />));
    await user.click(screen.getByRole("switch", { name: "Остановить периодическое сканирование" }));
    await user.click(screen.getByRole("button", { name: "Выключить сканирование" }));
    expect(mockToggleScanning).toHaveBeenCalledWith(false);
  });

  // Включение возвращает защитный контур — остаётся одним кликом, без подтверждения.
  it("включение сканирования не требует подтверждения", async () => {
    mockObserverData.is_scanning_enabled = false;
    const user = userEvent.setup();
    try {
      render(wrap(<ObserverTab />));
      await user.click(
        screen.getByRole("switch", { name: "Включить периодическое сканирование" }),
      );
      expect(mockToggleScanning).toHaveBeenCalledWith(true);
      expect(screen.queryByRole("dialog")).not.toBeInTheDocument();
    } finally {
      mockObserverData.is_scanning_enabled = true;
    }
  });

  it("валидирует интервал и показывает честный queued scan lifecycle", async () => {
    const user = userEvent.setup();
    render(wrap(<ObserverTab />));

    await user.click(screen.getByRole("button", { name: /поставить scan в очередь/i }));
    expect(mockScanNow).toHaveBeenCalledTimes(1);

    await user.clear(screen.getByLabelText("Интервал сканирования в секундах"));
    await user.type(screen.getByLabelText("Интервал сканирования в секундах"), "20");
    await user.click(screen.getByRole("button", { name: "Сохранить интервал" }));
    expect(screen.getByText(/от 30 до 600 секунд/i)).toBeInTheDocument();
    expect(mockUpdateObserverInterval).not.toHaveBeenCalled();
  });

  it("показывает читаемые колонки и сбрасывает настройку к дефолту", async () => {
    const user = userEvent.setup();
    render(wrap(<ObserverTab />));

    expect(screen.getByRole("checkbox", { name: "Название" })).toBeChecked();
    expect(screen.getByRole("checkbox", { name: "Затраты" })).toBeChecked();
    expect(screen.queryByText(/columns=name/i)).not.toBeInTheDocument();
    expect(screen.getByText("Fallback browser-agent")).toBeInTheDocument();
    expect(screen.getByText(/точное env-переопределение API не видит/i)).toBeInTheDocument();

    await user.click(screen.getByRole("button", { name: /сбросить к дефолту/i }));
    expect(mockUpdateAdsManagerColumns).toHaveBeenCalledWith(null);
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

  it("показывает Mini App и управление получателями без desktop-only разрыва", () => {
    render(wrap(<TelegramTab />));
    expect(screen.getByLabelText("HTTPS URL")).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Пригласить получателя" })).toBeInTheDocument();
    expect(screen.queryByText(/только в web/i)).not.toBeInTheDocument();
  });
});
