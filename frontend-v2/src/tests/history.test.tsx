/**
 * Тесты History-страницы: summary с mock-данными, period selector, empty state.
 */

import { describe, it, expect, vi } from "vitest";
import { render, screen, fireEvent } from "@testing-library/react";

import { HistorySummarySection } from "@/components/history/HistorySummarySection";
import { PeriodSelector } from "@/components/history/PeriodSelector";
import { HistoryTimeline } from "@/components/history/HistoryTimeline";
import type { HistorySummary } from "@/lib/types/api";

// Mock-данные summary: spend, leads, deposits, alerts по stage + by_rule.
// Структура соответствует реальному ответу /api/history/summary (вложенные блоки).
const SUMMARY: HistorySummary = {
  from_iso: "2026-04-29T00:00:00Z",
  to_iso: "2026-05-29T00:00:00Z",
  totals: {
    spend: "12345.67",
    impressions: 1_000_000,
    clicks: 5000,
    leads: 423,
    registrations: 210,
    deposits: 87,
    active_ads_count: 40,
  },
  alerts: {
    warning_count: 834,
    stop_count: 267,
    by_rule: [
      { rule_code: "CPL_HIGH", count: 412 },
      { rule_code: "SPEND_NO_EVENT", count: 234 },
      { rule_code: "FREQ_HIGH", count: 188 },
    ],
  },
  tasks: {
    disable_completed: 40,
    disable_failed: 3,
    enable_completed: 12,
  },
};

describe("HistorySummarySection", () => {
  // Тест: компонент рендерит 4 KPI с правильными значениями из summary.
  it("рендерит KPI spend / leads / deposits / alerts", () => {
    render(
      <HistorySummarySection
        summary={SUMMARY}
        isLoading={false}
        isError={false}
      />,
    );

    // Spend
    expect(screen.getByText("$12,345.67")).toBeInTheDocument();
    // Leads
    expect(screen.getByText("423")).toBeInTheDocument();
    // Deposits
    expect(screen.getByText("87")).toBeInTheDocument();
    // Alerts = warning + stop = 1101
    expect(screen.getByText("1,101")).toBeInTheDocument();
  });

  // Тест: loading рисует skeleton-плейсхолдеры, числа не отображаются.
  it("loading показывает skeleton, не данные", () => {
    render(
      <HistorySummarySection
        summary={undefined}
        isLoading={true}
        isError={false}
      />,
    );
    // Значения summary не должны быть видны
    expect(screen.queryByText("$12,345.67")).not.toBeInTheDocument();
    // Skeleton-элементы присутствуют
    expect(screen.getAllByRole("status").length).toBeGreaterThan(0);
  });

  // Тест: error показывает ErrorState с кнопкой Retry + тексты ошибки.
  it("error рендерит ErrorState с retry", () => {
    const onRetry = vi.fn();
    render(
      <HistorySummarySection
        summary={undefined}
        isLoading={false}
        isError={true}
        error={new Error("network fail")}
        onRetry={onRetry}
      />,
    );
    expect(screen.getByRole("alert")).toBeInTheDocument();
    expect(screen.getByText(/Не удалось загрузить сводку/i)).toBeInTheDocument();
    const btn = screen.getByRole("button", { name: /повторить/i });
    fireEvent.click(btn);
    expect(onRetry).toHaveBeenCalledTimes(1);
  });

  // Тест: breakdown by_rule показывает RuleBadge для каждого правила.
  it("рендерит breakdown по правилам", () => {
    render(
      <HistorySummarySection
        summary={SUMMARY}
        isLoading={false}
        isError={false}
      />,
    );
    expect(screen.getByText("CPL_HIGH")).toBeInTheDocument();
    expect(screen.getByText("SPEND_NO_EVENT")).toBeInTheDocument();
    expect(screen.getByText("FREQ_HIGH")).toBeInTheDocument();
  });
});

describe("PeriodSelector", () => {
  // Тест: клик на пресет '7д' вызывает onChange с правильным диапазоном.
  it("пресет '7д' вызывает onChange", () => {
    const onChange = vi.fn();
    render(
      <PeriodSelector
        value={{ from_iso: "2026-04-29", to_iso: "2026-05-29" }}
        onChange={onChange}
      />,
    );
    const btn7 = screen.getByText("7д");
    fireEvent.click(btn7);
    expect(onChange).toHaveBeenCalledTimes(1);
    const [called] = onChange.mock.calls[0] as [{ from_iso: string; to_iso: string }];
    const today = new Date().toISOString().slice(0, 10);
    expect(called.to_iso).toBe(today);
    // from_iso должен быть 7 дней назад от сегодня
    const diff = Math.round(
      (new Date(called.to_iso).getTime() - new Date(called.from_iso).getTime()) / 86400000,
    );
    expect(diff).toBe(7);
  });

  // Тест: кастомный диапазон >90 дней показывает ошибку и не вызывает onChange.
  it("кастомный диапазон >90 дней показывает ошибку", () => {
    const onChange = vi.fn();
    // Начинаем с 30-дневного пресета — showCustom=false, кнопка откроет форму.
    const today = new Date().toISOString().slice(0, 10);
    const thirtyAgo = new Date(Date.now() - 30 * 86400000).toISOString().slice(0, 10);
    render(
      <PeriodSelector
        value={{ from_iso: thirtyAgo, to_iso: today }}
        onChange={onChange}
      />,
    );
    // Открываем кастомный диапазон
    const customBtn = screen.getByText("Произвольный");
    fireEvent.click(customBtn);

    // Устанавливаем диапазон > 90 дней (from=2025-01-01, to=2026-05-29)
    const fromInput = screen.getByLabelText("Дата начала периода");
    const toInput = screen.getByLabelText("Дата конца периода");
    fireEvent.change(fromInput, { target: { value: "2025-01-01" } });
    fireEvent.change(toInput, { target: { value: "2026-05-29" } });

    const applyBtn = screen.getByText("Применить");
    fireEvent.click(applyBtn);

    // Ошибка должна появиться
    expect(screen.getByText(/90 дней/i)).toBeInTheDocument();
    // onChange не должен быть вызван
    expect(onChange).not.toHaveBeenCalled();
  });
});

describe("HistoryTimeline", () => {
  // Тест: пустой список событий показывает EmptyState.
  it("empty state при отсутствии событий", () => {
    render(
      <HistoryTimeline
        events={[]}
        isLoading={false}
        isError={false}
      />,
    );
    expect(screen.getByText(/Событий за период нет/i)).toBeInTheDocument();
  });

  // Тест: loading показывает skeleton-плейсхолдеры.
  it("loading показывает skeleton", () => {
    render(
      <HistoryTimeline
        events={undefined}
        isLoading={true}
        isError={false}
      />,
    );
    expect(screen.getAllByRole("status").length).toBeGreaterThan(0);
  });

  // Тест: error показывает ErrorState с retry.
  it("error рендерит ErrorState", () => {
    const onRetry = vi.fn();
    render(
      <HistoryTimeline
        events={undefined}
        isLoading={false}
        isError={true}
        error={new Error("500")}
        onRetry={onRetry}
      />,
    );
    expect(screen.getByRole("alert")).toBeInTheDocument();
    const btn = screen.getByRole("button", { name: /повторить/i });
    fireEvent.click(btn);
    expect(onRetry).toHaveBeenCalledTimes(1);
  });
});
