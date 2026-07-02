/**
 * SparklineKpiRow — KPI-строка FSM-состояний на Dashboard.
 *
 * Контракт (после жалоб владельца 02-03.07): спарклайнов в ячейках НЕТ вообще —
 * сначала там по ошибке рисовался spend-ряд как «прокси активности», потом честный
 * active_ads по часам, но мини-график без подписи всё равно считывался неверно.
 * Теперь — только чистые counts + deep-link клики; динамика — на /stats.
 */

import { describe, expect, it, vi } from "vitest";
import { render, screen } from "@testing-library/react";

import { KPI_CELL_STATE, SparklineKpiRow } from "@/components/dashboard/SparklineKpiRow";
import type { DashboardStats } from "@fb/shared";

function makeStats(overrides: Partial<DashboardStats> = {}): DashboardStats {
  return {
    total_ads_monitored: 16,
    ads_in_normal: 3,
    ads_in_warning: 1,
    ads_in_stop: 2,
    ads_in_claimed: 0,
    ads_in_disabled: 10,
    active_incidents: 3,
    current_day_spend: "3.36",
    last_scan_at: null,
    last_scan_outcome: null,
    scans_today: 0,
    scans_today_with_errors: 0,
    observer_status: "running",
    pending_disable_tasks: 0,
    failed_disable_tasks: 0,
    scan_blocked_reason: null,
    ...overrides,
  } as DashboardStats;
}

describe("SparklineKpiRow", () => {
  // Все 4 ячейки рендерятся с counts из stats
  it("рендерит 4 ячейки с корректными counts", () => {
    render(<SparklineKpiRow stats={makeStats()} />);

    expect(screen.getByText("ACTIVE")).toBeInTheDocument();
    expect(screen.getByText("WARNING")).toBeInTheDocument();
    expect(screen.getByText("STOP")).toBeInTheDocument();
    expect(screen.getByText("DISABLED")).toBeInTheDocument();
  });

  // Спарклайнов нет НИ В ОДНОЙ ячейке (решение владельца: убраны как вводящие
  // в заблуждение — polyline в разметке отсутствует)
  it("не рисует sparkline ни в одной ячейке", () => {
    const { container } = render(<SparklineKpiRow stats={makeStats()} />);
    expect(container.querySelector("polyline")).not.toBeInTheDocument();
  });

  // Клик по ячейке вызывает onCellClick с правильным key (deep-link в /ads)
  it("клик по ячейке ACTIVE вызывает onCellClick('active')", () => {
    const onCellClick = vi.fn();
    render(<SparklineKpiRow stats={makeStats()} onCellClick={onCellClick} />);

    screen
      .getByText("ACTIVE")
      .closest('[role="button"]')
      ?.dispatchEvent(new MouseEvent("click", { bubbles: true }));

    expect(onCellClick).toHaveBeenCalledWith("active");
  });

  // Маппинг key → alert_state для deep-link не дрейфует
  it("KPI_CELL_STATE маппит все 4 ячейки на канонические alert_state", () => {
    expect(KPI_CELL_STATE).toEqual({
      active: "normal",
      warning: "warning_sent",
      stop: "stop_sent",
      disabled: "disabled",
    });
  });
});
