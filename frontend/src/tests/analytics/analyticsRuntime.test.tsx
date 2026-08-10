import type { ReactNode } from "react";
import { QueryClient, QueryClientProvider, useQuery } from "@tanstack/react-query";
import { renderHook, waitFor } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";

const generatedUseQuery = vi.hoisted(() => vi.fn());

vi.mock("@/lib/api/generatedClient", () => ({
  generatedApi: { useQuery: generatedUseQuery },
}));

import {
  AnalyticsPayloadError,
  normalizeAnalyticsDaypart,
  normalizeAnalyticsLiveBudgetSeries,
  normalizeAnalyticsPerformance,
} from "@fb/shared/analytics/runtime";
import { useAnalyticsPerformance } from "@/lib/api/analytics";

import { makeAnalyticsPerformanceFixture } from "./analyticsFixture";

function wrapper(client: QueryClient) {
  return function QueryWrapper({ children }: { children: ReactNode }) {
    return <QueryClientProvider client={client}>{children}</QueryClientProvider>;
  };
}

describe("analytics performance runtime contract", () => {
  beforeEach(() => {
    generatedUseQuery.mockReset();
  });

  it("accepts a complete generated-contract payload without changing known zeroes", () => {
    const payload = makeAnalyticsPerformanceFixture();
    payload.totals.redeposits = 0;

    const result = normalizeAnalyticsPerformance(payload);

    expect(result.sources.meta.status).toBe("good");
    expect(result.totals.redeposits).toBe(0);
    expect(result.pagination.pages).toBe(1);
  });

  it("preserves explicit unknown metrics instead of coercing them to zero", () => {
    const payload = makeAnalyticsPerformanceFixture();
    payload.totals.spend = null;
    payload.totals.clicks = null;
    payload.state = "partial";

    const result = normalizeAnalyticsPerformance(payload);

    expect(result.totals.spend).toBeNull();
    expect(result.totals.clicks).toBeNull();
    expect(result.state).toBe("partial");
  });

  it.each([
    {},
    { ...makeAnalyticsPerformanceFixture(), sources: {} },
    {
      ...makeAnalyticsPerformanceFixture(),
      totals: Object.fromEntries(
        Object.entries(makeAnalyticsPerformanceFixture().totals).filter(([key]) => key !== "spend"),
      ),
    },
  ])("rejects a successful HTTP payload missing required analytics fields", (payload) => {
    expect(() => normalizeAnalyticsPerformance(payload)).toThrow(AnalyticsPayloadError);
  });

  it("rejects ready analytics without freshness evidence or a confirmed result set", () => {
    const valid = makeAnalyticsPerformanceFixture();

    for (const invalid of [
      { ...valid, as_of: null },
      { ...valid, freshness_seconds: null },
      { ...valid, issues: ["Источник не подтверждён"] },
      {
        ...valid,
        sources: {
          ...valid.sources,
          meta: { ...valid.sources.meta, status: "missing" },
        },
      },
      {
        ...valid,
        sources: {
          ...valid.sources,
          tracker: {
            ...valid.sources.tracker,
            issues: ["Tracker отстаёт"],
          },
        },
      },
      {
        ...valid,
        sources: {
          ...valid.sources,
          meta: { ...valid.sources.meta, last_event_at: null },
        },
      },
      {
        ...valid,
        sources: {
          ...valid.sources,
          tracker: { ...valid.sources.tracker, lag_seconds: null },
        },
      },
      {
        ...valid,
        window: { ...valid.window, timezone_known: false },
      },
      {
        ...valid,
        totals: { ...valid.totals, registrations: null },
      },
      {
        ...valid,
        rows: [{ ...valid.rows[0]!, state: "partial" }],
      },
      {
        ...valid,
        rows: [{ ...valid.rows[0]!, issues: ["Строка неполна"] }],
      },
      {
        ...valid,
        rows: [{ ...valid.rows[0]!, spend: null }],
      },
      {
        ...valid,
        rows: [],
        pagination: { page: 1, page_size: 50, total: 0, pages: 0 },
      },
      {
        ...valid,
        pagination: { ...valid.pagination, pages: 2 },
      },
    ]) {
      expect(() => normalizeAnalyticsPerformance(invalid)).toThrow(AnalyticsPayloadError);
    }
  });

  it("rejects an empty analytics state that still carries rows", () => {
    const valid = makeAnalyticsPerformanceFixture();

    expect(() =>
      normalizeAnalyticsPerformance({
        ...valid,
        state: "empty",
      }),
    ).toThrow(AnalyticsPayloadError);
  });

  it("wires the runtime normalizer into the generated OpenAPI query", () => {
    generatedUseQuery.mockReturnValue({ data: undefined });

    renderHook(() =>
      useAnalyticsPerformance({
        period: "today",
        level: "campaign",
        page: 1,
        page_size: 50,
      }),
    );

    const options = generatedUseQuery.mock.calls[0]?.[3] as {
      select: (payload: unknown) => unknown;
    };
    expect(options.select(makeAnalyticsPerformanceFixture())).toEqual(
      makeAnalyticsPerformanceFixture(),
    );
    expect(() => options.select({})).toThrow(AnalyticsPayloadError);
  });

  it("turns malformed decoded JSON into a query error instead of typed data", async () => {
    const client = new QueryClient({
      defaultOptions: { queries: { retry: false } },
    });

    const { result } = renderHook(
      () =>
        useQuery({
          queryKey: ["analytics-runtime-malformed"],
          queryFn: async () => ({}),
          select: normalizeAnalyticsPerformance,
        }),
      { wrapper: wrapper(client) },
    );

    await waitFor(() => expect(result.current.isError).toBe(true));
    expect(result.current.data).toBeUndefined();
    expect(result.current.error).toBeInstanceOf(AnalyticsPayloadError);
    expect(result.current.error).toHaveProperty(
      "message",
      expect.stringContaining("Неподтверждённые значения скрыты"),
    );
  });

  it("accepts nullable budget lines and sparse nullable daypart cells", () => {
    const section = {
      state: "partial",
      as_of: "2026-07-21T09:59:40Z",
      freshness_seconds: 20,
      issues: ["Неполное покрытие"],
      sources: makeAnalyticsPerformanceFixture().sources,
      scope: makeAnalyticsPerformanceFixture().scope,
    } as const;

    expect(
      normalizeAnalyticsLiveBudgetSeries({
        ...section,
        window: makeAnalyticsPerformanceFixture().window,
        points: [
          {
            ts: "2026-07-21T09:00:00Z",
            actual: "18.40",
            base: null,
            stop: null,
            available_ads: 0,
            unavailable_ads: 1,
          },
        ],
      }).points[0]?.base,
    ).toBeNull();

    expect(
      normalizeAnalyticsDaypart({
        ...section,
        timezone: "Europe/Kaliningrad",
        from_iso: "2026-07-14T00:00:00Z",
        to_iso: "2026-07-21T00:00:00Z",
        cells: [{ weekday: 1, hour: 10, clicks: 4, registrations: null, ftds: null }],
      }).cells,
    ).toHaveLength(1);
  });

  it("rejects duplicate daypart coordinates", () => {
    const payload = {
      state: "partial",
      as_of: null,
      freshness_seconds: null,
      issues: [],
      sources: makeAnalyticsPerformanceFixture().sources,
      scope: makeAnalyticsPerformanceFixture().scope,
      timezone: "Europe/Kaliningrad",
      from_iso: "2026-07-14T00:00:00Z",
      to_iso: "2026-07-21T00:00:00Z",
      cells: [
        { weekday: 1, hour: 10, clicks: 4, registrations: null, ftds: null },
        { weekday: 1, hour: 10, clicks: 5, registrations: null, ftds: null },
      ],
    };

    expect(() => normalizeAnalyticsDaypart(payload)).toThrow(AnalyticsPayloadError);
  });

  it("rejects ready chart sections without renderable evidence", () => {
    const valid = makeAnalyticsPerformanceFixture();
    const section = {
      state: "ready",
      as_of: valid.as_of,
      freshness_seconds: valid.freshness_seconds,
      issues: [],
      sources: valid.sources,
      scope: valid.scope,
    } as const;

    expect(() =>
      normalizeAnalyticsLiveBudgetSeries({
        ...section,
        window: valid.window,
        points: [],
      }),
    ).toThrow(AnalyticsPayloadError);
    expect(() =>
      normalizeAnalyticsDaypart({
        ...section,
        timezone: "Europe/Kaliningrad",
        from_iso: valid.window.from_iso,
        to_iso: valid.window.to_iso,
        cells: [],
      }),
    ).toThrow(AnalyticsPayloadError);

    expect(() =>
      normalizeAnalyticsLiveBudgetSeries({
        ...section,
        sources: {
          ...valid.sources,
          tracker: { ...valid.sources.tracker, status: "degraded" },
        },
        window: valid.window,
        points: [
          {
            ts: valid.as_of!,
            actual: "1.00",
            base: "1.00",
            stop: "2.00",
            available_ads: 1,
            unavailable_ads: 0,
          },
        ],
      }),
    ).toThrow(AnalyticsPayloadError);
  });
});
