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
  // Рендерит все 5 вкладок канона: Панель/Объявления/Черновики/История/Ещё
  it("рендерит 5 основных вкладок", () => {
    render(<TabBar />);
    expect(screen.getByLabelText("Панель")).toBeInTheDocument();
    expect(screen.getByLabelText("Объявления")).toBeInTheDocument();
    expect(screen.getByLabelText("Черновики")).toBeInTheDocument();
    expect(screen.getByLabelText("История")).toBeInTheDocument();
    expect(screen.getByLabelText("Ещё")).toBeInTheDocument();
  });

  // Активная вкладка имеет aria-current="page"
  it("Панель активна при pathname '/'", () => {
    mockLocation.pathname = "/";
    render(<TabBar />);
    const btn = screen.getByLabelText("Панель");
    expect(btn).toHaveAttribute("aria-current", "page");
  });

  // Неактивная вкладка — нет aria-current
  it("Объявления неактивны при pathname '/'", () => {
    mockLocation.pathname = "/";
    render(<TabBar />);
    const btn = screen.getByLabelText("Объявления");
    expect(btn).not.toHaveAttribute("aria-current");
  });

  // Клик вызывает navigate
  it("клик по вкладке вызывает navigate", async () => {
    mockLocation.pathname = "/";
    mockNavigate.mockClear();
    render(<TabBar />);
    await userEvent.click(screen.getByLabelText("Объявления"));
    expect(mockNavigate).toHaveBeenCalledWith({ to: "/ads" });
  });

  // Черновики — основной таб, навигирует на /drafts
  it("клик по Черновикам навигирует на /drafts", async () => {
    mockLocation.pathname = "/";
    mockNavigate.mockClear();
    render(<TabBar />);
    await userEvent.click(screen.getByLabelText("Черновики"));
    expect(mockNavigate).toHaveBeenCalledWith({ to: "/drafts" });
  });

  // Скрывается на /ads/:fbAdId (detail)
  it("не рендерится на /ads/12345 (detail-экран)", () => {
    mockLocation.pathname = "/ads/12345";
    const { container } = render(<TabBar />);
    expect(container.firstChild).toBeNull();
  });
});
