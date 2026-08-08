import { describe, expect, it } from "vitest";

import {
  AnalyticsPayloadError,
  normalizeAnalyticsDaypart,
  normalizeAnalyticsLiveBudgetSeries,
  normalizeAnalyticsPerformance,
} from "../runtime";

const SCOPE = {
  account_ids: ["123"],
  display_timezone: "Europe/Kaliningrad",
  cabinet_timezone: "Europe/Kaliningrad",
  cabinet_timezone_state: "single",
  missing_timezone_account_ids: [],
  currency: "USD",
  currency_state: "single",
  missing_currency_account_ids: [],
  currency_observed_at: "2026-07-29T10:00:00Z",
};

const SOURCES = {
  meta: {
    source: "meta",
    status: "good",
    last_event_at: "2026-07-29T10:00:00Z",
    lag_seconds: 0,
    unmatched_events: 0,
    timezone_known: true,
    missing_timezone_account_ids: [],
    issues: [],
    note: null,
  },
  tracker: {
    source: "tracker",
    status: "good",
    last_event_at: "2026-07-29T10:00:00Z",
    lag_seconds: 0,
    unmatched_events: 0,
    timezone_known: true,
    missing_timezone_account_ids: [],
    issues: [],
    note: null,
  },
};

const WINDOW = {
  from_iso: "2026-07-29T00:00:00Z",
  to_iso: "2026-07-29T10:00:00Z",
  is_live: true,
  timezone: "Europe/Kaliningrad",
  timezone_known: true,
  timezone_state: "single",
  missing_timezone_account_ids: [],
  issues: [],
  cabinet_day_note: "Сутки кабинета",
};

const METRICS = {
  spend: "10.25",
  impressions: 100,
  clicks: 10,
  leads: 4,
  registrations: 3,
  ftds: 1,
  confirmed_deposits: 1,
  redeposits: 0,
  revenue: "20.00",
  cpc: "1.025",
  ctr_pct: "10",
  click_registration_cr_pct: "30",
  registration_ftd_cr_pct: "33.333",
  cost_per_registration: "3.416",
  cost_per_ftd: "10.25",
  roi_pct: "95.122",
  roas: "1.951",
};

function performance() {
  return {
    state: "ready",
    as_of: "2026-07-29T10:00:00Z",
    freshness_seconds: 0,
    issues: [],
    scope: { ...SCOPE },
    window: { ...WINDOW },
    sources: structuredClone(SOURCES),
    totals: { ...METRICS },
    total_live_budget: null,
    total_budget_unavailable_reason: null,
    pagination: { page: 1, page_size: 10, total: 1, pages: 1 },
    filter_options: { accounts: [], offers: [], campaigns: [] },
    rows: [
      {
        ...METRICS,
        id: "campaign-1",
        fb_id: "120001",
        name: "Campaign 1",
        level: "campaign",
        parent_id: null,
        parent_name: null,
        has_children: true,
        ad_account_id: "123",
        cabinet_timezone: "Europe/Kaliningrad",
        timezone_known: true,
        timezone_state: "single",
        offer_id: null,
        offer_code: null,
        state: "ready",
        issues: [],
        live_budget: null,
        budget_unavailable_reason: null,
      },
    ],
  };
}

function daypart() {
  return {
    state: "ready",
    as_of: "2026-07-29T10:00:00Z",
    freshness_seconds: 0,
    issues: [],
    scope: { ...SCOPE },
    sources: structuredClone(SOURCES),
    timezone: "Europe/Kaliningrad",
    from_iso: "2026-07-22T00:00:00Z",
    to_iso: "2026-07-29T10:00:00Z",
    cells: [
      { weekday: 2, hour: 10, clicks: 5, registrations: 2, ftds: 1 },
    ],
  };
}

describe("analytics runtime account-context boundary", () => {
  it("accepts a complete server-confirmed single-currency response", () => {
    const payload = performance();
    expect(normalizeAnalyticsPerformance(payload)).toBe(payload);
  });

  it("accepts a reviewed backend currency absent from Intl.supportedValuesOf", () => {
    const valid = performance();
    const payload = {
      ...valid,
      scope: { ...valid.scope, currency: "VED" },
    };

    expect(normalizeAnalyticsPerformance(payload)).toBe(payload);
  });

  it("rejects absent, unsupported and contradictory scope evidence", () => {
    const valid = performance();
    for (const scope of [
      undefined,
      { ...SCOPE, currency: "ZZZ" },
      { ...SCOPE, currency_state: "mixed", currency: "USD" },
      {
        ...SCOPE,
        cabinet_timezone_state: "unknown",
        cabinet_timezone: "Europe/Kaliningrad",
      },
    ]) {
      expect(() =>
        normalizeAnalyticsPerformance({ ...valid, scope }),
      ).toThrow(AnalyticsPayloadError);
    }
  });

  it("allows counts but rejects every money value without one currency", () => {
    const valid = performance();
    const scope = {
      ...SCOPE,
      currency: null,
      currency_state: "unknown",
      missing_currency_account_ids: ["123"],
      currency_observed_at: null,
    };
    const nullMoney = {
      ...METRICS,
      spend: null,
      revenue: null,
      cpc: null,
      cost_per_registration: null,
      cost_per_ftd: null,
      roi_pct: null,
      roas: null,
    };
    const failClosed = {
      ...valid,
      state: "partial",
      scope,
      totals: nullMoney,
      rows: [
        {
          ...valid.rows[0],
          ...nullMoney,
          state: "partial",
          issues: ["Валюта не подтверждена"],
        },
      ],
    };
    expect(normalizeAnalyticsPerformance(failClosed)).toBe(failClosed);
    expect(() =>
      normalizeAnalyticsPerformance({
        ...failClosed,
        totals: { ...nullMoney, spend: "10.25" },
      }),
    ).toThrow(AnalyticsPayloadError);
  });

  it("requires scope and fail-closed money in live-budget responses", () => {
    const valid = {
      state: "ready",
      as_of: "2026-07-29T10:00:00Z",
      freshness_seconds: 0,
      issues: [],
      scope: { ...SCOPE },
      window: { ...WINDOW },
      sources: structuredClone(SOURCES),
      points: [
        {
          ts: "2026-07-29T10:00:00Z",
          actual: "10.25",
          base: "8.00",
          stop: "12.00",
          available_ads: 1,
          unavailable_ads: 0,
        },
      ],
    };
    expect(normalizeAnalyticsLiveBudgetSeries(valid)).toBe(valid);
    expect(() =>
      normalizeAnalyticsLiveBudgetSeries({ ...valid, scope: undefined }),
    ).toThrow(AnalyticsPayloadError);
    expect(() =>
      normalizeAnalyticsLiveBudgetSeries({
        ...valid,
        state: "partial",
        scope: {
          ...SCOPE,
          currency: null,
          currency_state: "unknown",
          missing_currency_account_ids: ["123"],
          currency_observed_at: null,
        },
      }),
    ).toThrow(AnalyticsPayloadError);
  });

  it("requires account-context evidence for daypart counts", () => {
    const valid = daypart();
    expect(normalizeAnalyticsDaypart(valid)).toBe(valid);
    expect(() =>
      normalizeAnalyticsDaypart({ ...valid, scope: undefined }),
    ).toThrow(AnalyticsPayloadError);
  });

  it("rejects invalid or contradictory daypart timezones", () => {
    const valid = daypart();
    for (const payload of [
      { ...valid, timezone: "Mars/Olympus" },
      {
        ...valid,
        timezone: "UTC",
      },
    ]) {
      expect(() => normalizeAnalyticsDaypart(payload)).toThrow(
        AnalyticsPayloadError,
      );
    }
  });

  it("rejects non-RFC3339, impossible and reversed timestamps", () => {
    const valid = performance();
    for (const asOf of [
      "0",
      "2026-07-29",
      "2026-07-29T12:00:00",
      "2026-02-30T00:00:00Z",
    ]) {
      expect(() =>
        normalizeAnalyticsPerformance({ ...valid, as_of: asOf }),
      ).toThrow(AnalyticsPayloadError);
    }
    expect(() =>
      normalizeAnalyticsPerformance({
        ...valid,
        window: {
          ...valid.window,
          from_iso: "2026-07-30T00:00:00Z",
          to_iso: "2026-07-29T00:00:00Z",
        },
      }),
    ).toThrow(AnalyticsPayloadError);
    const validDaypart = daypart();
    expect(() =>
      normalizeAnalyticsDaypart({
        ...validDaypart,
        from_iso: validDaypart.to_iso,
        to_iso: validDaypart.from_iso,
      }),
    ).toThrow(AnalyticsPayloadError);
  });

  it("rejects a ready page outside the confirmed pagination range", () => {
    const valid = performance();
    expect(() =>
      normalizeAnalyticsPerformance({
        ...valid,
        pagination: { ...valid.pagination, page: 2 },
        rows: [],
      }),
    ).toThrow(AnalyticsPayloadError);
  });
});
