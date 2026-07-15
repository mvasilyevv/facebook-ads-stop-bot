import { fireEvent, render, screen } from "@testing-library/react";
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

  it("делает внешний вход основным и не обещает ложный Protected", () => {
    render(<RemoteDesktopPage />);

    expect(screen.getByText("Требуется вход")).toBeInTheDocument();
    expect(screen.queryByText("Protected")).not.toBeInTheDocument();
    expect(screen.queryByTitle(/встроенный режим/i)).not.toBeInTheDocument();
  });

  it("показывает iframe только после явного выбора пользователя", () => {
    render(<RemoteDesktopPage />);
    fireEvent.click(screen.getByRole("button", { name: "Попробовать внутри" }));

    expect(screen.getByTitle("Vision Desktop — встроенный режим")).toBeInTheDocument();
    expect(screen.getByText(/не может проверить Basic Auth/i)).toBeInTheDocument();
  });

  it("открывает удалённый рабочий стол в отдельном окне", () => {
    const open = vi.spyOn(window, "open").mockImplementation(() => null);
    render(<RemoteDesktopPage />);
    fireEvent.click(screen.getByRole("button", { name: "Подключиться" }));

    expect(open).toHaveBeenCalledWith(
      "https://desktop.adpulse.su",
      "_blank",
      "noopener,noreferrer",
    );
  });
});
