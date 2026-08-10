import { describe, expect, it } from "vitest";

import type { AnalyticsPerformance } from "../../api/types";
import {
  analyticsPerformanceState,
  effectiveAnalyticsState,
  inheritAnalyticsState,
} from "../state";

describe("analytics state inheritance", () => {
  it("marks cached or disconnected data stale without overriding unavailable", () => {
    expect(
      effectiveAnalyticsState("ready", {
        realtimeConnected: true,
        placeholder: true,
      }),
    ).toBe("stale");
    expect(effectiveAnalyticsState("ready", { realtimeConnected: false })).toBe(
      "stale",
    );
    expect(
      effectiveAnalyticsState("ready", {
        realtimeConnected: true,
        refreshing: true,
      }),
    ).toBe("stale");
    expect(
      effectiveAnalyticsState("unavailable", { realtimeConnected: false }),
    ).toBe("unavailable");
  });

  it("marks ready analytics partial when cabinet-day boundaries are unknown", () => {
    const data = {
      state: "ready",
      window: {
        from_iso: "2026-07-27T00:00:00Z",
        to_iso: "2026-07-27T01:00:00Z",
        is_live: true,
        timezone: "UTC",
        timezone_known: false,
        issues: ["Cabinet timezone unavailable"],
        cabinet_day_note: null,
      },
    } as AnalyticsPerformance;

    expect(analyticsPerformanceState(data, { realtimeConnected: true })).toBe(
      "partial",
    );
  });

  it("inherits the least trustworthy parent or child state", () => {
    expect(inheritAnalyticsState("ready", "stale")).toBe("stale");
    expect(inheritAnalyticsState("partial", "ready")).toBe("partial");
    expect(inheritAnalyticsState("ready", "unavailable")).toBe("unavailable");
    expect(inheritAnalyticsState("empty", "ready")).toBe("empty");
  });
});
