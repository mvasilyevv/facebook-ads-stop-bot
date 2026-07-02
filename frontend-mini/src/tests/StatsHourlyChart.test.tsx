/**
 * Тест StatsHourlyChart: <2 точек → «Нет данных», ≥2 точек → path рендерится,
 * переключатель метрики (spend | лиды | депы) меняет отображаемый ряд.
 */
import { describe, it, expect } from "vitest";
import { render, screen, fireEvent } from "@testing-library/react";
import { StatsHourlyChart, type StatsChartPoint } from "@/components/domain/StatsHourlyChart";

const POINTS: StatsChartPoint[] = [
  { label: "10:00", spend: 10, leads: 1, deposits: 0 },
  { label: "11:00", spend: 25, leads: 3, deposits: 1 },
  { label: "12:00", spend: 40, leads: 5, deposits: 2 },
];

describe("StatsHourlyChart", () => {
  // Меньше 2 точек — заглушка «Нет данных», без SVG-линии
  it("показывает «Нет данных» при менее чем 2 точках", () => {
    render(<StatsHourlyChart data={[POINTS[0]!]} />);
    expect(screen.getByText("Нет данных")).toBeInTheDocument();
  });

  // Пустой массив — тоже заглушка
  it("показывает «Нет данных» при пустом массиве", () => {
    render(<StatsHourlyChart data={[]} />);
    expect(screen.getByText("Нет данных")).toBeInTheDocument();
  });

  // 3 точки — рендерится SVG polyline (линия графика)
  it("рендерит polyline при 2+ точках", () => {
    const { container } = render(<StatsHourlyChart data={POINTS} />);
    expect(container.querySelector("polyline")).toBeInTheDocument();
    expect(screen.queryByText("Нет данных")).not.toBeInTheDocument();
  });

  // По умолчанию активна метрика Spend
  it("по умолчанию активна кнопка Spend", () => {
    render(<StatsHourlyChart data={POINTS} />);
    expect(screen.getByRole("button", { name: "Spend" })).toHaveAttribute("aria-pressed", "true");
    expect(screen.getByRole("button", { name: "Лиды" })).toHaveAttribute("aria-pressed", "false");
  });

  // Клик на «Лиды» переключает активную метрику
  it("клик на «Лиды» переключает aria-pressed на кнопку Лиды", () => {
    render(<StatsHourlyChart data={POINTS} />);
    fireEvent.click(screen.getByRole("button", { name: "Лиды" }));
    expect(screen.getByRole("button", { name: "Лиды" })).toHaveAttribute("aria-pressed", "true");
    expect(screen.getByRole("button", { name: "Spend" })).toHaveAttribute("aria-pressed", "false");
  });

  // Клик на «Депы» переключает активную метрику
  it("клик на «Депы» переключает aria-pressed на кнопку Депы", () => {
    render(<StatsHourlyChart data={POINTS} />);
    fireEvent.click(screen.getByRole("button", { name: "Депы" }));
    expect(screen.getByRole("button", { name: "Депы" })).toHaveAttribute("aria-pressed", "true");
  });

  // Метки оси X берутся из point.label
  it("отображает метки времени из point.label", () => {
    const { container } = render(<StatsHourlyChart data={POINTS} />);
    const texts = Array.from(container.querySelectorAll("text")).map((t) => t.textContent);
    expect(texts).toContain("10:00");
    expect(texts).toContain("12:00");
  });
});
