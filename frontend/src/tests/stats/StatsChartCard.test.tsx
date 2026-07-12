/**
 * Тесты StatsChartCard — график воронки (почасовые дельты / подневные итоги).
 * ResizeObserver-мок — тот же паттерн, что в tests/data/AreaChart.test.tsx (Recharts
 * в jsdom требует реальный contentRect от ResizeObserver).
 */
import { render, screen } from "@testing-library/react";
import { describe, it, expect, beforeAll } from "vitest";
import { StatsChartCard } from "@/components/stats/StatsChartCard";

beforeAll(() => {
  class MockResizeObserver {
    private cb: ResizeObserverCallback;
    constructor(cb: ResizeObserverCallback) {
      this.cb = cb;
    }
    observe(_el: Element) {
      this.cb(
        [{ contentRect: { width: 600, height: 280 } } as ResizeObserverEntry],
        this as unknown as ResizeObserver,
      );
    }
    unobserve() {}
    disconnect() {}
  }
  globalThis.ResizeObserver = MockResizeObserver as unknown as typeof ResizeObserver;
});

const HOURLY_POINTS = [
  { ts: "2026-07-02T10:00:00Z", spend: "10.00", impressions: 100, clicks: 5, leads: 2, registrations: 1, deposits: 0, active_ads: 5 },
  { ts: "2026-07-02T11:00:00Z", spend: "20.00", impressions: 200, clicks: 10, leads: 4, registrations: 2, deposits: 1, active_ads: 4 },
  { ts: "2026-07-02T12:00:00Z", spend: "15.00", impressions: 150, clicks: 7, leads: 3, registrations: 1, deposits: 0, active_ads: 3 },
];

describe("StatsChartCard", () => {
  // Режим hourly с >=2 точками — рисует ChartWrapper (реальный AreaChart), не заглушку.
  it("рендерится в режиме hourly с данными без ошибок", () => {
    expect(() => {
      render(<StatsChartCard mode="hourly" points={HOURLY_POINTS} />);
    }).not.toThrow();
    expect(screen.getByTestId("chart-wrapper")).toBeInTheDocument();
  });

  // Пустое состояние: <2 точек → «Нет данных» текстом, без пустого графика с осями.
  it("показывает «Нет данных» при пустом массиве точек", () => {
    render(<StatsChartCard mode="hourly" points={[]} />);
    expect(screen.getByText("Нет данных")).toBeInTheDocument();
    expect(screen.queryByTestId("chart-wrapper")).not.toBeInTheDocument();
  });

  // Пустое состояние: points=undefined (запрос ещё не пришёл, но loading=false) — тоже «Нет данных».
  it("показывает «Нет данных» когда points не переданы", () => {
    render(<StatsChartCard mode="hourly" />);
    expect(screen.getByText("Нет данных")).toBeInTheDocument();
  });

  // Одна точка тоже недостаточно для линии — «Нет данных».
  it("показывает «Нет данных» при одной точке", () => {
    render(<StatsChartCard mode="hourly" points={HOURLY_POINTS.slice(0, 1)} />);
    expect(screen.getByText("Нет данных")).toBeInTheDocument();
  });

  // loading=true — skeleton вместо графика/заглушки.
  it("рендерит skeleton при loading=true", () => {
    render(<StatsChartCard mode="hourly" loading />);
    expect(screen.getByRole("status")).toBeInTheDocument();
  });

  // M-24 (аудит 2026-07-12): «Итого»/«Пик» = реальная сумма/максимум точек, не shape.
  // spend 10+20+15 = 45.00, пик = 20.00. Ловит регрессию агрегата (spend уже дельты).
  it("MONEY: Итого = сумма точек, Пик = максимум", () => {
    render(<StatsChartCard mode="hourly" points={HOURLY_POINTS} />);
    expect(screen.getByText("Итого").parentElement).toHaveTextContent("$45.00");
    expect(screen.getByText("Пик").parentElement).toHaveTextContent("$20.00");
  });

  // Режим daily с подневными точками — тоже рендерится без ошибок.
  it("рендерится в режиме daily без ошибок", () => {
    const dailyPoints = [
      { day: "2026-07-01", spend: "100.00", impressions: 1000, clicks: 50, leads: 20, registrations: 10, deposits: 3, active_ads: 8 },
      { day: "2026-07-02", spend: "120.00", impressions: 1200, clicks: 60, leads: 25, registrations: 12, deposits: 4, active_ads: 6 },
    ];
    expect(() => {
      render(<StatsChartCard mode="daily" points={dailyPoints} />);
    }).not.toThrow();
    expect(screen.getByTestId("chart-wrapper")).toBeInTheDocument();
  });
});
