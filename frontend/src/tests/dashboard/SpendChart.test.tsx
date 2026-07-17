import { render } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it } from "vitest";

import { SpendChart, type SpendChartPoint } from "@/components/dashboard/SpendChart";

let observedWidth = 420;

class MockResizeObserver {
  constructor(private readonly callback: ResizeObserverCallback) {}

  observe() {
    this.callback(
      [{ contentRect: { width: observedWidth, height: 170 } } as ResizeObserverEntry],
      this as unknown as ResizeObserver,
    );
  }

  unobserve() {}
  disconnect() {}
}

function points(count: number): SpendChartPoint[] {
  return Array.from({ length: count }, (_, index) => ({
    ts: new Date(Date.UTC(2026, 6, 16, index)).toISOString(),
    spend: 3 + index * 0.75,
  }));
}

describe("SpendChart responsive time axis", () => {
  beforeEach(() => {
    globalThis.ResizeObserver = MockResizeObserver as unknown as typeof ResizeObserver;
  });

  afterEach(() => {
    observedWidth = 420;
  });

  it.each([18, 24])("keeps %s timestamp points inside a narrow card", (count) => {
    const { container } = render(<SpendChart data={points(count)} live={false} />);
    const wrapper = container.firstElementChild;
    const labels = [...container.querySelectorAll("svg text")];

    expect(wrapper).toHaveClass("overflow-hidden");
    expect(labels).toHaveLength(3);
    for (const label of labels) {
      const x = Number(label.getAttribute("x"));
      expect(x).toBeGreaterThanOrEqual(8);
      expect(x).toBeLessThanOrEqual(observedWidth - 8);
    }
  });

  it.each([1280, 1440, 1920])("derives ticks from a %spx card width", (width) => {
    observedWidth = width;
    const { container } = render(<SpendChart data={points(24)} live={false} />);
    const labels = [...container.querySelectorAll("svg text")];

    expect(labels.length).toBe(Math.floor(width / 110));
    expect(Number(labels.at(0)?.getAttribute("x"))).toBe(8);
    expect(Number(labels.at(-1)?.getAttribute("x"))).toBe(width - 8);
  });
});
