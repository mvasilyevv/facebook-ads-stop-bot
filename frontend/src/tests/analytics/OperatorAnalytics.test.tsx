import { fireEvent, render, screen, within } from "@testing-library/react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { vi } from "vitest";

import type {
  AnalyticsDaypart,
  AnalyticsLiveBudgetSeries,
  AnalyticsPerformanceRow,
} from "@fb/shared";
import { makeOperatorScopeEvidence } from "@fb/shared/operator/testFixture";

import { BudgetLineChart } from "@/components/analytics/BudgetLineChart";
import { DaypartHeatmap } from "@/components/analytics/DaypartHeatmap";
import { FunnelChart } from "@/components/analytics/FunnelChart";
import { PerformanceTable } from "@/components/analytics/PerformanceTable";

const analyticsSources = {
  meta: {
    source: "meta" as const,
    status: "good" as const,
    last_event_at: "2026-07-19T09:59:00Z",
    lag_seconds: 60,
    unmatched_events: 0,
    missing_timezone_account_ids: [],
    issues: [],
  },
  tracker: {
    source: "tracker" as const,
    status: "good" as const,
    last_event_at: "2026-07-19T09:59:00Z",
    lag_seconds: 60,
    unmatched_events: 0,
    missing_timezone_account_ids: [],
    issues: [],
  },
};

describe("operator analytics semantics", () => {
  it("renders unknown funnel values as unknown rather than confirmed zero", () => {
    render(
      <FunnelChart
        clicks={null}
        registrations={null}
        ftds={null}
        confirmedDeposits={null}
        spend={null}
        currency={null}
        timezone="UTC"
        asOf={null}
        completeness="unavailable"
        sources={[]}
      />,
    );

    expect(screen.getByText("Данные воронки не подтверждены источниками.")).toBeInTheDocument();
    expect(screen.getAllByText("—").length).toBeGreaterThan(3);
    expect(screen.queryByText("0")).not.toBeInTheDocument();
  });

  it("keeps count, conversion and cost visible without opening the data table", () => {
    render(
      <FunnelChart
        clicks={42}
        registrations={5}
        ftds={1}
        confirmedDeposits={1}
        spend="18.40"
        currency="USD"
        timezone="UTC"
        asOf="2026-07-19T10:00:00Z"
        completeness="ready"
        sources={["meta", "tracker"]}
      />,
    );

    const visual = screen.getByRole("group", {
      name: "Интерактивный график «Воронка»",
    });
    expect(visual).toHaveTextContent("42");
    expect(visual).toHaveTextContent(/CR 11\.9% · стоимость \$.*3\.68/);
    expect(visual).toHaveTextContent(/CR 20\.0% · стоимость \$.*18\.40/);
  });

  it("hides funnel money when the working currency is not USD", () => {
    render(
      <FunnelChart
        clicks={1}
        registrations={1}
        ftds={1}
        confirmedDeposits={1}
        spend="1.234"
        currency="KWD"
        timezone="UTC"
        asOf="2026-07-19T10:00:00Z"
        completeness="ready"
        sources={["meta", "tracker"]}
      />,
    );

    const visual = screen.getByRole("group", {
      name: "Интерактивный график «Воронка»",
    });
    expect(visual).not.toHaveTextContent(/KWD|1\.234/);
    expect(visual).toHaveTextContent(/стоимость —/);
  });

  it("exposes chart summary, timezone and an HTML data table", () => {
    const data: AnalyticsLiveBudgetSeries = {
      state: "empty",
      as_of: null,
      freshness_seconds: null,
      issues: [],
      sources: analyticsSources,
      scope: makeOperatorScopeEvidence(),
      window: {
        from_iso: "2026-07-19T00:00:00Z",
        to_iso: "2026-07-19T10:00:00Z",
        is_live: true,
        timezone: "UTC",
        timezone_known: false,
        timezone_state: "unknown",
        missing_timezone_account_ids: ["act_1"],
      },
      points: [],
    };
    render(<BudgetLineChart data={data} timezone="UTC" parentState="partial" />);

    expect(screen.getByText("Расход, база и stop-граница")).toBeInTheDocument();
    expect(screen.getByText("Часовой пояс: UTC")).toBeInTheDocument();
    expect(screen.getByText("Показать данные таблицей")).toBeInTheDocument();
    expect(
      screen.getByRole("table", {
        name: "Почасовой расход, база, stop и доступность источников",
      }),
    ).toBeInTheDocument();
  });

  it("keeps confirmed Meta spend when budget boundaries are unavailable", () => {
    const data: AnalyticsLiveBudgetSeries = {
      state: "partial",
      as_of: "2026-07-19T09:59:00Z",
      freshness_seconds: 60,
      issues: ["Budget rules unavailable"],
      sources: analyticsSources,
      scope: makeOperatorScopeEvidence(),
      window: {
        from_iso: "2026-07-19T00:00:00Z",
        to_iso: "2026-07-19T10:00:00Z",
        is_live: true,
        timezone: "UTC",
        timezone_known: true,
        timezone_state: "single",
        missing_timezone_account_ids: [],
      },
      points: [
        {
          ts: "2026-07-19T10:00:00Z",
          actual: "18.40",
          base: null,
          stop: null,
          available_ads: 0,
          unavailable_ads: 1,
        },
      ],
    };
    render(<BudgetLineChart data={data} timezone="UTC" parentState="ready" />);

    const table = screen.getByRole("table", {
      name: "Почасовой расход, база, stop и доступность источников",
    });
    const row = within(table).getByText("10:00").closest("tr");
    expect(row).toHaveTextContent(/\$.*18\.40/);
    expect(row).toHaveTextContent("—");
    expect(screen.getByText("Частично")).toBeInTheDocument();
  });

  it("hides monetary chart evidence when a single currency is not USD", () => {
    const data: AnalyticsLiveBudgetSeries = {
      state: "ready",
      as_of: "2026-07-19T09:59:00Z",
      freshness_seconds: 60,
      issues: [],
      sources: analyticsSources,
      scope: {
        ...makeOperatorScopeEvidence(),
        currency: "EUR",
      },
      window: {
        from_iso: "2026-07-19T00:00:00Z",
        to_iso: "2026-07-19T10:00:00Z",
        is_live: true,
        timezone: "UTC",
        timezone_known: true,
        timezone_state: "single",
        missing_timezone_account_ids: [],
      },
      points: [
        {
          ts: "2026-07-19T10:00:00Z",
          actual: "18.40",
          base: "15.00",
          stop: "30.00",
          available_ads: 1,
          unavailable_ads: 0,
        },
      ],
    };

    render(<BudgetLineChart data={data} timezone="UTC" parentState="ready" />);

    expect(
      screen.getByText("Денежные ряды скрыты: рабочая валюта не подтверждена как USD."),
    ).toBeInTheDocument();
    expect(screen.queryByText(/18\.40/)).not.toBeInTheDocument();
    expect(screen.queryByText(/EUR/)).not.toBeInTheDocument();
  });

  it("hides cached budget points when the parent snapshot is unavailable", () => {
    const data: AnalyticsLiveBudgetSeries = {
      state: "ready",
      as_of: "2026-07-19T09:59:00Z",
      freshness_seconds: 60,
      issues: [],
      sources: analyticsSources,
      scope: makeOperatorScopeEvidence(),
      window: {
        from_iso: "2026-07-19T00:00:00Z",
        to_iso: "2026-07-19T10:00:00Z",
        is_live: true,
        timezone: "UTC",
        timezone_known: true,
        timezone_state: "single",
        missing_timezone_account_ids: [],
      },
      points: [
        {
          ts: "2026-07-19T10:00:00Z",
          actual: "18.40",
          base: "15.00",
          stop: "30.00",
          available_ads: 1,
          unavailable_ads: 0,
        },
      ],
    };

    render(<BudgetLineChart data={data} timezone="UTC" parentState="unavailable" />);

    expect(
      screen.getByText("Точки расхода, базы и stop-границы не подтверждены и скрыты."),
    ).toBeInTheDocument();
    expect(screen.queryByText(/\$.*18\.40/)).not.toBeInTheDocument();
    expect(
      screen.getByRole("table", {
        name: "Почасовой расход, база, stop и доступность источников",
      }),
    ).toHaveTextContent("не подтверждено");
  });

  it("treats missing heatmap cells as unknown and exposes keyboard/touch inspection", () => {
    const data: AnalyticsDaypart = {
      state: "partial",
      as_of: "2026-07-19T00:00:00Z",
      freshness_seconds: 0,
      issues: ["Tracker evidence is sparse"],
      sources: analyticsSources,
      scope: makeOperatorScopeEvidence(),
      timezone: "Europe/Kaliningrad",
      from_iso: "2026-07-12T00:00:00Z",
      to_iso: "2026-07-19T00:00:00Z",
      cells: [{ weekday: 1, hour: 0, clicks: 0, registrations: null, ftds: null }],
    };
    render(<DaypartHeatmap data={data} parentState="partial" />);

    expect(screen.getByRole("combobox", { name: "День" })).toBeInTheDocument();
    const missingCells = screen.getAllByRole("button", {
      name: /Пн 01:00 · Регистрации: неизвестно/,
    });
    expect(missingCells.length).toBeGreaterThan(0);
    fireEvent.focus(missingCells[0]!);
    expect(screen.getByText(/Пн 01:00 · Регистрации: неизвестно/)).toBeInTheDocument();
    fireEvent.click(missingCells[0]!);
    expect(screen.getByText(/Пн 01:00 · Регистрации: неизвестно/)).toBeInTheDocument();

    const midnight = screen.getAllByRole("button", {
      name: /Пн 00:00 · Регистрации: неизвестно/,
    })[0]!;
    fireEvent.focus(midnight);
    fireEvent.keyDown(midnight, { key: "ArrowRight" });
    expect(document.activeElement).toBe(missingCells[0]);

    const table = screen.getByRole("table", { name: "Данные по дням недели и часам" });
    expect(within(table).getAllByRole("row")).toHaveLength(169);
    expect(within(table).getByText("0")).toBeInTheDocument();
    expect(within(table).getAllByText("—").length).toBeGreaterThan(400);
    expect(screen.getByText("Частично")).toBeInTheDocument();
  });

  it("hides cached heatmap cells when the parent snapshot is unavailable", () => {
    const data: AnalyticsDaypart = {
      state: "ready",
      as_of: "2026-07-19T00:00:00Z",
      freshness_seconds: 0,
      issues: [],
      sources: analyticsSources,
      scope: makeOperatorScopeEvidence(),
      timezone: "Europe/Kaliningrad",
      from_iso: "2026-07-12T00:00:00Z",
      to_iso: "2026-07-19T00:00:00Z",
      cells: [{ weekday: 1, hour: 0, clicks: 0, registrations: 0, ftds: 0 }],
    };

    render(<DaypartHeatmap data={data} parentState="unavailable" />);

    expect(screen.getAllByText("Нет подтверждённых почасовых данных.").length).toBeGreaterThan(0);
    const table = screen.getByRole("table", {
      name: "Данные по дням недели и часам",
    });
    expect(within(table).queryByText("0")).not.toBeInTheDocument();
    expect(within(table).getAllByText("—").length).toBeGreaterThan(400);
    expect(screen.getAllByText("Недоступно").length).toBeGreaterThan(0);
  });

  it("keeps exactly seven desktop columns for every analytics preset", () => {
    const onPreset = vi.fn();
    const table = (preset: "economy" | "funnel" | "delivery") => (
      <PerformanceTable
        rows={[]}
        currency="USD"
        params={{
          period: "today",
          level: "campaign",
          sort: "spend",
          direction: "desc",
          page: 1,
          page_size: 50,
        }}
        preset={preset}
        onPreset={onPreset}
        onSort={() => {}}
      />
    );
    const view = render(table("economy"));

    expect(
      screen.getByRole("table", {
        name: "Результаты по кампаниям, адсетам и объявлениям",
      }),
    ).toBeInTheDocument();
    expect(screen.getAllByRole("columnheader")).toHaveLength(7);
    fireEvent.click(screen.getByRole("button", { name: "Воронка" }));
    expect(onPreset).toHaveBeenLastCalledWith("funnel");
    view.rerender(table("funnel"));
    expect(screen.getAllByRole("columnheader")).toHaveLength(7);
    fireEvent.click(screen.getByRole("button", { name: "Доставка" }));
    expect(onPreset).toHaveBeenLastCalledWith("delivery");
    view.rerender(table("delivery"));
    expect(screen.getAllByRole("columnheader")).toHaveLength(7);
  });

  it("hides row metrics when the parent snapshot is unavailable", () => {
    const row: AnalyticsPerformanceRow = {
      id: "campaign-1",
      fb_id: "120001",
      name: "Campaign with cached metrics",
      level: "campaign",
      parent_id: null,
      parent_name: null,
      has_children: false,
      ad_account_id: "act_1",
      cabinet_timezone: "Europe/Kaliningrad",
      timezone_known: true,
      timezone_state: "single",
      offer_id: "offer-1",
      offer_code: "AVI",
      state: "ready",
      issues: [],
      spend: "18.40",
      impressions: 420,
      clicks: 42,
      leads: 5,
      registrations: 5,
      ftds: 1,
      confirmed_deposits: 1,
      redeposits: 0,
      revenue: "25.00",
      cpc: "0.44",
      ctr_pct: "10.00",
      click_registration_cr_pct: "11.90",
      registration_ftd_cr_pct: "20.00",
      cost_per_registration: "3.68",
      cost_per_ftd: "18.40",
      roi_pct: "35.87",
      roas: "1.36",
      live_budget: null,
      budget_unavailable_reason: null,
    };

    render(
      <QueryClientProvider client={new QueryClient()}>
        <PerformanceTable
          rows={[row]}
          parentState="unavailable"
          currency="USD"
          params={{
            period: "today",
            level: "campaign",
            sort: "spend",
            direction: "desc",
            page: 1,
            page_size: 50,
          }}
          preset="economy"
          onPreset={() => {}}
          onSort={() => {}}
        />
      </QueryClientProvider>,
    );

    expect(screen.getAllByText("Campaign with cached metrics")).toHaveLength(2);
    expect(screen.getAllByText("act_1")).toHaveLength(2);
    expect(screen.queryByText("act_act_1")).not.toBeInTheDocument();
    expect(screen.queryByText(/\$.*18\.40/)).not.toBeInTheDocument();
    expect(screen.getAllByText("—").length).toBeGreaterThan(0);
    expect(screen.getAllByText("Источник недоступен").length).toBeGreaterThan(0);
  });
});
