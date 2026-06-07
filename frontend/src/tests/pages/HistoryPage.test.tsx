/**
 * Тесты History-страницы:
 * - PeriodSelector меняет запрос (queryKey)
 * - Summary-секция рендерит KPI-данные
 * - Drawer открывается при клике на alert в таймлайне
 */

import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, it, expect, vi } from "vitest";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";

// ─── Моки хуков ───────────────────────────────────────────────────────────────

const mockSummary = {
  from_iso: "2026-05-08T00:00:00Z",
  to_iso: "2026-06-07T23:59:59Z",
  totals: {
    spend: "1234.56",
    impressions: 500000,
    clicks: 10000,
    leads: 200,
    registrations: 150,
    deposits: 50,
    active_ads_count: 12,
  },
  alerts: {
    warning_count: 8,
    stop_count: 3,
    by_rule: [
      { rule_code: "cpl_stop", count: 5 },
      { rule_code: "spend_no_dep_range", count: 2 },
    ],
  },
  tasks: {
    disable_completed: 3,
    disable_failed: 0,
    enable_completed: 1,
  },
};

const mockTimeline = [
  {
    event_type: "alert",
    ts: "2026-06-06T14:32:00Z",
    fb_ad_id: "ad_123",
    ad_name: "Test Ad",
    campaign_name: "Test Campaign",
    stage: "stop",
    rule_codes: ["cpl_stop"],
    task_type: null,
    task_status: null,
  },
  {
    event_type: "task",
    ts: "2026-06-06T14:35:00Z",
    fb_ad_id: "ad_123",
    ad_name: "Test Ad",
    campaign_name: null,
    stage: null,
    rule_codes: null,
    task_type: "meta_api_mutation",
    task_status: "DONE",
  },
];

// Мок-функции с возможностью контроля из тестов
const mockSummaryData: typeof mockSummary | undefined = mockSummary;
const mockTimelineData: typeof mockTimeline | undefined = mockTimeline;

vi.mock("@/lib/api/history", () => ({
  useHistorySummary: (_params: unknown) => ({
    data: mockSummaryData,
    isLoading: false,
    error: null,
    refetch: vi.fn(),
  }),
  useHistoryTimeline: (_params: unknown) => ({
    data: mockTimelineData,
    isLoading: false,
    error: null,
    refetch: vi.fn(),
  }),
  useHistoryEvents: (_params: unknown) => ({
    data: [],
    isLoading: false,
    error: null,
    refetch: vi.fn(),
  }),
}));

import { HistorySummarySection } from "@/components/history/HistorySummarySection";
import { PeriodSelector } from "@/components/history/PeriodSelector";
import { HistoryTimeline } from "@/components/history/HistoryTimeline";
import { HistoryEventsDrawer } from "@/components/history/HistoryEventsDrawer";

// ─── Хелперы ──────────────────────────────────────────────────────────────────

function makeQueryClient() {
  return new QueryClient({ defaultOptions: { queries: { retry: false } } });
}

function wrap(ui: React.ReactElement) {
  return (
    <QueryClientProvider client={makeQueryClient()}>{ui}</QueryClientProvider>
  );
}

// ─── PeriodSelector ────────────────────────────────────────────────────────────

describe("PeriodSelector", () => {
  // PeriodSelector рендерится с кнопками пресетов
  it("рендерит пресеты 7, 30, 90 дней", () => {
    const onChange = vi.fn();
    const period = {
      from_iso: new Date(Date.now() - 30 * 86400 * 1000).toISOString(),
      to_iso: new Date().toISOString(),
    };
    render(wrap(<PeriodSelector value={period} onChange={onChange} />));
    expect(screen.getByText("7 дн")).toBeInTheDocument();
    expect(screen.getByText("30 дн")).toBeInTheDocument();
    expect(screen.getByText("90 дн")).toBeInTheDocument();
  });

  // Клик на пресет вызывает onChange с новым периодом
  it("клик на '7 дн' вызывает onChange", async () => {
    const user = userEvent.setup();
    const onChange = vi.fn();
    const period = {
      from_iso: new Date(Date.now() - 30 * 86400 * 1000).toISOString(),
      to_iso: new Date().toISOString(),
    };
    render(wrap(<PeriodSelector value={period} onChange={onChange} />));
    await user.click(screen.getByText("7 дн"));
    expect(onChange).toHaveBeenCalledOnce();
    // Разница должна быть ~7 дней
    const called = onChange.mock.calls[0]?.[0] as { from_iso: string; to_iso: string };
    const diff = (new Date(called.to_iso).getTime() - new Date(called.from_iso).getTime()) / 86400_000;
    expect(diff).toBeGreaterThan(6);
    expect(diff).toBeLessThan(8);
  });

  // Ошибка при диапазоне > 90 дней
  it("custom > 90 дней показывает ошибку", async () => {
    const user = userEvent.setup();
    const onChange = vi.fn();
    const period = {
      from_iso: new Date(Date.now() - 30 * 86400 * 1000).toISOString(),
      to_iso: new Date().toISOString(),
    };
    render(wrap(<PeriodSelector value={period} onChange={onChange} />));

    // Устанавливаем даты вручную
    const fromInput = screen.getByLabelText("С даты");
    const toInput = screen.getByLabelText("По дату");
    await user.clear(fromInput);
    await user.type(fromInput, "2026-01-01");
    await user.clear(toInput);
    await user.type(toInput, "2026-06-07");
    await user.click(screen.getByText("Применить"));

    expect(screen.getByRole("alert")).toHaveTextContent("90");
    expect(onChange).not.toHaveBeenCalled();
  });
});

// ─── HistorySummarySection ─────────────────────────────────────────────────────

describe("HistorySummarySection", () => {
  // Рендерит spend из данных
  it("отображает spend из данных", () => {
    render(
      wrap(
        <HistorySummarySection
          data={mockSummary}
          isLoading={false}
          error={null}
        />,
      ),
    );
    expect(screen.getByText("$1,234.56")).toBeInTheDocument();
  });

  // Рендерит warning и stop counts
  it("отображает alert counts", () => {
    render(
      wrap(
        <HistorySummarySection
          data={mockSummary}
          isLoading={false}
          error={null}
        />,
      ),
    );
    // warning_count=8 уникален в DOM
    expect(screen.getByText("8")).toBeInTheDocument();
    // stop_count=3 может дублироваться (disable_completed тоже 3),
    // проверяем что секция "Алерты" содержит метку Stop
    expect(screen.getByText("Stop")).toBeInTheDocument();
  });

  // Показывает скелетон при загрузке
  it("показывает skeleton при isLoading=true", () => {
    const { container } = render(
      wrap(
        <HistorySummarySection
          data={undefined}
          isLoading={true}
          error={null}
        />,
      ),
    );
    // Skeleton рендерит div-элементы с анимацией
    expect(container.querySelector("[class*='animate']")).toBeInTheDocument();
  });

  // Показывает ErrorState при ошибке
  it("показывает ErrorState при ошибке", () => {
    render(
      wrap(
        <HistorySummarySection
          data={undefined}
          isLoading={false}
          error={new Error("Network error")}
          onRetry={vi.fn()}
        />,
      ),
    );
    expect(screen.getByRole("alert")).toBeInTheDocument();
    expect(screen.getByText("Network error")).toBeInTheDocument();
  });
});

// ─── HistoryTimeline ───────────────────────────────────────────────────────────

describe("HistoryTimeline", () => {
  // Рендерит события с именами объявлений
  it("рендерит STOP событие из таймлайна", () => {
    render(
      wrap(
        <HistoryTimeline
          items={mockTimeline}
          isLoading={false}
          error={null}
        />,
      ),
    );
    expect(screen.getByText(/STOP.*Test Ad/)).toBeInTheDocument();
  });

  // Показывает EmptyState при пустом списке
  it("EmptyState при пустом списке", () => {
    render(
      wrap(
        <HistoryTimeline
          items={[]}
          isLoading={false}
          error={null}
        />,
      ),
    );
    expect(screen.getByText("Событий нет")).toBeInTheDocument();
  });

  // Клик на "подробнее" вызывает onAlertClick
  it("клик 'подробнее' → onAlertClick", async () => {
    const user = userEvent.setup();
    const onAlertClick = vi.fn();
    render(
      wrap(
        <HistoryTimeline
          items={mockTimeline}
          isLoading={false}
          error={null}
          onAlertClick={onAlertClick}
        />,
      ),
    );
    await user.click(screen.getByRole("button", { name: /подробнее/i }));
    expect(onAlertClick).toHaveBeenCalledOnce();
  });

  // Day-separator рендерится
  it("day-separator по дате события", () => {
    render(
      wrap(
        <HistoryTimeline
          items={mockTimeline}
          isLoading={false}
          error={null}
        />,
      ),
    );
    // Дата 2026-06-06
    expect(screen.getByText("2026-06-06")).toBeInTheDocument();
  });
});

// ─── HistoryEventsDrawer ──────────────────────────────────────────────────────

describe("HistoryEventsDrawer", () => {
  const period = {
    from_iso: "2026-05-08T00:00:00Z",
    to_iso: "2026-06-07T23:59:59Z",
  };

  // Drawer закрыт — контент не виден
  it("закрытый drawer — контент не в DOM", () => {
    render(
      wrap(
        <HistoryEventsDrawer
          open={false}
          onOpenChange={vi.fn()}
          period={period}
        />,
      ),
    );
    expect(screen.queryByText("История событий")).not.toBeInTheDocument();
  });

  // Drawer открыт — заголовок виден
  it("открытый drawer — заголовок виден", () => {
    render(
      wrap(
        <HistoryEventsDrawer
          open={true}
          onOpenChange={vi.fn()}
          period={period}
        />,
      ),
    );
    expect(screen.getByText("История событий")).toBeInTheDocument();
  });

  // Esc закрывает drawer
  it("Esc вызывает onOpenChange(false)", async () => {
    const user = userEvent.setup();
    const onOpenChange = vi.fn();
    render(
      wrap(
        <HistoryEventsDrawer
          open={true}
          onOpenChange={onOpenChange}
          period={period}
        />,
      ),
    );
    await user.keyboard("{Escape}");
    expect(onOpenChange).toHaveBeenCalledWith(false);
  });
});
