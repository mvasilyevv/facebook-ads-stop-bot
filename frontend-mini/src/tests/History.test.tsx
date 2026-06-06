/**
 * Тест HistoryPage: переключение периода меняет days в хуке.
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

  // Количество алертов отображается
  it("показывает количество warning-алертов", () => {
    render(<HistoryTestWrapper />);
    expect(screen.getByText("5")).toBeInTheDocument();
  });

  // Правило из by_rule отображается
  it("показывает spend_no_event из топа нарушений", () => {
    render(<HistoryTestWrapper />);
    expect(screen.getByText("spend_no_event")).toBeInTheDocument();
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
});
