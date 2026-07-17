import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import type { ComponentType } from "react";
import { afterEach, describe, expect, it, vi } from "vitest";

vi.mock("@tanstack/react-router", () => ({
  createFileRoute: () => (options: { component: ComponentType }) => options,
}));

import { Route } from "@/routes/remote-desktop/index";

const RemoteDesktopPage = (Route as unknown as { component: ComponentType }).component;

describe("RemoteDesktopPage", () => {
  afterEach(() => {
    vi.restoreAllMocks();
  });

  it("показывает один защищённый сценарий подключения", () => {
    render(<RemoteDesktopPage />);

    expect(screen.getByText("Защищённый доступ")).toBeInTheDocument();
    expect(screen.getByText(/одноразовый билет/i)).toBeInTheDocument();
    expect(screen.getAllByRole("button")).toHaveLength(1);
    expect(screen.queryByText("Открыть отдельно")).not.toBeInTheDocument();
    expect(screen.queryByText("Попробовать внутри")).not.toBeInTheDocument();
    expect(screen.queryByTitle(/встроенный режим/i)).not.toBeInTheDocument();
  });

  it("получает билет фоном и открывает сразу desktop host", async () => {
    const user = userEvent.setup();
    const replace = vi.fn();
    const popup = {
      close: vi.fn(),
      location: { replace },
      opener: window,
    } as unknown as Window;
    vi.spyOn(window, "open").mockReturnValue(popup);
    vi.spyOn(window, "fetch").mockResolvedValue(
      new Response(
        JSON.stringify({
          url: "https://desktop.adpulse.su/desktop-auth/redeem?ticket=single-use",
        }),
        { status: 200, headers: { "Content-Type": "application/json" } },
      ),
    );
    render(<RemoteDesktopPage />);

    await user.click(screen.getByRole("button", { name: "Подключиться" }));

    expect(window.open).toHaveBeenCalledWith("about:blank", "_blank");
    expect(window.fetch).toHaveBeenCalledWith(
      "/auth/desktop/session",
      expect.objectContaining({ method: "POST", credentials: "same-origin" }),
    );
    expect(replace).toHaveBeenCalledWith(
      "https://desktop.adpulse.su/desktop-auth/redeem?ticket=single-use",
    );
    expect(popup.opener).toBeNull();
  });

  it("закрывает пустую вкладку при небезопасном адресе", async () => {
    const user = userEvent.setup();
    const close = vi.fn();
    vi.spyOn(window, "open").mockReturnValue({
      close,
      location: { replace: vi.fn() },
      opener: window,
    } as unknown as Window);
    vi.spyOn(window, "fetch").mockResolvedValue(
      new Response(JSON.stringify({ url: "https://example.com/steal" }), {
        status: 200,
        headers: { "Content-Type": "application/json" },
      }),
    );
    render(<RemoteDesktopPage />);

    await user.click(screen.getByRole("button", { name: "Подключиться" }));

    expect(close).toHaveBeenCalledOnce();
    expect(await screen.findByRole("alert")).toHaveTextContent(/небезопасный адрес/i);
  });
});
