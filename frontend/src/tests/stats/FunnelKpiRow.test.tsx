/**
 * Тесты FunnelKpiRow — KPI-строка воронки залива (spend/клики/лиды/реги/депы).
 */
import { render, screen } from "@testing-library/react";
import { describe, it, expect } from "vitest";
import { FunnelKpiRow } from "@/components/stats/FunnelKpiRow";
import type { FunnelDerived, FunnelTotals } from "@fb/shared";

const TOTALS: FunnelTotals = {
  spend: "123.45",
  impressions: 10000,
  clicks: 500,
  leads: 40,
  registrations: 20,
  deposits: 5,
};

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

describe("FunnelKpiRow", () => {
  // Полный набор данных — 5 ячеек full-режима со значениями из totals/derived.
  it("рендерит 5 ячеек в full-режиме с реальными данными", () => {
    render(<FunnelKpiRow data={{ totals: TOTALS, derived: DERIVED }} />);

    const list = screen.getByRole("list", { name: "Воронка залива" });
    expect(list).toBeInTheDocument();
    expect(screen.getByText("$123.45")).toBeInTheDocument();
    expect(screen.getByText("500")).toBeInTheDocument();
    expect(screen.getByText("40")).toBeInTheDocument();
    expect(screen.getByText("20")).toBeInTheDocument();
    expect(screen.getByText("5")).toBeInTheDocument();
    // CPL/CPA попадают в note-строку ячеек «Лиды»/«Депозиты».
    expect(screen.getByText(/CPL \$3\.09/)).toBeInTheDocument();
    expect(screen.getByText(/CPA \$24\.69/)).toBeInTheDocument();
  });

  // compact-режим — 4 ячейки (spend/лиды/реги/депы), без CPL/CPA note и без клика.
  it("compact-режим рендерит 4 ячейки без CPL/CPA note", () => {
    render(<FunnelKpiRow data={{ totals: TOTALS, derived: DERIVED }} compact />);

    expect(screen.queryByText(/CPL/)).not.toBeInTheDocument();
    expect(screen.queryByText(/CPA/)).not.toBeInTheDocument();
    expect(screen.getByText("$123.45")).toBeInTheDocument();
    expect(screen.getByText("40")).toBeInTheDocument();
  });

  // Нулевой знаменатель на бэке → derived-поля null → formatSpend отдаёт «—» (no-fake-data).
  it("null-производные метрики рендерятся как «—»", () => {
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
    render(<FunnelKpiRow data={{ totals: TOTALS, derived: nullDerived }} />);

    expect(screen.getByText(/CPL —/)).toBeInTheDocument();
    expect(screen.getByText(/CPA —/)).toBeInTheDocument();
  });

  // loading=true (или отсутствие data) — рендерит skeleton (role=status), не бросает.
  it("рендерит skeleton при loading=true", () => {
    render(<FunnelKpiRow loading />);
    expect(screen.getByRole("status", { name: "Загрузка воронки" })).toBeInTheDocument();
  });

  // Отсутствие data (undefined) без явного loading — тоже skeleton (защита от undefined).
  it("рендерит skeleton когда data не передана", () => {
    render(<FunnelKpiRow />);
    expect(screen.getByRole("status", { name: "Загрузка воронки" })).toBeInTheDocument();
  });
});
