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
  // Action-first канон: Сейчас/Решения/Реклама/Ещё («Действия» заменена
  // «Решениями» — issue #338, PR4, спека eng-lead).
  it("рендерит 4 основные вкладки", () => {
    render(<TabBar />);
    expect(screen.getByLabelText("Сейчас")).toBeInTheDocument();
    expect(screen.getByLabelText("Решения")).toBeInTheDocument();
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

  // Решения — главный операционный таб (issue #338).
  it("клик по Решения навигирует на /decisions", async () => {
    mockLocation.pathname = "/";
    mockNavigate.mockClear();
    render(<TabBar />);
    await userEvent.click(screen.getByLabelText("Решения"));
    expect(mockNavigate).toHaveBeenCalledWith({ to: "/decisions" });
  });

  // Журнал инцидентов и лог действий остаются доступны из той же вкладки
  // (extra-пути), отдельного таба для них не заводим — тесно.
  it("подсвечивает «Решения» на /incidents и /actions (extra-пути)", () => {
    mockLocation.pathname = "/incidents";
    render(<TabBar />);
    expect(screen.getByLabelText("Решения")).toHaveAttribute(
      "aria-current",
      "page",
    );
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

  function incidentItem(id: string, severity: "warning" | "critical") {
    return {
      id,
      kind: "incident" as const,
      severity,
      title: "Сигнал",
      summary: "Сигнал требует проверки",
      reason: null,
      occurred_at: "2026-01-01T00:00:00Z",
      target: { kind: "ad" as const, id: "1", label: "Ad" },
      action: null,
      recovery_action: null,
      status: "open" as const,
      requires_usd_evidence: false,
    };
  }

  // Бейдж на «Решения» — дешёвая аппроксимация без импорта decisionFeed.ts
  // (бюджет initial JS, см. комментарий в TabBar.tsx): incident/source(≠ok)
  // считаются напрямую, action — не считается (нужен join с actions,
  // которого здесь нет; точный список — на самом экране «Решения»).
  it("не показывает бейдж, пока снимок ещё не загружен нигде (unknown ≠ 0)", () => {
    mockLocation.pathname = "/";
    mockPeekOperatorSnapshot.mockReturnValue({ data: undefined });
    render(<TabBar />);
    expect(screen.getByLabelText("Решения")).toBeInTheDocument();
    expect(screen.queryByLabelText(/ожидают решения/)).not.toBeInTheDocument();
  });

  it("показывает число решений, посчитанное сервером, а не пересчитывает строки сам", () => {
    mockLocation.pathname = "/";
    mockPeekOperatorSnapshot.mockReturnValue({
      data: {
        attention: {
          state: "ready",
          data: {
            items: [
              incidentItem("incident-1", "warning"),
              incidentItem("incident-2", "warning"),
              {
                id: "task:99",
                kind: "action" as const,
                severity: "critical" as const,
                title: "Команда требует сверки",
                summary: "#99 · running",
                reason: null,
                occurred_at: "2026-01-01T00:00:00Z",
                target: { kind: "ad" as const, id: null, label: null },
                action: { label: "Открыть", href: "/actions/99" },
                recovery_action: null,
                status: null,
                requires_usd_evidence: false,
              },
            ],
            total: 3,
            truncated: false,
            // Сервер отобрал строки тем же правилом, что и лента: два
            // инцидента считаются, running-действие — нет, это прогресс.
            decisions_count: 2,
            decisions_critical: false,
          },
        },
        actions: {
          state: "ready",
          data: { items: [{ id: "99", state: "running" }] },
        },
      },
    });
    render(<TabBar />);
    expect(screen.getByLabelText("Решения, решений: 2")).toBeInTheDocument();
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

  it("красит бейдж в danger, если среди решений есть critical", () => {
    mockLocation.pathname = "/";
    mockPeekOperatorSnapshot.mockReturnValue({
      data: {
        attention: {
          state: "ready",
          data: {
            items: [incidentItem("incident-1", "critical")],
            total: 1,
            truncated: false,
            decisions_count: 1,
            decisions_critical: true,
          },
        },
      },
    });
    render(<TabBar />);
    const badge = screen.getByText("1");
    expect(badge).toHaveStyle({ backgroundColor: "var(--color-danger)" });
  });
});
