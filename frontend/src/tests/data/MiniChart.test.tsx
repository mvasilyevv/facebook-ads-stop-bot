/**
 * Тесты MiniChart — smoke + danger/accent tint + пустые данные.
 */
import { render, screen } from "@testing-library/react";
import { describe, it, expect, beforeAll } from "vitest";
import { MiniChart } from "@/components/data/charts/MiniChart";
import type { MiniChartPoint } from "@/components/data/charts/MiniChart";

// Mock ResizeObserver — jsdom не поддерживает, нужен класс-конструктор
beforeAll(() => {
  class MockResizeObserver {
    private cb: ResizeObserverCallback;
    constructor(cb: ResizeObserverCallback) {
      this.cb = cb;
    }
    observe(_el: Element) {
      this.cb(
        [{ contentRect: { width: 580, height: 120 } } as ResizeObserverEntry],
        this as unknown as ResizeObserver,
      );
    }
    unobserve() {}
    disconnect() {}
  }
  globalThis.ResizeObserver = MockResizeObserver as unknown as typeof ResizeObserver;
});

const DATA: MiniChartPoint[] = [
  { label: "10:00", spend: 70 },
  { label: "11:00", spend: 85 },
  { label: "12:00", spend: 120 },
];

describe("MiniChart", () => {
  // Smoke-тест: danger tint (default)
  it("рендерится без ошибок с danger tint", () => {
    expect(() => {
      render(<MiniChart data={DATA} />);
    }).not.toThrow();
  });

  // Accent tint
  it("рендерится без ошибок с accent tint", () => {
    expect(() => {
      render(<MiniChart data={DATA} tint="accent" />);
    }).not.toThrow();
  });

  // Пустые данные — не падает
  it("не падает на пустых данных", () => {
    expect(() => {
      render(<MiniChart data={[]} />);
    }).not.toThrow();
  });

  // aria-label передаётся
  it("aria-label доступен через role=img", () => {
    render(<MiniChart data={DATA} aria-label="Spend за 6h" />);
    expect(screen.getByRole("img", { name: "Spend за 6h" })).toBeInTheDocument();
  });

  // Кастомная высота не ломает
  it("кастомная height не ломает", () => {
    expect(() => {
      render(<MiniChart data={DATA} height={80} />);
    }).not.toThrow();
  });
});
