import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import type { ComponentType } from "react";
import { beforeEach, describe, expect, it, vi } from "vitest";

const { post, impact, notify, openLink } = vi.hoisted(() => ({
  post: vi.fn(),
  openLink: vi.fn(),
  impact: vi.fn(),
  notify: vi.fn(),
}));

vi.mock("@tanstack/react-router", () => ({
  createFileRoute: () => (options: { component: ComponentType }) => options,
}));

vi.mock("@/lib/auth", () => ({ tmaFetchApi: { POST: post } }));
vi.mock("@/lib/tg", () => ({
  haptic: { impact, notify },
  openLink,
}));

import { Route } from "@/routes/desktop/index";

const RemoteDesktopPage = (Route as unknown as { component: ComponentType }).component;

describe("Mini App RemoteDesktopPage", () => {
  beforeEach(() => {
    post.mockReset();
    openLink.mockReset();
    impact.mockReset();
    notify.mockReset();
  });

  it("получает Bearer launch через общий API-клиент и открывает URL через Telegram", async () => {
    const user = userEvent.setup();
    post.mockResolvedValue({
      data: { url: "https://desktop.adpulse.su/desktop-auth/redeem?ticket=single-use", expires_at: "2026-07-17T12:00:00Z", transport: "kasm" },
      response: { ok: true },
    });
    render(<RemoteDesktopPage />);

    await user.click(screen.getByRole("button", { name: "Подключиться" }));

    expect(post).toHaveBeenCalledWith("/api/desktop/launch", {
      body: { presentation: "mobile" },
    });
    expect(openLink).toHaveBeenCalledWith(
      "https://desktop.adpulse.su/desktop-auth/redeem?ticket=single-use",
    );
    expect(screen.getAllByRole("button")).toHaveLength(1);
    expect(screen.queryByText(/desktop\.adpulse\.su/i)).not.toBeInTheDocument();
    expect(screen.queryByText(/отдельный вход/i)).not.toBeInTheDocument();
  });

  it("показывает понятную ошибку и оставляет одну кнопку повтора", async () => {
    const user = userEvent.setup();
    post.mockRejectedValue(new Error("Доступ к рабочему столу запрещён."));
    render(<RemoteDesktopPage />);

    await user.click(screen.getByRole("button", { name: "Подключиться" }));

    expect(await screen.findByRole("alert")).toHaveTextContent("Доступ к рабочему столу запрещён.");
    expect(screen.getByRole("button", { name: "Повторить" })).toBeEnabled();
    expect(notify).toHaveBeenCalledWith("error");
  });

  it("не передаёт Telegram URL с чужого origin", async () => {
    const user = userEvent.setup();
    post.mockResolvedValue({
      data: { url: "https://evil.example/desktop-auth/redeem?ticket=stolen", expires_at: "2026-07-17T12:00:00Z", transport: "kasm" },
      response: { ok: true },
    });
    render(<RemoteDesktopPage />);

    await user.click(screen.getByRole("button", { name: "Подключиться" }));

    expect(openLink).not.toHaveBeenCalled();
    expect(await screen.findByRole("alert")).toHaveTextContent("некорректный билет");
  });
});
