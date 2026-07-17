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

  it("показывает один защищённый сценарий подключения", () => {
    render(<RemoteDesktopPage />);

    expect(screen.getByText("Защищённый доступ")).toBeInTheDocument();
    expect(screen.getByText(/в этой вкладке/i)).toBeInTheDocument();
    expect(screen.getAllByRole("button")).toHaveLength(1);
    expect(screen.queryByText("Открыть отдельно")).not.toBeInTheDocument();
    expect(screen.queryByText("Попробовать внутри")).not.toBeInTheDocument();
    expect(screen.queryByTitle(/встроенный режим/i)).not.toBeInTheDocument();
  });

  it("получает билет и переходит в текущей вкладке", async () => {
    const user = userEvent.setup();
    const open = vi.spyOn(window, "open");
    const assign = vi.spyOn(desktopNavigation, "assign").mockImplementation(() => undefined);
    apiSend.mockResolvedValue({
      url: "https://app.adpulse.su/desktop-auth/redeem?ticket=single-use",
      expires_at: "2026-07-17T12:00:00Z",
    });
    render(<RemoteDesktopPage />);

    await user.click(screen.getByRole("button", { name: "Подключиться" }));

    expect(apiSend).toHaveBeenCalledWith("POST", "/desktop/launch");
    expect(assign).toHaveBeenCalledWith(
      "https://app.adpulse.su/desktop-auth/redeem?ticket=single-use",
    );
    expect(open).not.toHaveBeenCalled();
  });

  it("показывает одну ошибку и предлагает повторить", async () => {
    const user = userEvent.setup();
    apiSend.mockRejectedValue(new Error("Доступ к рабочему столу запрещён."));
    render(<RemoteDesktopPage />);

    await user.click(screen.getByRole("button", { name: "Подключиться" }));

    expect(await screen.findByRole("alert")).toHaveTextContent("Доступ к рабочему столу запрещён.");
    expect(screen.getByRole("button", { name: "Повторить" })).toBeEnabled();
  });

  it("не переходит по URL вне production desktop origin", async () => {
    const user = userEvent.setup();
    const assign = vi.spyOn(desktopNavigation, "assign").mockImplementation(() => undefined);
    apiSend.mockResolvedValue({
      url: "https://evil.example/desktop-auth/redeem?ticket=stolen",
      expires_at: "2026-07-17T12:00:00Z",
    });
    render(<RemoteDesktopPage />);

    await user.click(screen.getByRole("button", { name: "Подключиться" }));

    expect(assign).not.toHaveBeenCalled();
    expect(await screen.findByRole("alert")).toHaveTextContent("некорректный билет");
  });
});
