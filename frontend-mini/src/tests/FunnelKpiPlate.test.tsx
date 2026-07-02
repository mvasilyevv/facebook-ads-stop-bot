/**
 * Тест FunnelKpiPlate: полные данные, «—» при null-производных, compact-режим.
 */
import { describe, it, expect } from "vitest";
import { render, screen } from "@testing-library/react";
import type { FunnelDerived, FunnelTotals } from "@fb/shared";
import { FunnelKpiPlate } from "@/components/domain/FunnelKpiPlate";

const TOTALS: FunnelTotals = {
  spend: "125.50",
  impressions: 10000,
  clicks: 400,
  leads: 40,
  registrations: 20,
  deposits: 5,
};

const DERIVED: FunnelDerived = {
  cpc: "0.31",
  cpl: "3.14",
  cpr: "6.28",
  cpa: "25.10",
  ctr_pct: "4.0",
  cr_click_lead_pct: "10.0",
  cr_lead_reg_pct: "50.0",
  cr_reg_dep_pct: "25.0",
};

describe("FunnelKpiPlate", () => {
  // Полный набор данных: спенд, клики, лиды, реги, депы, CPL — все отрисованы
  it("рендерит полную сетку 2×3 с данными воронки", () => {
    render(<FunnelKpiPlate data={{ totals: TOTALS, derived: DERIVED }} />);
    expect(screen.getByText("$125.50")).toBeInTheDocument();
    expect(screen.getByText("400")).toBeInTheDocument();
    expect(screen.getByText("40")).toBeInTheDocument();
    expect(screen.getByText("20")).toBeInTheDocument();
    expect(screen.getByText("5")).toBeInTheDocument();
    expect(screen.getByText("$3.14")).toBeInTheDocument();
  });

  // derived.cpl=null → CPL-плитка показывает «—» вместо суммы
  it("показывает «—» когда cpl равен null (деление на ноль на бэке)", () => {
    render(
      <FunnelKpiPlate
        data={{ totals: TOTALS, derived: { ...DERIVED, cpl: null } }}
      />,
    );
    expect(screen.getByText("—")).toBeInTheDocument();
  });

  // data=undefined + loading=true → скелетоны, без чисел
  it("рендерит скелетоны при loading=true без данных", () => {
    const { container } = render(<FunnelKpiPlate loading />);
    expect(container.querySelectorAll('[role="status"]').length).toBeGreaterThan(0);
    expect(screen.queryByText("$125.50")).not.toBeInTheDocument();
  });

  // compact=true → одна строка из 3 плиток (spend/лиды/CPL), без impressions/clicks/reg/dep
  it("compact-режим показывает только 3 плитки: spend, лиды, CPL", () => {
    render(<FunnelKpiPlate data={{ totals: TOTALS, derived: DERIVED }} compact />);
    expect(screen.getByText("$125.50")).toBeInTheDocument();
    expect(screen.getByText("40")).toBeInTheDocument();
    expect(screen.getByText("$3.14")).toBeInTheDocument();
    // impressions (10000) не должен рендериться в compact-режиме
    expect(screen.queryByText("10,000")).not.toBeInTheDocument();
  });
});
