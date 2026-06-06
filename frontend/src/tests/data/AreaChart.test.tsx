/**
 * Тесты AreaChart — Recharts area-график.
 * ResponsiveContainer в jsdom возвращает width=0 — мокируем его размер через
 * ResizeObserver stub + contentRect override (стандартный паттерн для Recharts в jsdom).
 */
import { render, screen } from "@testing-library/react";
import { describe, it, expect, beforeAll } from "vitest";
import { AreaChart } from "@/components/data/charts/AreaChart";
import type { AreaDataPoint } from "@/components/data/charts/AreaChart";

// Mock ResizeObserver — jsdom его не поддерживает, Recharts требует конструктор
beforeAll(() => {
  // Нужен class (конструктор), vi.fn() без class не работает в vitest
  class MockResizeObserver {
    private cb: ResizeObserverCallback;
    constructor(cb: ResizeObserverCallback) {
      this.cb = cb;
    }
    observe(_el: Element) {
      // Эмитим размер сразу — ResponsiveContainer получит width>0
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

const DATA: AreaDataPoint[] = [
  { ts: "2026-06-06T10:00:00Z", label: "10:00", spend: 100, leads: 5 },
  { ts: "2026-06-06T11:00:00Z", label: "11:00", spend: 200, leads: 10 },
  { ts: "2026-06-06T12:00:00Z", label: "12:00", spend: 150, leads: 7 },
];

describe("AreaChart", () => {
  // График рендерится с данными без ошибок
  it("рендерится с данными", () => {
    expect(() => {
      render(<AreaChart data={DATA} />);
    }).not.toThrow();
  });

  // ChartWrapper присутствует
  it("содержит ChartWrapper (data-testid)", () => {
    render(<AreaChart data={DATA} />);
    expect(screen.getByTestId("chart-wrapper")).toBeInTheDocument();
  });

  // Рендерится с пустым массивом — не падает
  it("не падает на пустых данных", () => {
    expect(() => {
      render(<AreaChart data={[]} />);
    }).not.toThrow();
  });

  // Рендерится с одной точкой
  it("рендерится с одной точкой", () => {
    const single = DATA.slice(0, 1);
    expect(() => {
      render(<AreaChart data={single} />);
    }).not.toThrow();
  });

  // Кастомный yTickFormatter применяется
  it("принимает кастомный yTickFormatter", () => {
    expect(() => {
      render(
        <AreaChart
          data={DATA}
          yTickFormatter={(v) => `€${v}`}
        />,
      );
    }).not.toThrow();
  });

  // showPeak=false не ломает
  it("showPeak=false рендерится корректно", () => {
    expect(() => {
      render(<AreaChart data={DATA} showPeak={false} />);
    }).not.toThrow();
  });
});
