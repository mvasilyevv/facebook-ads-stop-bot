import type { ComponentType } from "react";
import { fireEvent, render, screen } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";

import type { TelegramSettings } from "@fb/shared";

const routeState = vi.hoisted(() => ({
  section: undefined as
    | "display"
    | "observer"
    | "telegram"
    | "vision"
    | undefined,
  role: "owner",
}));
const mockNavigate = vi.hoisted(() => vi.fn());

vi.mock("@tanstack/react-router", () => ({
  createFileRoute: () => (options: { component: ComponentType }) => ({
    ...options,
    useSearch: () => ({ section: routeState.section }),
  }),
  useNavigate: () => mockNavigate,
}));

vi.mock("@/lib/tg", () => ({
  haptic: { selection: vi.fn(), notify: vi.fn(), impact: vi.fn() },
}));

vi.mock("@/lib/auth", () => ({
  getStoredRole: () => routeState.role,
}));

vi.mock("@/components/layout/MiniHeader", () => ({
  MiniHeader: ({ title }: { title: string }) => <header>{title}</header>,
}));

const telegram: TelegramSettings = {
  is_authorized: true,
  bot_username: "fb_stop_bot",
  auth_deep_link: null,
  activation_command: null,
  auth_invite_expires_at: null,
  web_app_url: "https://t.me/fb_stop_bot/app",
};

const mockUseObserverSettings = vi.fn();
const mockUseTelegramSettings = vi.fn();
const mockUseTelegramNotificationDiagnostics = vi.fn();
const mockUseVisionSettings = vi.fn();
const mockUseOperatorDisplayPreference = vi.fn();

vi.mock("@/lib/api", () => ({
  useOperatorDisplayPreference: (enabled?: boolean) =>
    mockUseOperatorDisplayPreference(enabled),
  useObserverSettings: () => mockUseObserverSettings(),
  useTelegramSettings: () => mockUseTelegramSettings(),
  useTelegramNotificationDiagnostics: () =>
    mockUseTelegramNotificationDiagnostics(),
  useVisionSettings: () => mockUseVisionSettings(),
}));

vi.mock("@/lib/settingsApi", () => ({
  useOperatorDisplayPreference: (enabled?: boolean) =>
    mockUseOperatorDisplayPreference(enabled),
  useUpdateOperatorDisplayPreference: () => ({
    mutate: vi.fn(),
    isPending: false,
    isError: false,
    error: null,
  }),
}));

import { Route } from "@/routes/settings/index";

const SettingsPage = (Route as unknown as { component: ComponentType })
  .component;

describe("TMA settings route", () => {
  beforeEach(() => {
    vi.clearAllMocks();
    routeState.section = undefined;
    routeState.role = "owner";
    globalThis.localStorage.clear();
    mockUseObserverSettings.mockReturnValue({
      data: {
        is_scanning_enabled: true,
        default_interval_seconds: 60,
        owner_campaign_tag: "MV",
        campaign_ids: [],
      },
      isLoading: false,
      isError: false,
      error: null,
    });
    mockUseTelegramSettings.mockReturnValue({
      data: telegram,
      isLoading: false,
      isError: false,
      error: null,
    });
    mockUseTelegramNotificationDiagnostics.mockReturnValue({
      data: {
        as_of: "2026-07-21T10:00:00Z",
        webhook_state: "configured",
        gateway_state: "configured",
        outbox_state: "idle",
        inbox_counts: {},
        delivery_counts: {},
        command_reply_counts: {},
        active_recipients: 1,
        enabled_recipients: 1,
        auth_incident_active: false,
        recent_errors: [],
      },
      isLoading: false,
      isError: false,
      error: null,
    });
    mockUseVisionSettings.mockReturnValue({
      data: {
        has_token: true,
        profile_id: "profile-123",
        channel_status: "READY",
        channel_message: null,
        browser_contract_compatible: true,
      },
      isLoading: false,
      isError: false,
      error: null,
    });
    mockUseOperatorDisplayPreference.mockReturnValue({
      data: {
        timezone_name: "Europe/Kaliningrad",
        updated_at: "2026-08-09T10:00:00Z",
      },
      isPending: false,
      isError: false,
      error: null,
    });
  });

  it("exposes complete platform-native settings without web-only copy", () => {
    render(<SettingsPage />);

    expect(screen.getByText("Ещё")).toBeInTheDocument();
    expect(
      screen.getByRole("button", { name: /Отображение/i }),
    ).toBeInTheDocument();
    expect(
      screen.getByRole("button", { name: /Observer/i }),
    ).toBeInTheDocument();
    expect(
      screen.getByRole("button", { name: /Telegram/i }),
    ).toBeInTheDocument();
    expect(
      screen.getByRole("button", { name: /Vision и desktop/i }),
    ).toBeInTheDocument();
    expect(screen.queryByText(/web-панел/i)).not.toBeInTheDocument();
    expect(screen.queryByText(/редкие настройки/i)).not.toBeInTheDocument();
  });

  it("keeps section state in the typed settings URL", () => {
    render(<SettingsPage />);
    fireEvent.click(screen.getByRole("button", { name: /Observer/i }));
    expect(mockNavigate).toHaveBeenCalledWith({
      to: "/settings",
      search: { section: "observer" },
    });
  });

  it("renders a fail-closed owner gate for notification-only recipients", () => {
    routeState.role = "recipient";
    render(<SettingsPage />);
    expect(screen.getByText(/доступен только для чтения/i)).toBeInTheDocument();
    expect(screen.getByText("Только owner")).toBeInTheDocument();
    expect(mockUseOperatorDisplayPreference).toHaveBeenCalledWith(false);
  });

  it("keeps primary navigation and settings controls at least 44px", () => {
    render(<SettingsPage />);
    for (const label of [
      "Отображение",
      "Observer",
      "Telegram",
      "Vision и desktop",
      "Рабочий стол",
      "Аналитика",
      "Источники и воркеры",
      "Запуски кампаний",
      "Офферы",
    ]) {
      expect(
        screen.getByRole("button", { name: new RegExp(label, "i") }).className,
      ).toMatch(/min-h-(?:11|\[64px\])/);
    }
  });
});
