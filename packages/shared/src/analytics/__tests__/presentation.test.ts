import { describe, expect, it } from "vitest";

import type { AnalyticsPerformanceRow } from "../../api/types";
import {
  analyticsColumnsForPreset,
  analyticsMetricsForRow,
} from "../presentation";

const row = {
  spend: "18.40",
  revenue: "25.00",
  impressions: 420,
  clicks: 42,
  registrations: 5,
  ftds: 1,
  confirmed_deposits: 1,
  cpc: "0.44",
  ctr_pct: "10.00",
  click_registration_cr_pct: "11.90",
  registration_ftd_cr_pct: "20.00",
  cost_per_registration: "3.68",
  cost_per_ftd: "18.40",
  roi_pct: "35.87",
  live_budget: {
    base_budget: "15.00",
    base_delta: "3.40",
  },
} as AnalyticsPerformanceRow;

describe("analytics presentation model", () => {
  it.each(["economy", "funnel", "delivery"] as const)(
    "keeps %s at six metrics plus one object column",
    (preset) => {
      expect(analyticsColumnsForPreset(preset)).toHaveLength(6);
      expect(analyticsMetricsForRow(row, preset, "USD")).toHaveLength(6);
    },
  );

  it("hides money when USD evidence is absent while retaining counts", () => {
    const delivery = analyticsMetricsForRow(row, "delivery", null);
    expect(delivery.find((metric) => metric.key === "spend")?.value).toBe("—");
    expect(delivery.find((metric) => metric.key === "clicks")?.value).toBe(
      "42",
    );
  });
});
