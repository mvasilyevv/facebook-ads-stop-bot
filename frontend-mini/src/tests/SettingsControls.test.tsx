import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";

const api = vi.hoisted(() => ({
  observerData: {
    is_scanning_enabled: true,
    default_interval_seconds: 60,
    owner_campaign_tag: "MV",
    campaign_ids: ["campaign-1"],
    am_columns: ["name", "spend"],
    am_columns_use_default: false,
    am_column_options: [
      { id: "name", label: "Название" },
      { id: "spend", label: "Затраты" },
    ],
  },
  preferenceData: {
    recipient_id: "00000000-0000-0000-0000-000000000001",
    timezone: "Europe/Kaliningrad",
    min_severity: "warning" as const,
    quiet_hours_start: null,
    quiet_hours_end: null,
    digest_local_time: null,
    categories: {},
    is_enabled: true,
    updated_at: null,
  },
  toggleScanning: vi.fn(),
  updateObserverInterval: vi.fn(),
  updateOwnerTag: vi.fn(),
  setAllowlist: vi.fn(),
  updateAdsManagerColumns: vi.fn(),
  refreshCampaigns: vi.fn(),
  scanNow: vi.fn(),
  updateToken: vi.fn(),
  deleteToken: vi.fn(),
  updateWebAppUrl: vi.fn(),
  createOwnerInvite: vi.fn(),
  createRecipientInvite: vi.fn(),
  deleteRecipient: vi.fn(),
  updatePreferences: vi.fn(),
  updateVision: vi.fn(),
  reconnectVision: vi.fn(),
  updateDisplayPreference: vi.fn(),
  readDisplayPreference: vi.fn(),
}));

vi.mock("@/lib/tg", () => ({
  haptic: { notify: vi.fn(), impact: vi.fn(), selection: vi.fn() },
}));

vi.mock("@/lib/api", () => ({
  useObserverSettings: () => ({
    data: api.observerData,
    isLoading: false,
    isError: false,
    error: null,
    refetch: vi.fn(),
  }),
  useToggleObserverScanning: () => ({
    mutateAsync: api.toggleScanning,
    isPending: false,
  }),
  useUpdateObserverInterval: () => ({
    mutateAsync: api.updateObserverInterval,
    isPending: false,
  }),
  useUpdateObserverOwnerTag: () => ({
    mutateAsync: api.updateOwnerTag,
    isPending: false,
  }),
  useSetObserverCampaignAllowlist: () => ({
    mutateAsync: api.setAllowlist,
    isPending: false,
  }),
  useUpdateAdsManagerColumns: () => ({
    mutateAsync: api.updateAdsManagerColumns,
    isPending: false,
  }),
  useRefreshObserverCampaigns: () => ({
    mutateAsync: api.refreshCampaigns,
    isPending: false,
  }),
  useScanObserverNow: () => ({ mutateAsync: api.scanNow, isPending: false }),
  useObserverCampaigns: () => ({
    data: [{ id: "campaign-1", name: "MV · GH · campaign", selected: true }],
    isLoading: false,
    isError: false,
    error: null,
    refetch: vi.fn(),
  }),
  useTelegramSettings: () => ({
    data: {
      is_authorized: true,
      bot_username: "fb_agent_bot",
      auth_deep_link: null,
      activation_command: null,
      auth_invite_expires_at: null,
      web_app_url: "https://agent.example/app",
    },
    isLoading: false,
    isError: false,
    error: null,
    refetch: vi.fn(),
  }),
  useTelegramNotificationDiagnostics: () => ({
    data: {
      webhook_state: "configured",
      gateway_state: "configured",
      outbox_state: "idle",
      inbox_counts: {},
      delivery_counts: {},
      command_reply_counts: {},
    },
    isLoading: false,
    isError: false,
    error: null,
  }),
  useTelegramRecipients: () => ({
    data: {
      recipients: [
        {
          id: "00000000-0000-0000-0000-000000000001",
          chat_id: 123,
          username: "owner",
          role: "owner",
          created_at: "2026-08-01T00:00:00Z",
        },
      ],
      total: 1,
    },
    isLoading: false,
    isError: false,
    error: null,
    refetch: vi.fn(),
  }),
  useTelegramRecipientPreferences: () => ({
    data: api.preferenceData,
    isLoading: false,
    isError: false,
    error: null,
    refetch: vi.fn(),
  }),
  useUpdateTelegramToken: () => ({
    mutateAsync: api.updateToken,
    isPending: false,
  }),
  useDeleteTelegramToken: () => ({
    mutateAsync: api.deleteToken,
    isPending: false,
  }),
  useUpdateTelegramWebAppUrl: () => ({
    mutateAsync: api.updateWebAppUrl,
    isPending: false,
  }),
  useCreateTelegramOwnerInvite: () => ({
    mutateAsync: api.createOwnerInvite,
    isPending: false,
  }),
  useCreateTelegramRecipientInvite: () => ({
    mutateAsync: api.createRecipientInvite,
    isPending: false,
  }),
  useDeleteTelegramRecipient: () => ({
    mutateAsync: api.deleteRecipient,
    isPending: false,
  }),
  useUpdateTelegramRecipientPreferences: () => ({
    mutateAsync: api.updatePreferences,
    isPending: false,
  }),
  useVisionSettings: () => ({
    data: {
      has_token: true,
      profile_id: "profile-1",
      channel_status: "READY",
      browser_contract_compatible: true,
    },
    isLoading: false,
    isError: false,
    error: null,
    refetch: vi.fn(),
  }),
  useUpdateVisionSettings: () => ({
    mutateAsync: api.updateVision,
    isPending: false,
  }),
  useReconnectVision: () => ({
    mutateAsync: api.reconnectVision,
    isPending: false,
  }),
}));

vi.mock("@/lib/settingsApi", () => ({
  useOperatorDisplayPreference: (enabled?: boolean) => {
    api.readDisplayPreference(enabled);
    return {
      data: {
        timezone_name: "Europe/Kaliningrad",
        updated_at: "2026-08-09T10:00:00Z",
      },
      isPending: false,
      isError: false,
      error: null,
      refetch: vi.fn(),
    };
  },
  useUpdateOperatorDisplayPreference: () => ({
    mutate: api.updateDisplayPreference,
    isPending: false,
    isError: false,
    error: null,
  }),
}));

import { ObserverSettings } from "@/features/settings/ObserverSettings";
import { DisplaySettings } from "@/features/settings/DisplaySettings";
import { TelegramRecipientPreferences } from "@/features/settings/TelegramRecipientPreferences";
import { TelegramSettings } from "@/features/settings/TelegramSettings";

describe("TMA settings controls", () => {
  beforeEach(() => {
    vi.clearAllMocks();
    globalThis.localStorage.clear();
    api.observerData.am_columns_use_default = false;
    for (const mutation of Object.values(api)) {
      if (typeof mutation === "function")
        mutation.mockResolvedValue({ status: "queued" });
    }
  });

  it("persists a validated display timezone through the shared server preference", () => {
    render(<DisplaySettings canEdit />);
    const input = screen.getByLabelText("IANA timezone");
    fireEvent.change(input, { target: { value: "Europe/London" } });
    fireEvent.click(screen.getByRole("button", { name: "Сохранить" }));

    expect(api.updateDisplayPreference).toHaveBeenCalledWith(
      { body: { timezone_name: "Europe/London" } },
      expect.objectContaining({ onSuccess: expect.any(Function) }),
    );
    expect(globalThis.localStorage.length).toBe(0);
  });

  it("fails closed for notification-only recipients without reading the owner preference", () => {
    render(<DisplaySettings canEdit={false} />);

    expect(api.readDisplayPreference).toHaveBeenCalledWith(false);
    expect(api.updateDisplayPreference).not.toHaveBeenCalled();
    expect(screen.queryByLabelText("IANA timezone")).not.toBeInTheDocument();
    expect(screen.getByText(/доступна только владельцу/i)).toBeInTheDocument();
  });

  it("treats scan-now as queued and validates interval before mutation", async () => {
    render(<ObserverSettings canEdit />);

    fireEvent.click(
      screen.getByRole("button", { name: /поставить scan в очередь/i }),
    );
    await waitFor(() => expect(api.scanNow).toHaveBeenCalledTimes(1));
    expect(
      screen.getByText(/это ещё не завершённое сканирование/i),
    ).toBeInTheDocument();

    const interval = screen.getByLabelText("Интервал, секунд");
    fireEvent.change(interval, { target: { value: "20" } });
    fireEvent.click(screen.getByRole("button", { name: "Сохранить интервал" }));
    expect(await screen.findByText(/от 30 до 600 секунд/i)).toBeInTheDocument();
    expect(api.updateObserverInterval).not.toHaveBeenCalled();
  });

  it("disables Observer mutations for a notification-only recipient", () => {
    render(<ObserverSettings canEdit={false} />);
    expect(
      screen.getByRole("switch", { name: /периодическое сканирование/i }),
    ).toBeDisabled();
    expect(
      screen.getByRole("button", { name: /поставить scan/i }),
    ).toBeDisabled();
  });

  it("редактирует колонки чекбоксами и явно сбрасывает default", async () => {
    api.updateAdsManagerColumns.mockResolvedValueOnce({
      ...api.observerData,
      am_columns_use_default: true,
    });
    render(<ObserverSettings canEdit />);

    expect(screen.getByRole("checkbox", { name: "Название" })).toBeChecked();
    expect(screen.getByRole("checkbox", { name: "Затраты" })).toBeChecked();
    expect(screen.queryByText(/columns=name/i)).not.toBeInTheDocument();

    fireEvent.click(
      screen.getByRole("button", { name: /сбросить к дефолту/i }),
    );
    await waitFor(() =>
      expect(api.updateAdsManagerColumns).toHaveBeenCalledWith(null),
    );
  });

  it("не выдаёт встроенный набор за точное env-переопределение", () => {
    api.observerData.am_columns_use_default = true;

    render(<ObserverSettings canEdit />);

    expect(screen.getByRole("status")).toHaveTextContent(
      "Fallback browser-agent",
    );
    expect(
      screen.getByText(/точное env-переопределение здесь недоступно/i),
    ).toBeInTheDocument();
  });

  it("sanitizes an untrusted Telegram mutation error", async () => {
    api.updateToken.mockRejectedValueOnce(
      new Error(
        "traceback postgres://secret 00000000-0000-0000-0000-000000000099",
      ),
    );
    render(<TelegramSettings canEdit />);

    fireEvent.change(screen.getByLabelText("Новый Bot Token"), {
      target: { value: "123456:SECRET" },
    });
    fireEvent.click(screen.getByRole("button", { name: "Сохранить токен" }));

    expect(await screen.findByText("Токен не сохранён")).toBeInTheDocument();
    expect(
      screen.queryByText(/traceback|postgres|00000000-/i),
    ).not.toBeInTheDocument();
  });

  it("validates recipient timezone before saving preferences", async () => {
    const recipient = {
      id: "00000000-0000-0000-0000-000000000001",
      chat_id: 123,
      username: "owner",
      role: "owner",
      created_at: "2026-08-01T00:00:00Z",
    };
    render(
      <TelegramRecipientPreferences
        recipient={recipient}
        label="@owner"
        canEdit
        onBack={vi.fn()}
      />,
    );

    await waitFor(() =>
      expect(screen.getByLabelText("Timezone")).toHaveValue(
        "Europe/Kaliningrad",
      ),
    );
    fireEvent.change(screen.getByLabelText("Timezone"), {
      target: { value: "Mars/Olympus" },
    });
    fireEvent.click(
      screen.getByRole("button", { name: "Сохранить уведомления" }),
    );

    expect(
      await screen.findByText(/корректный IANA timezone/i),
    ).toBeInTheDocument();
    expect(api.updatePreferences).not.toHaveBeenCalled();
  });
});
