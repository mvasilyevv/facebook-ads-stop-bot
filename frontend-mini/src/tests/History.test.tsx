/**
 * Тест HistoryPage: переключение периода, KPI-данные, stage-счётчики, правила, loading-state.
 */
import { describe, it, expect, vi, beforeEach } from "vitest";
import { render, screen, fireEvent } from "@testing-library/react";
import type { HistorySummary } from "@fb/shared";

// Мок роутера
vi.mock("@tanstack/react-router", () => ({
  createFileRoute: () => ({ component: (c: unknown) => c }),
  useNavigate: () => vi.fn(),
}));

// Мок TG
vi.mock("@/lib/tg", () => ({
  haptic: { impact: vi.fn(), notify: vi.fn(), selection: vi.fn() },
  tgConfirm: vi.fn().mockResolvedValue(true),
  tgAlert: vi.fn().mockResolvedValue(undefined),
}));

// Мок-данные history summary
const MOCK_SUMMARY: HistorySummary = {
  from_iso: "2026-05-31T00:00:00",
  to_iso: "2026-06-07T00:00:00",
  totals: {
    spend: "1250.50",
    impressions: 50000,
    clicks: 2500,
    leads: 120,
    registrations: 95,
    deposits: 30,
    active_ads_count: 8,
  },
  alerts: {
    warning_count: 5,
    stop_count: 2,
    by_rule: [
      { rule_code: "spend_no_event", count: 3 },
      { rule_code: "cpa_threshold", count: 2 },
    ],
  },
  tasks: {
    disable_completed: 2,
    disable_failed: 0,
    enable_completed: 1,
  },
};

// Следим за вызовами хуков
const mockUseHistorySummary = vi.fn();
const mockUseHistoryOffers = vi.fn();
const mockUseHistoryCampaigns = vi.fn();

vi.mock("@/lib/api", () => ({
  useHistorySummary: (days: number) => mockUseHistorySummary(days),
  useHistoryOffers: (days: number) => mockUseHistoryOffers(days),
  useHistoryCampaigns: (days: number) => mockUseHistoryCampaigns(days),
}));

// Мок MiniHeader
vi.mock("@/components/layout/MiniHeader", () => ({
  MiniHeader: ({ title }: { title: string }) => <header>{title}</header>,
}));

import HistoryTestWrapper from "./History.test.helper";

describe("HistoryPage", () => {
  beforeEach(() => {
    // По умолчанию все хуки возвращают данные
    mockUseHistorySummary.mockImplementation(() => ({
      data: MOCK_SUMMARY,
      isLoading: false,
      isError: false,
      refetch: vi.fn(),
    }));
    mockUseHistoryOffers.mockImplementation(() => ({
      data: [],
      isLoading: false,
      isError: false,
      refetch: vi.fn(),
    }));
    mockUseHistoryCampaigns.mockImplementation(() => ({
      data: [],
      isLoading: false,
      isError: false,
      refetch: vi.fn(),
    }));
  });

  // Начальный рендер показывает KPI с данными summary
  it("показывает spend из summary", () => {
    render(<HistoryTestWrapper />);
    expect(screen.getByText("$1,250.50")).toBeInTheDocument();
  });

  // Период 7 дней по умолчанию — хук вызван с 7
  it("вызывает useHistorySummary(7) по умолчанию", () => {
    render(<HistoryTestWrapper />);
    expect(mockUseHistorySummary).toHaveBeenCalledWith(7);
  });

  // Переключение на 30 дней меняет аргумент хука
  it("переключение на 30 дней вызывает useHistorySummary(30)", () => {
    render(<HistoryTestWrapper />);
    const btn30 = screen.getByText("30 дней");
    fireEvent.click(btn30);
    expect(mockUseHistorySummary).toHaveBeenCalledWith(30);
  });

  // Переключение на 90 дней
  it("переключение на 90 дней вызывает useHistorySummary(90)", () => {
    render(<HistoryTestWrapper />);
    fireEvent.click(screen.getByText("90 дней"));
    expect(mockUseHistorySummary).toHaveBeenCalledWith(90);
  });

  // Warning-счётчик отображается в блоке ПО STAGE
  it("показывает количество warning-алертов", () => {
    render(<HistoryTestWrapper />);
    // «Warning-алертов» лейбл + «5» значение в MetaRow
    expect(screen.getByText("Warning-алертов")).toBeInTheDocument();
    expect(screen.getByText("5")).toBeInTheDocument();
  });

  // Stop-счётчик отображается (значение "2" может встречаться несколько раз — проверяем лейбл)
  it("показывает количество stop-алертов", () => {
    render(<HistoryTestWrapper />);
    expect(screen.getByText("Stop-алертов")).toBeInTheDocument();
    // «2» встречается в stop_count, disable_completed и cpa_threshold — используем getAllByText
    expect(screen.getAllByText("2").length).toBeGreaterThanOrEqual(1);
  });

  // Задачи disable/enable отображаются
  it("показывает счётчики задач disable/enable", () => {
    render(<HistoryTestWrapper />);
    expect(screen.getByText("Disable завершено")).toBeInTheDocument();
    expect(screen.getByText("Enable завершено")).toBeInTheDocument();
  });

  // Правило из by_rule отображается через ruleCodeLabel
  it("показывает spend_no_event из топа нарушений как короткий лейбл или код", () => {
    render(<HistoryTestWrapper />);
    // ruleCodeLabel("spend_no_event", true) — нет в RULE_CODE_LABELS_SHORT → fallback = сам код
    expect(screen.getByText("spend_no_event")).toBeInTheDocument();
  });

  // KPI лиды отображается
  it("показывает кол-во лидов", () => {
    render(<HistoryTestWrapper />);
    expect(screen.getByText("120")).toBeInTheDocument();
  });

  // KPI депозиты отображается
  it("показывает кол-во депозитов", () => {
    render(<HistoryTestWrapper />);
    expect(screen.getByText("30")).toBeInTheDocument();
  });

  // При loading показывает скелетоны, а не KPI
  it("при загрузке показывает skeleton, не данные", () => {
    mockUseHistorySummary.mockReturnValue({
      data: undefined,
      isLoading: true,
      isError: false,
      refetch: vi.fn(),
    });
    render(<HistoryTestWrapper />);
    expect(screen.queryByText("$1,250.50")).not.toBeInTheDocument();
  });

  // При пустых данных показывает хотя бы один EmptyState "Событий нет"
  it("при отсутствии данных показывает EmptyState", () => {
    mockUseHistorySummary.mockReturnValue({
      data: undefined,
      isLoading: false,
      isError: false,
      refetch: vi.fn(),
    });
    render(<HistoryTestWrapper />);
    // "Событий нет" может появляться несколько раз (summary + offers + campaigns)
    expect(screen.getAllByText("Событий нет").length).toBeGreaterThanOrEqual(1);
  });

  // Заголовок страницы
  it("показывает заголовок История", () => {
    render(<HistoryTestWrapper />);
    expect(screen.getByText("История")).toBeInTheDocument();
  });
});
