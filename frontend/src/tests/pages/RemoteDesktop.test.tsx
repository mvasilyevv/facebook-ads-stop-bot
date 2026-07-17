import { render, screen } from "@testing-library/react";
import type { ComponentType } from "react";
import { describe, expect, it, vi } from "vitest";

vi.mock("@tanstack/react-router", () => ({
  createFileRoute: () => (options: { component: ComponentType }) => options,
}));

import { Route } from "@/routes/remote-desktop/index";

const RemoteDesktopPage = (Route as unknown as { component: ComponentType }).component;

describe("RemoteDesktopPage", () => {
  it("показывает один защищённый сценарий подключения", () => {
    render(<RemoteDesktopPage />);

    expect(screen.getByText("Защищённый доступ")).toBeInTheDocument();
    expect(screen.getByText(/одноразовый билет/i)).toBeInTheDocument();
    expect(screen.getAllByRole("button")).toHaveLength(1);
    expect(screen.queryByText("Открыть отдельно")).not.toBeInTheDocument();
    expect(screen.queryByText("Попробовать внутри")).not.toBeInTheDocument();
    expect(screen.queryByTitle(/встроенный режим/i)).not.toBeInTheDocument();
  });

  it("открывает desktop через защищённый launch endpoint", () => {
    render(<RemoteDesktopPage />);
    const connect = screen.getByRole("button", { name: "Подключиться" });
    const form = connect.closest("form");

    expect(form).toHaveAttribute("action", "/auth/desktop/launch");
    expect(form).toHaveAttribute("method", "get");
    expect(form).toHaveAttribute("target", "_blank");
    expect(form).toHaveAttribute("rel", "noopener noreferrer");
  });
});
