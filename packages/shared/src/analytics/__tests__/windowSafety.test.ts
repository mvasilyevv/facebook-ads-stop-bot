import { describe, expect, it } from "vitest";

import { analyticsWindowSafety, UNKNOWN_TIMEZONE_ISSUE } from "../windowSafety";

describe("analytics window timezone safety", () => {
  it("marks a durable explicit IANA timezone ready", () => {
    const result = analyticsWindowSafety({
      from_iso: "2026-07-19T00:00:00Z",
      to_iso: "2026-07-19T23:59:59Z",
      is_live: true,
      timezone: "Europe/Kaliningrad",
      timezone_known: true,
      timezone_state: "single",
      missing_timezone_account_ids: [],
      issues: [],
    } as never);

    expect(result).toMatchObject({
      timezone: "Europe/Kaliningrad",
      timezoneKnown: true,
      state: "ready",
    });
  });

  it("fails closed when required timezone safety fields are missing", () => {
    const result = analyticsWindowSafety({
      from_iso: "2026-07-19T00:00:00Z",
      to_iso: "2026-07-19T23:59:59Z",
      is_live: true,
    });

    expect(result).toMatchObject({
      timezone: "UTC",
      timezoneKnown: false,
      state: "partial",
    });
    expect(result.issues).toContain(UNKNOWN_TIMEZONE_ISSUE);
  });

  it("never treats an invalid zone as exact even with a true flag", () => {
    const result = analyticsWindowSafety({
      from_iso: "2026-07-19T00:00:00Z",
      to_iso: "2026-07-19T23:59:59Z",
      is_live: true,
      timezone: "Mars/Olympus",
      timezone_known: true,
      timezone_state: "single",
      missing_timezone_account_ids: [],
      issues: [],
    } as never);

    expect(result).toMatchObject({
      timezone: "UTC",
      timezoneKnown: false,
      state: "partial",
    });
  });

  it("keeps per-cabinet mixed timezone evidence exact without inventing UTC", () => {
    const result = analyticsWindowSafety({
      from_iso: "2026-07-19T00:00:00Z",
      to_iso: "2026-07-19T23:59:59Z",
      is_live: true,
      timezone: null,
      timezone_known: true,
      timezone_state: "mixed",
      missing_timezone_account_ids: [],
      issues: [],
    });

    expect(result).toMatchObject({
      timezone: null,
      timezoneKnown: true,
      timezoneState: "mixed",
      state: "ready",
    });
  });
});
