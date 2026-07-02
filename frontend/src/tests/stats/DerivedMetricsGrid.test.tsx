/**
 * Тесты DerivedMetricsGrid — сетка производных метрик (CPC/CPL/CPR/CPA/CTR/CR-ступени).
 */
import { render, screen } from "@testing-library/react";
import { describe, it, expect } from "vitest";
import { DerivedMetricsGrid } from "@/components/stats/DerivedMetricsGrid";
import type { FunnelDerived } from "@fb/shared";

const DERIVED: FunnelDerived = {
  cpc: "0.25",
  cpl: "3.09",
  cpr: "6.17",
  cpa: "24.69",
  ctr_pct: "5.0",
  cr_click_lead_pct: "8.0",
  cr_lead_reg_pct: "50.0",
  cr_reg_dep_pct: "25.0",
};

describe("DerivedMetricsGrid", () => {
  // Все 8 метрик рендерятся с реальными значениями (денежные — formatSpend, % — formatPercentValue).
  it("рендерит 8 метрик с реальными значениями", () => {
    render(<DerivedMetricsGrid data={DERIVED} />);

    expect(screen.getByText("$0.25")).toBeInTheDocument(); // CPC
    expect(screen.getByText("$3.09")).toBeInTheDocument(); // CPL
    expect(screen.getByText("$6.17")).toBeInTheDocument(); // CPR
    expect(screen.getByText("$24.69")).toBeInTheDocument(); // CPA
    expect(screen.getByText("5.0%")).toBeInTheDocument(); // CTR
    expect(screen.getByText("8.0%")).toBeInTheDocument(); // CR клик→лид
    expect(screen.getByText("50.0%")).toBeInTheDocument(); // CR лид→рег
    expect(screen.getByText("25.0%")).toBeInTheDocument(); // CR рег→деп
  });

  // Знаменатель нулевой на бэке → все поля null → «—» везде, без фейковых нулей.
  it("null-метрики рендерятся как «—»", () => {
    const nullDerived: FunnelDerived = {
      cpc: null,
      cpl: null,
      cpr: null,
      cpa: null,
      ctr_pct: null,
      cr_click_lead_pct: null,
      cr_lead_reg_pct: null,
      cr_reg_dep_pct: null,
    };
    render(<DerivedMetricsGrid data={nullDerived} />);

    const dashes = screen.getAllByText("—");
    expect(dashes.length).toBe(8);
  });

  // loading=true — skeleton, не бросает.
  it("рендерит skeleton при loading=true", () => {
    render(<DerivedMetricsGrid loading />);
    expect(screen.getByRole("status", { name: "Загрузка производных метрик" })).toBeInTheDocument();
  });
});
