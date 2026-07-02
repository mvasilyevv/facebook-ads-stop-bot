/**
 * Тест TrackerBlockMini: available=true рендерит метрики, available=false —
 * пустое состояние «Нет данных трекера».
 */
import { describe, it, expect } from "vitest";
import { render, screen } from "@testing-library/react";
import { TrackerBlockMini, type TrackerBlock } from "@/components/domain/TrackerBlockMini";

const TRACKER_AVAILABLE: TrackerBlock = {
  available: true,
  day_utc: "2026-07-02",
  attribution_note: "Трекер считает по UTC-дню — расхождение с Meta нормально.",
  totals: {
    installs: 100,
    registrations: 30,
    deposits: 8,
    revenue: "640.00",
    roi_pct: "12.5",
  },
  series_daily: [],
};

const TRACKER_UNAVAILABLE: TrackerBlock = {
  available: false,
  day_utc: null,
  attribution_note: "",
  totals: { installs: 0, registrations: 0, deposits: 0, revenue: null, roi_pct: null },
  series_daily: [],
};

describe("TrackerBlockMini", () => {
  // available=true — показывает реги/депы/revenue/ROI + attribution_note
  it("рендерит метрики трекера при available=true", () => {
    render(<TrackerBlockMini tracker={TRACKER_AVAILABLE} />);
    expect(screen.getByText("30")).toBeInTheDocument();
    expect(screen.getByText("8")).toBeInTheDocument();
    expect(screen.getByText("$640.00")).toBeInTheDocument();
    expect(screen.getByText("12.5%")).toBeInTheDocument();
    expect(screen.getByText(/расхождение с Meta нормально/)).toBeInTheDocument();
  });

  // available=false — «Нет данных трекера», метрики не рендерятся
  it("показывает «Нет данных трекера» при available=false", () => {
    render(<TrackerBlockMini tracker={TRACKER_UNAVAILABLE} />);
    expect(screen.getByText("Нет данных трекера")).toBeInTheDocument();
    expect(screen.queryByText("$640.00")).not.toBeInTheDocument();
  });

  // tracker=undefined (данные ещё не пришли, но loading=false) — тоже пустое состояние
  it("показывает пустое состояние когда tracker не передан", () => {
    render(<TrackerBlockMini />);
    expect(screen.getByText("Нет данных трекера")).toBeInTheDocument();
  });

  // loading=true — скелетон, без EmptyState и без метрик
  it("рендерит скелетон при loading=true", () => {
    const { container } = render(<TrackerBlockMini loading />);
    expect(container.querySelectorAll('[role="status"]').length).toBeGreaterThan(0);
    expect(screen.queryByText("Нет данных трекера")).not.toBeInTheDocument();
  });
});
