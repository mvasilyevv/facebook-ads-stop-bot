import { beforeEach, describe, expect, it } from "vitest";

import {
  browserTimeZone,
  formatDisplayDateTime,
  formatDisplayTime,
  isValidTimeZone,
  resolveDisplayTimeZone,
} from "@/lib/timezone";
import { useUiStore } from "@/stores/ui";

describe("display timezone", () => {
  beforeEach(() => {
    useUiStore.setState({ displayTimeZone: "auto" });
  });

  it("uses the browser timezone in automatic mode", () => {
    expect(resolveDisplayTimeZone()).toBe(browserTimeZone());
  });

  it("uses and validates a manual IANA timezone", () => {
    useUiStore.getState().setDisplayTimeZone("Europe/Kaliningrad");
    expect(resolveDisplayTimeZone()).toBe("Europe/Kaliningrad");
    expect(isValidTimeZone("Europe/Kaliningrad")).toBe(true);
    expect(isValidTimeZone("Kaliningrad/not-a-zone")).toBe(false);
  });

  it("formats one instant consistently in the selected timezone", () => {
    const value = "2026-07-17T10:15:00Z";
    expect(formatDisplayTime(value, {}, "Europe/Kaliningrad")).toBe("12:15");
    expect(formatDisplayDateTime(value, "Europe/Kaliningrad")).toContain("12:15");
  });
});
