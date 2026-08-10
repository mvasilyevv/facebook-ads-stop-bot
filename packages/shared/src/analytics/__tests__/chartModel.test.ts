import { describe, expect, it } from "vitest";

import {
  buildAnalyticsFunnelModel,
  buildSpendChartModel,
  selectedDayHours,
} from "../chartModel";

const points = [
  "2026-07-27T08:00:00Z",
  "2026-07-27T09:00:00Z",
  "2026-07-27T10:00:00Z",
];

describe("analytics chart model", () => {
  it("keeps missing spend points as gaps and uses server time", () => {
    const model = buildSpendChartModel(
      [
        { at: points[0]!, actual: "1.00", base: "2.00", stop: "3.00" },
        { at: points[1]!, actual: null, base: "2.00", stop: "3.00" },
        { at: points[2]!, actual: "4.00", base: null, stop: "5.00" },
      ],
      "2026-07-27T09:30:00Z",
    );
    expect(model.points[1]?.actual).toBeNull();
    expect(model.points[2]?.base).toBeNull();
    expect(model.currentMarker).toBe(points[1]);
    expect(model.maximum).toBe(5);
  });

  it("builds one USD-only funnel model for both renderers", () => {
    const totals = {
      spend: "18.40",
      clicks: 42,
      registrations: 5,
      ftds: 1,
      confirmed_deposits: 1,
    };
    const usd = buildAnalyticsFunnelModel(totals, "USD");
    const rejectedCurrency = buildAnalyticsFunnelModel(totals, "KWD");
    expect(usd.map((stage) => stage.key)).toEqual([
      "clicks",
      "registrations",
      "ftd",
      "confirmed_deposits",
    ]);
    expect(usd[1]?.conversion).toBeCloseTo(11.9047, 3);
    expect(usd[1]?.cost).toMatch(/^\$/);
    expect(rejectedCurrency.every((stage) => stage.cost === "—")).toBe(true);
  });

  it("materializes a sparse selected day into 24 explicit hours", () => {
    const hours = selectedDayHours(
      [
        {
          weekday: 1,
          hour: 0,
          clicks: 0,
          registrations: 0,
          ftds: 0,
        },
      ],
      1,
    );
    expect(hours).toHaveLength(24);
    expect(hours[0]).toMatchObject({ present: true, clicks: 0 });
    expect(hours[1]).toMatchObject({ present: false, clicks: null });
  });
});
