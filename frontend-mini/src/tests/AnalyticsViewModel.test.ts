import { describe, expect, it } from "vitest";

import {
  performanceWindow,
  selectedDayHours,
} from "@/features/analytics/viewModel";
import { effectiveAnalyticsState } from "@fb/shared/analytics/state";

describe("analytics mobile view-model", () => {
  it("sends semantic periods and leaves exact boundaries to the server", () => {
    expect(performanceWindow("today")).toEqual({ period: "today" });
    expect(performanceWindow("7d")).toEqual({ period: "7d" });
    expect(performanceWindow("30d")).toEqual({ period: "30d" });
    expect(performanceWindow("custom", "2026-08-01", "2026-08-08")).toEqual({
      period: "custom",
      from_date: "2026-08-01",
      to_date: "2026-08-08",
    });
  });

  it("materializes sparse daypart data as 24 explicit hours", () => {
    const hours = selectedDayHours(
      [
        {
          weekday: 3,
          hour: 2,
          clicks: 0,
          registrations: 0,
          ftds: 0,
        },
        {
          weekday: 3,
          hour: 5,
          clicks: null,
          registrations: null,
          ftds: null,
        },
      ],
      3,
    );
    expect(hours).toHaveLength(24);
    expect(hours[2]).toMatchObject({ clicks: 0, present: true });
    expect(hours[4]).toMatchObject({ clicks: null, present: false });
    expect(hours[5]).toMatchObject({ clicks: null, present: true });
  });

  it("downgrades usable evidence while reconnecting or replacing filters", () => {
    expect(effectiveAnalyticsState("ready", { realtimeConnected: false })).toBe(
      "stale",
    );
    expect(
      effectiveAnalyticsState("partial", {
        realtimeConnected: true,
        placeholder: true,
      }),
    ).toBe("stale");
    expect(
      effectiveAnalyticsState("ready", {
        realtimeConnected: true,
        windowKnown: false,
      }),
    ).toBe("partial");
    expect(
      effectiveAnalyticsState("unavailable", {
        realtimeConnected: false,
        placeholder: true,
      }),
    ).toBe("unavailable");
  });
});
