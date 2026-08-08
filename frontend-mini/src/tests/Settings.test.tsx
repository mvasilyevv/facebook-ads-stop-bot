import type { ComponentType } from "react";
import { render, screen } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";

import type { TelegramSettings } from "@fb/shared";

vi.mock("@tanstack/react-router", () => ({
  createFileRoute: () => (options: { component: ComponentType }) => options,
  useNavigate: () => vi.fn(),
}));

vi.mock("@/lib/tg", () => ({
  haptic: { selection: vi.fn() },
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

const mockUseTelegramSettings = vi.fn();
const mockUseTelegramNotificationDiagnostics = vi.fn();
const mockUseVisionSettings = vi.fn();

vi.mock("@/lib/api", () => ({
  useTelegramSettings: () => mockUseTelegramSettings(),
  useTelegramNotificationDiagnostics: () =>
    mockUseTelegramNotificationDiagnostics(),
  useVisionSettings: () => mockUseVisionSettings(),
}));

import { Route } from "@/routes/settings/index";

const SettingsPage = (Route as unknown as { component: ComponentType })
  .component;

describe("TMA settings route", () => {
  beforeEach(() => {
    vi.clearAllMocks();
    mockUseTelegramSettings.mockReturnValue({
      data: telegram,
      isLoading: false,
      isError: false,
      error: null,
      refetch: vi.fn(),
    });
    mockUseTelegramNotificationDiagnostics.mockReturnValue({
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
      error: null,
    });
    mockUseVisionSettings.mockReturnValue({
      data: {
        has_token: true,
        profile_id: "profile-123",
        channel_status: "READY",
        channel_message: null,
      },
      isLoading: false,
      isError: false,
      error: null,
      refetch: vi.fn(),
    });
  });

  it("renders the real action-first route and read-only diagnostics", () => {
    render(<SettingsPage />);

    expect(screen.getByText("Ещё")).toBeInTheDocument();
    expect(screen.getByText("@fb_stop_bot")).toBeInTheDocument();
    expect(screen.getByText("Webhook")).toBeInTheDocument();
    expect(screen.getByText("Gateway")).toBeInTheDocument();
    expect(screen.getByText("Outbox")).toBeInTheDocument();
    expect(screen.getByText("profile-123")).toBeInTheDocument();
  });

  it("keeps rare configuration desktop-first", () => {
    render(<SettingsPage />);

    expect(screen.getByText(/редкие настройки/i)).toBeInTheDocument();
    expect(screen.queryByText("Owner Campaign Tag")).not.toBeInTheDocument();
    expect(screen.queryByText("Автостарт кабинета")).not.toBeInTheDocument();
    expect(screen.queryByRole("switch")).not.toBeInTheDocument();
  });

  it("links only to supported secondary screens", () => {
    render(<SettingsPage />);

    for (const label of [
      "Рабочий стол",
      "Аналитика",
      "Источники и воркеры",
      "Запуски кампаний",
      "Офферы",
    ]) {
      expect(screen.getByText(label)).toBeInTheDocument();
    }
    expect(screen.queryByText("Скрипты кампаний")).not.toBeInTheDocument();
  });

});
