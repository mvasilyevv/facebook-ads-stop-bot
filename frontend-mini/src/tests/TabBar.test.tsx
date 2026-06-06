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
  // Рендерит все 5 вкладок
  it("рендерит 5 основных вкладок", () => {
    render(<TabBar />);
    expect(screen.getByLabelText("Дашборд")).toBeInTheDocument();
    expect(screen.getByLabelText("Объявл.")).toBeInTheDocument();
    expect(screen.getByLabelText("Офферы")).toBeInTheDocument();
    expect(screen.getByLabelText("История")).toBeInTheDocument();
    expect(screen.getByLabelText("Ещё")).toBeInTheDocument();
  });

  // Активная вкладка имеет aria-current="page"
  it("Дашборд активен при pathname '/'", () => {
    mockLocation.pathname = "/";
    render(<TabBar />);
    const btn = screen.getByLabelText("Дашборд");
    expect(btn).toHaveAttribute("aria-current", "page");
  });

  // Неактивная вкладка — нет aria-current
  it("Объявления неактивны при pathname '/'", () => {
    mockLocation.pathname = "/";
    render(<TabBar />);
    const btn = screen.getByLabelText("Объявл.");
    expect(btn).not.toHaveAttribute("aria-current");
  });

  // Клик вызывает navigate
  it("клик по вкладке вызывает navigate", async () => {
    mockLocation.pathname = "/";
    mockNavigate.mockClear();
    render(<TabBar />);
    await userEvent.click(screen.getByLabelText("Объявл."));
    expect(mockNavigate).toHaveBeenCalledWith({ to: "/ads" });
  });

  // Скрывается на /ads/:fbAdId (detail)
  it("не рендерится на /ads/12345 (detail-экран)", () => {
    mockLocation.pathname = "/ads/12345";
    const { container } = render(<TabBar />);
    expect(container.firstChild).toBeNull();
  });
});
