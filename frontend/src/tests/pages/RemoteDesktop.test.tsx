import { render, screen, waitFor } from "@testing-library/react";
import type { ComponentType } from "react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

const { apiSend } = vi.hoisted(() => ({ apiSend: vi.fn() }));

vi.mock("@/lib/api/client", () => ({ apiSend }));

vi.mock("@tanstack/react-router", () => ({
  createFileRoute: () => (options: { component: ComponentType }) => options,
}));

import { desktopNavigation, Route } from "@/routes/remote-desktop/index";

const RemoteDesktopPage = (Route as unknown as { component: ComponentType }).component;

// Переводит jsdom в режим открытой вкладки запуска (?launch=1).
const enterLaunchMode = () => {
  window.history.replaceState({}, "", "/remote-desktop?launch=1");
};

describe("RemoteDesktopPage", () => {
  beforeEach(() => {
    apiSend.mockReset();
    window.history.replaceState({}, "", "/remote-desktop");
  });

  afterEach(() => {
    vi.restoreAllMocks();
    window.history.replaceState({}, "", "/");
  });

  // Обычный режим: кнопка — настоящая ссылка в новую вкладку, никаких запросов
  // на клик (браузер сам открывает вкладку, запуск идёт уже внутри неё).
  it("показывает ссылку-подключение в новой вкладке без запроса на клик", () => {
    render(<RemoteDesktopPage />);

    const link = screen.getByRole("link", { name: /Подключиться/ });
    expect(link).toHaveAttribute("href", "/remote-desktop?launch=1");
    expect(link).toHaveAttribute("target", "_blank");
    expect(link).toHaveAttribute("rel", "noopener noreferrer");
    expect(screen.getByText(/в новой вкладке/i)).toBeInTheDocument();
    expect(apiSend).not.toHaveBeenCalled();
  });

  // Режим запуска: на монтировании получает билет и заменяет адрес вкладки.
  it("в режиме запуска получает билет и заменяет адрес вкладки", async () => {
    enterLaunchMode();
    const replace = vi
      .spyOn(desktopNavigation, "replace")
      .mockImplementation(() => undefined);
    apiSend.mockResolvedValue({
      url: "https://desktop.adpulse.su/desktop-auth/redeem?ticket=single-use",
      expires_at: "2026-07-17T12:00:00Z",
      transport: "kasm",
    });

    render(<RemoteDesktopPage />);

    await waitFor(() => {
      expect(apiSend).toHaveBeenCalledWith("POST", "/desktop/launch");
      expect(replace).toHaveBeenCalledWith(
        "https://desktop.adpulse.su/desktop-auth/redeem?ticket=single-use",
      );
    });
  });

  // Режим запуска, ошибка API: показывает сообщение и ссылку «Повторить».
  it("в режиме запуска показывает ошибку и предлагает повторить", async () => {
    enterLaunchMode();
    apiSend.mockRejectedValue(new Error("Доступ к рабочему столу запрещён."));

    render(<RemoteDesktopPage />);

    expect(await screen.findByRole("alert")).toHaveTextContent("Доступ к рабочему столу запрещён.");
    const retry = screen.getByRole("link", { name: /Повторить/ });
    expect(retry).toHaveAttribute("href", "/remote-desktop?launch=1");
  });

  // Режим запуска, чужой origin в билете: адрес не заменяется, показывается ошибка.
  it("в режиме запуска не переходит по URL вне production desktop origin", async () => {
    enterLaunchMode();
    const replace = vi
      .spyOn(desktopNavigation, "replace")
      .mockImplementation(() => undefined);
    apiSend.mockResolvedValue({
      url: "https://evil.example/desktop-auth/redeem?ticket=stolen",
      expires_at: "2026-07-17T12:00:00Z",
      transport: "kasm",
    });

    render(<RemoteDesktopPage />);

    expect(await screen.findByRole("alert")).toHaveTextContent("некорректный билет");
    expect(replace).not.toHaveBeenCalled();
  });
});
