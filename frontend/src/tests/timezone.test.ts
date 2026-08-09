import { describe, expect, it } from "vitest";

import { formatDisplayDateTime, formatDisplayTime, isValidTimeZone } from "@/lib/timezone";

describe("display timezone", () => {
  it("validates an explicit IANA timezone", () => {
    expect(isValidTimeZone("Europe/Kaliningrad")).toBe(true);
    expect(isValidTimeZone("Kaliningrad/not-a-zone")).toBe(false);
  });

  it("formats one instant consistently in the selected timezone", () => {
    const value = "2026-07-17T10:15:00Z";
    expect(formatDisplayTime(value, {}, "Europe/Kaliningrad")).toBe("12:15");
    expect(formatDisplayDateTime(value, "Europe/Kaliningrad")).toContain("12:15");
  });

  it("never falls back to the device timezone when presentation is unsupported", () => {
    expect(formatDisplayTime("2026-07-17T10:15:00Z", {}, "Mars/Olympus")).toBe("—");
  });
});
