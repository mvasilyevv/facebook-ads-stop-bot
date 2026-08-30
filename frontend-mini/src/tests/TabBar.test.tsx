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

// TabBar рендерится на каждом экране: бейдж читает уже загруженный снимок
// пассивно (enabled: false), поэтому в тестах достаточно замокать эти два
// хука, не поднимая ни QueryClientProvider, ни реальный сокет.
const mockRealtimeStatus = vi.fn(() => "connected");
const mockPeekOperatorSnapshot = vi.fn(
  (..._args: unknown[]): { data: unknown } => ({ data: undefined }),
);

vi.mock("@fb/operator-api", () => ({
  useOperatorRealtimeStatus: () => mockRealtimeStatus(),
}));

vi.mock("@/lib/operatorApi", () => ({
  usePeekOperatorSnapshot: (...args: unknown[]) =>
    mockPeekOperatorSnapshot(...args),
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

  // Полноэкранные роуты настроек (issue #342, часть 2) — тот же паттерн, что
  // /desktop и /analytics: вторичный экран, а не detail с единственным
  // объектом, поэтому tab-bar остаётся видимым и подсвечивает «Ещё».
  it("не прячет tab-bar и подсвечивает «Ещё» на полноэкранных настройках", () => {
    mockLocation.pathname = "/settings/observer";
    const { container } = render(<TabBar />);
    expect(container.firstChild).not.toBeNull();
    expect(screen.getByLabelText("Ещё")).toHaveAttribute("aria-current", "page");
  });

  it("подсвечивает «Ещё» на мобильной аналитике", () => {
    mockLocation.pathname = "/analytics";
    render(<TabBar />);

    expect(screen.getByLabelText("Ещё")).toHaveAttribute("aria-current", "page");
  });

  // Бейдж открытых инцидентов на «Действия» — источник: уже загруженный снимок.
  it("не показывает бейдж, пока снимок ещё не загружен нигде (unknown ≠ 0)", () => {
    mockLocation.pathname = "/";
    mockPeekOperatorSnapshot.mockReturnValue({ data: undefined });
    render(<TabBar />);
    expect(screen.getByLabelText("Действия")).toBeInTheDocument();
    expect(screen.queryByLabelText(/открытых инцидентов/)).not.toBeInTheDocument();
  });

  it("показывает число открытых инцидентов из attention-секции снимка", () => {
    mockLocation.pathname = "/";
    mockPeekOperatorSnapshot.mockReturnValue({
      data: {
        attention: {
          state: "ready",
          data: {
            items: [
              { kind: "incident", severity: "warning" },
              { kind: "incident", severity: "warning" },
              { kind: "action", severity: "critical" },
            ],
          },
        },
      },
    });
    render(<TabBar />);
    expect(
      screen.getByLabelText("Действия, открытых инцидентов: 2"),
    ).toBeInTheDocument();
    expect(screen.getByText("2")).toBeInTheDocument();
  });

  it("не заводит собственный запрос — вызывает peek с enabled: false внутри хука", () => {
    mockLocation.pathname = "/";
    mockPeekOperatorSnapshot.mockClear();
    render(<TabBar />);
    // Компонент читает кэш через usePeekOperatorSnapshot, а не через
    // отдельный fetch/useOperatorSnapshot — сам хук уже enabled: false.
    expect(mockPeekOperatorSnapshot).toHaveBeenCalledWith({ window: "today" });
  });

  it("красит бейдж в danger, если среди инцидентов есть critical", () => {
    mockLocation.pathname = "/";
    mockPeekOperatorSnapshot.mockReturnValue({
      data: {
        attention: {
          state: "ready",
          data: {
            items: [{ kind: "incident", severity: "critical" }],
          },
        },
      },
    });
    render(<TabBar />);
    const badge = screen.getByText("1");
    expect(badge).toHaveStyle({ backgroundColor: "var(--color-danger)" });
  });
});
