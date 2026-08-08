import { describe, expect, it } from "vitest";

import {
  isIncreasingTimestampRange,
  isRfc3339Timestamp,
} from "../rfc3339";

describe("strict RFC3339 runtime boundary", () => {
  it.each([
    "2026-07-29T10:00:00Z",
    "2026-07-29T10:00:00.123456+02:00",
    "2024-02-29T00:00:00-05:30",
  ])("accepts backend timestamp %s", (value) => {
    expect(isRfc3339Timestamp(value)).toBe(true);
  });

  it.each([
    "0",
    "2026-07-29",
    "2026-07-29T10:00:00",
    "2026-02-29T00:00:00Z",
    "2026-02-30T00:00:00Z",
    "0000-01-01T00:00:00Z",
    "2026-07-29T10:00:00+14:01",
  ])("rejects malformed or ambiguous timestamp %s", (value) => {
    expect(isRfc3339Timestamp(value)).toBe(false);
  });

  it("orders instants after applying their explicit offsets", () => {
    expect(
      isIncreasingTimestampRange(
        "2026-07-29T10:00:00+02:00",
        "2026-07-29T08:00:01Z",
      ),
    ).toBe(true);
    expect(
      isIncreasingTimestampRange(
        "2026-07-29T10:00:00+02:00",
        "2026-07-29T08:00:00Z",
      ),
    ).toBe(false);
  });
});
