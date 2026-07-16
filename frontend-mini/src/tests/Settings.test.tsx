/**
 * Тест SettingsPage: toggle скана, отображение секций.
 */
import { describe, it, expect, vi, beforeEach } from "vitest";
import { render, screen, fireEvent, waitFor } from "@testing-library/react";
import type { ObserverConfig } from "@fb/shared";
import type { TelegramSettings } from "@fb/shared";

// Мок роутера
vi.mock("@tanstack/react-router", () => ({
  createFileRoute: () => ({ component: (c: unknown) => c }),
  useNavigate: () => vi.fn(),
  Link: ({ children, to }: { children: React.ReactNode; to: string }) => (
    <a href={to}>{children}</a>
  ),
}));

// Мок TG
vi.mock("@/lib/tg", () => ({
  haptic: { impact: vi.fn(), notify: vi.fn(), selection: vi.fn() },
  tgConfirm: vi.fn().mockResolvedValue(true),
}));

// Мок MiniHeader
vi.mock("@/components/layout/MiniHeader", () => ({
  MiniHeader: ({ title }: { title: string }) => <header>{title}</header>,
}));

const MOCK_OBSERVER: ObserverConfig = {
  is_scanning_enabled: true,
  auto_enable_recommendations: false,
  default_interval_seconds: 60,
  owner_campaign_tag: null,
};

const MOCK_TELEGRAM: TelegramSettings = {
  is_authorized: true,
  poller_status: "ONLINE",
  bot_username: "fb_stop_bot",
  auth_deep_link: null,
  activation_command: null,
  auth_invite_expires_at: null,
  chat_id: null,
  web_app_url: "https://t.me/fb_stop_bot/app",
};

const mockUseObserverSettings = vi.fn();
const mockUseToggleScanning = vi.fn();
const mockUseTriggerScan = vi.fn();
const mockUseTelegramSettings = vi.fn();
const mockUseVisionSettings = vi.fn();

vi.mock("@/lib/api", () => ({
  useObserverSettings: () => mockUseObserverSettings(),
  useToggleScanning: () => mockUseToggleScanning(),
  useTriggerScan: () => mockUseTriggerScan(),
  useTelegramSettings: () => mockUseTelegramSettings(),
  useVisionSettings: () => mockUseVisionSettings(),
}));

import SettingsTestWrapper from "./Settings.test.helper";

describe("SettingsPage", () => {
  const mutateAsync = vi.fn().mockResolvedValue({});

  beforeEach(() => {
    mutateAsync.mockClear();
    mockUseObserverSettings.mockReturnValue({
      data: MOCK_OBSERVER,
      isLoading: false,
      isError: false,
      refetch: vi.fn(),
    });
    mockUseToggleScanning.mockReturnValue({ mutateAsync, isPending: false });
    mockUseTriggerScan.mockReturnValue({
      mutateAsync: vi.fn().mockResolvedValue({}),
      isPending: false,
    });
    mockUseTelegramSettings.mockReturnValue({
      data: MOCK_TELEGRAM,
      isLoading: false,
      isError: false,
      refetch: vi.fn(),
    });
    mockUseVisionSettings.mockReturnValue({
      data: {
        has_token: true,
        profile_id: "profile-123",
        cdp_ready: true,
        cdp_port: 9222,
        auto_restart_on_missing_cdp: true,
        runtime_status: "ready",
        runtime_status_message: null,
      },
      isLoading: false,
      isError: false,
      refetch: vi.fn(),
    });
  });

  // Observer-секция показывает toggle
  it("показывает секцию Observer с переключателем", () => {
    render(<SettingsTestWrapper />);
    expect(screen.getByText("Сканирование")).toBeInTheDocument();
  });

  // Toggle в позиции "включён" при is_scanning_enabled=true
  it("Switch включён при is_scanning_enabled=true", () => {
    render(<SettingsTestWrapper />);
    const checkbox = screen.getByRole("switch") as HTMLInputElement;
    expect(checkbox.checked).toBe(true);
  });

  // Клик по toggle вызывает useToggleScanning с enabled=false
  it("клик по Switch вызывает toggleScanning с enabled=false", async () => {
    render(<SettingsTestWrapper />);
    const checkbox = screen.getByRole("switch");
    fireEvent.click(checkbox);
    await waitFor(() => {
      expect(mutateAsync).toHaveBeenCalledWith({ enabled: false });
    });
  });

  // Telegram-секция показывает имя бота
  it("показывает username бота из Telegram-настроек", () => {
    render(<SettingsTestWrapper />);
    expect(screen.getByText("@fb_stop_bot")).toBeInTheDocument();
  });

  // Vision-секция показывает profile_id
  it("показывает profile_id из Vision-настроек", () => {
    render(<SettingsTestWrapper />);
    expect(screen.getByText("profile-123")).toBeInTheDocument();
  });

  // Ссылки навигации к вторичным экранам («Ещё»)
  it("показывает кнопки навигации на /health, /scripts, /offers", () => {
    render(<SettingsTestWrapper />);
    expect(screen.getByText("Здоровье воркеров")).toBeInTheDocument();
    expect(screen.getByText("Скрипты кампаний")).toBeInTheDocument();
    expect(screen.getByText("Офферы")).toBeInTheDocument();
  });

  // При toggle=false начальный state
  it("Switch выключен при is_scanning_enabled=false", () => {
    mockUseObserverSettings.mockReturnValue({
      data: { ...MOCK_OBSERVER, is_scanning_enabled: false },
      isLoading: false,
      isError: false,
      refetch: vi.fn(),
    });
    render(<SettingsTestWrapper />);
    const checkbox = screen.getByRole("switch") as HTMLInputElement;
    expect(checkbox.checked).toBe(false);
  });
});
