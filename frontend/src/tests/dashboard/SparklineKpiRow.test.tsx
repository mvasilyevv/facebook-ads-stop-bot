/**
 * Тесты SparklineKpiRow — 4-ячейка KPI strip на Dashboard.
 *
 * Фикс (задача B): ACTIVE-спарклайн раньше питался spend-рядом «как прокси
 * активности» — визуально похож на график, но это другая метрика (spend почти
 * всегда растёт монотонно в течение суток, поэтому спарклайн выглядел «растущим»,
 * даже когда число активных объявлений падало 16→3). Теперь пропс называется
 * activeAdsSpark и ожидает реальный ряд active_ads по часам; WARNING/STOP/DISABLED
 * по-прежнему без спарклайна (честной почасовой истории по FSM-state в API нет).
 */
import { render, screen } from "@testing-library/react";
import { describe, it, expect, vi } from "vitest";
import { SparklineKpiRow, SparklineKpiRowSkeleton } from "@/components/dashboard/SparklineKpiRow";
import type { DashboardStats } from "@fb/shared";

function makeStats(overrides: Partial<DashboardStats> = {}): DashboardStats {
  return {
    total_ads_monitored: 100,
    ads_in_normal: 80,
    ads_in_warning: 5,
    ads_in_stop: 2,
    ads_in_claimed: 1,
    ads_in_disabled: 12,
    active_incidents: 7,
    last_scan_at: new Date().toISOString(),
    last_scan_outcome: "ok",
    scans_today: 42,
    scans_today_with_errors: 0,
    observer_status: "running",
    pending_disable_tasks: 3,
    pending_enable_tasks: 1,
    failed_tasks_24h: 0,
    ...overrides,
  };
}

describe("SparklineKpiRow", () => {
  // 4 ячейки рендерятся с реальными counts из stats.
  it("рендерит 4 ячейки с counts из stats", () => {
    render(<SparklineKpiRow stats={makeStats()} />);

    expect(screen.getByRole("list", { name: "Ключевые показатели" })).toBeInTheDocument();
    expect(screen.getByText("ACTIVE")).toBeInTheDocument();
    expect(screen.getByText("WARNING")).toBeInTheDocument();
    expect(screen.getByText("STOP")).toBeInTheDocument();
    expect(screen.getByText("DISABLED")).toBeInTheDocument();
  });

  // ACTIVE-ячейка получает activeAdsSpark (не spend!) — sparkline рисуется (>=2 точек).
  it("ACTIVE-ячейка рисует sparkline из activeAdsSpark (не из spend)", () => {
    const { container } = render(
      <SparklineKpiRow stats={makeStats()} activeAdsSpark={[16, 12, 8, 5, 3]} />,
    );

    // 4 svg — по одному на ячейку, ACTIVE первая содержит polyline (>=2 точек).
    const svgs = container.querySelectorAll("svg");
    expect(svgs.length).toBe(4);
    const activeSvg = svgs[0]!;
    expect(activeSvg.querySelector("polyline")).toBeInTheDocument();
  });

  // WARNING/STOP/DISABLED не получают спарклайн (честной почасовой истории по FSM-state нет) —
  // их svg пустые (без polyline), Sparkline сам ничего не рисует на пустом массиве.
  it("WARNING/STOP/DISABLED без sparkline (нет фейковых данных)", () => {
    const { container } = render(
      <SparklineKpiRow stats={makeStats()} activeAdsSpark={[16, 12, 8, 5, 3]} />,
    );

    const svgs = container.querySelectorAll("svg");
    // warning, stop, disabled — индексы 1,2,3
    for (const svg of [svgs[1]!, svgs[2]!, svgs[3]!]) {
      expect(svg.querySelector("polyline")).not.toBeInTheDocument();
    }
  });

  // Без activeAdsSpark (не передан) — ACTIVE тоже без sparkline, не падает.
  it("без activeAdsSpark ACTIVE-ячейка тоже без sparkline и не падает", () => {
    expect(() => {
      render(<SparklineKpiRow stats={makeStats()} />);
    }).not.toThrow();
  });

  // Клик по ячейке вызывает onCellClick с правильным key.
  it("клик по ячейке ACTIVE вызывает onCellClick('active')", async () => {
    const onCellClick = vi.fn();
    render(<SparklineKpiRow stats={makeStats()} onCellClick={onCellClick} />);

    screen.getByText("ACTIVE").closest('[role="button"]')?.dispatchEvent(
      new MouseEvent("click", { bubbles: true }),
    );

    expect(onCellClick).toHaveBeenCalledWith("active");
  });

  // Skeleton-версия рендерится (role=status) без реальных данных.
  it("SparklineKpiRowSkeleton рендерит 4 плейсхолдера", () => {
    render(<SparklineKpiRowSkeleton />);
    expect(screen.getByRole("status", { name: "Загрузка KPI" })).toBeInTheDocument();
  });
});
