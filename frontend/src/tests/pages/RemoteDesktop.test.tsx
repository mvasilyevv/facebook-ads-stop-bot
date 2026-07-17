import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import type { ComponentType } from "react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

const { apiSend } = vi.hoisted(() => ({ apiSend: vi.fn() }));

vi.mock("@/lib/api/client", () => ({ apiSend }));

vi.mock("@tanstack/react-router", () => ({
  createFileRoute: () => (options: { component: ComponentType }) => options,
}));

import { desktopNavigation, Route } from "@/routes/remote-desktop/index";

const RemoteDesktopPage = (Route as unknown as { component: ComponentType }).component;

describe("RemoteDesktopPage", () => {
  beforeEach(() => {
    apiSend.mockReset();
  });

  afterEach(() => {
    vi.restoreAllMocks();
  });

  // Псевдо-вкладка: страница синхронно открывает пустое окно и после ответа API
  // подставляет туда URL билета через location.replace.
  const fakeDesktopWindow = () => {
    const win = {
      opener: {} as unknown,
      close: vi.fn(),
      location: { replace: vi.fn() },
    };
    return win as unknown as Window & { close: ReturnType<typeof vi.fn> } & {
      location: { replace: ReturnType<typeof vi.fn> };
    };
  };

  // Единственный сценарий подключения; текст обещает открытие в новой вкладке.
  it("показывает один защищённый сценарий подключения", () => {
    render(<RemoteDesktopPage />);

    expect(screen.getByText("Защищённый доступ")).toBeInTheDocument();
    expect(screen.getByText(/в новой вкладке/i)).toBeInTheDocument();
    expect(screen.getAllByRole("button")).toHaveLength(1);
    expect(screen.queryByText("Открыть отдельно")).not.toBeInTheDocument();
    expect(screen.queryByText("Попробовать внутри")).not.toBeInTheDocument();
    expect(screen.queryByTitle(/встроенный режим/i)).not.toBeInTheDocument();
  });

  // Happy path: вкладка открывается синхронно (Safari-safe), билет уезжает в неё
  // через location.replace, opener обнуляется, текущая вкладка не трогается.
  it("получает билет и открывает рабочий стол в новой вкладке", async () => {
    const user = userEvent.setup();
    const win = fakeDesktopWindow();
    const openTab = vi.spyOn(desktopNavigation, "openTab").mockReturnValue(win);
    const assign = vi.spyOn(desktopNavigation, "assign").mockImplementation(() => undefined);
    apiSend.mockResolvedValue({
      url: "https://app.adpulse.su/desktop-auth/redeem?ticket=single-use",
      expires_at: "2026-07-17T12:00:00Z",
    });
    render(<RemoteDesktopPage />);

    await user.click(screen.getByRole("button", { name: "Подключиться" }));

    expect(apiSend).toHaveBeenCalledWith("POST", "/desktop/launch");
    expect(openTab).toHaveBeenCalledTimes(1);
    expect(win.location.replace).toHaveBeenCalledWith(
      "https://app.adpulse.su/desktop-auth/redeem?ticket=single-use",
    );
    expect(win.opener).toBeNull();
    expect(win.close).not.toHaveBeenCalled();
    expect(assign).not.toHaveBeenCalled();
  });

  // Попап заблокирован браузером (openTab -> null): билет не пропадает —
  // фолбэк открывает рабочий стол в текущей вкладке.
  it("при заблокированном попапе открывает в текущей вкладке", async () => {
    const user = userEvent.setup();
    vi.spyOn(desktopNavigation, "openTab").mockReturnValue(null);
    const assign = vi.spyOn(desktopNavigation, "assign").mockImplementation(() => undefined);
    apiSend.mockResolvedValue({
      url: "https://app.adpulse.su/desktop-auth/redeem?ticket=single-use",
      expires_at: "2026-07-17T12:00:00Z",
    });
    render(<RemoteDesktopPage />);

    await user.click(screen.getByRole("button", { name: "Подключиться" }));

    expect(assign).toHaveBeenCalledWith(
      "https://app.adpulse.su/desktop-auth/redeem?ticket=single-use",
    );
  });

  // Ошибка API: предоткрытая пустая вкладка закрывается, показывается alert.
  it("показывает одну ошибку, закрывает пустую вкладку и предлагает повторить", async () => {
    const user = userEvent.setup();
    const win = fakeDesktopWindow();
    vi.spyOn(desktopNavigation, "openTab").mockReturnValue(win);
    apiSend.mockRejectedValue(new Error("Доступ к рабочему столу запрещён."));
    render(<RemoteDesktopPage />);

    await user.click(screen.getByRole("button", { name: "Подключиться" }));

    expect(await screen.findByRole("alert")).toHaveTextContent("Доступ к рабочему столу запрещён.");
    expect(win.close).toHaveBeenCalledTimes(1);
    expect(win.location.replace).not.toHaveBeenCalled();
    expect(screen.getByRole("button", { name: "Повторить" })).toBeEnabled();
  });

  // Чужой origin в билете: навигации нет ни в новую, ни в текущую вкладку,
  // предоткрытая вкладка закрывается.
  it("не переходит по URL вне production desktop origin", async () => {
    const user = userEvent.setup();
    const win = fakeDesktopWindow();
    vi.spyOn(desktopNavigation, "openTab").mockReturnValue(win);
    const assign = vi.spyOn(desktopNavigation, "assign").mockImplementation(() => undefined);
    apiSend.mockResolvedValue({
      url: "https://evil.example/desktop-auth/redeem?ticket=stolen",
      expires_at: "2026-07-17T12:00:00Z",
    });
    render(<RemoteDesktopPage />);

    await user.click(screen.getByRole("button", { name: "Подключиться" }));

    expect(assign).not.toHaveBeenCalled();
    expect(win.location.replace).not.toHaveBeenCalled();
    expect(win.close).toHaveBeenCalledTimes(1);
    expect(await screen.findByRole("alert")).toHaveTextContent("некорректный билет");
  });
});
