/**
 * Тесты TabBar: навигация, активная вкладка, aria-current.
 */
import { describe, it, expect, vi } from "vitest";
import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";

// Мокаем TanStack Router — в тестах реального RouterProvider нет
const mockNavigate = vi.fn();
const mockLocation = { pathname: "/" };

vi.mock("@tanstack/react-router", () => ({
  useRouter: () => ({ navigate: mockNavigate }),
  useLocation: () => mockLocation,
  createRootRoute: vi.fn(),
  createFileRoute: vi.fn(),
}));

vi.mock("@/lib/tg", () => ({
  haptic: { selection: vi.fn() },
  getInitData: () => "",
  initTheme: vi.fn(),
  registerBackButton: vi.fn(() => () => {}),
  hideBackButton: vi.fn(),
}));

import { TabBar } from "@/components/layout/TabBar";

describe("TabBar", () => {
  // Action-first канон: Сейчас/Действия/Реклама/Ещё.
  it("рендерит 4 основные вкладки", () => {
    render(<TabBar />);
    expect(screen.getByLabelText("Сейчас")).toBeInTheDocument();
    expect(screen.getByLabelText("Действия")).toBeInTheDocument();
    expect(screen.getByLabelText("Реклама")).toBeInTheDocument();
    expect(screen.getByLabelText("Ещё")).toBeInTheDocument();
  });

  // Активная вкладка имеет aria-current="page"
  it("Сейчас активна при pathname '/'", () => {
    mockLocation.pathname = "/";
    render(<TabBar />);
    const btn = screen.getByLabelText("Сейчас");
    expect(btn).toHaveAttribute("aria-current", "page");
  });

  // Неактивная вкладка — нет aria-current
  it("Реклама неактивна при pathname '/'", () => {
    mockLocation.pathname = "/";
    render(<TabBar />);
    const btn = screen.getByLabelText("Реклама");
    expect(btn).not.toHaveAttribute("aria-current");
  });

  // Клик вызывает navigate
  it("клик по вкладке вызывает navigate", async () => {
    mockLocation.pathname = "/";
    mockNavigate.mockClear();
    render(<TabBar />);
    await userEvent.click(screen.getByLabelText("Реклама"));
    expect(mockNavigate).toHaveBeenCalledWith({ to: "/ads" });
  });

  // Действия — основной money-action таб.
  it("клик по Действия навигирует на /actions", async () => {
    mockLocation.pathname = "/";
    mockNavigate.mockClear();
    render(<TabBar />);
    await userEvent.click(screen.getByLabelText("Действия"));
    expect(mockNavigate).toHaveBeenCalledWith({ to: "/actions" });
  });

  // Скрывается на /ads/:fbAdId (detail)
  it("не рендерится на /ads/12345 (detail-экран)", () => {
    mockLocation.pathname = "/ads/12345";
    const { container } = render(<TabBar />);
    expect(container.firstChild).toBeNull();
  });

  it("оставляет основные вкладки на /open как явный путь выхода", () => {
    mockLocation.pathname = "/open";
    render(<TabBar />);
    expect(
      screen.getByRole("navigation", { name: "Навигация" }),
    ).toBeInTheDocument();
    expect(screen.getByLabelText("Сейчас")).toBeInTheDocument();
  });

  it("подсвечивает «Ещё» на мобильной аналитике", () => {
    mockLocation.pathname = "/analytics";
    render(<TabBar />);

    expect(screen.getByLabelText("Ещё")).toHaveAttribute("aria-current", "page");
  });
});
