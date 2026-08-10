import { describe, expect, it } from "vitest";

import { serverTimeMarker } from "@/components/analytics/BudgetLineChart";
import { currentMarkerLabelPosition, serverSeriesMarker } from "@fb/shared/operator/chartModel";

describe("server-authoritative chart time markers", () => {
  it("uses the snapshot time even when the current actual value is unknown", () => {
    expect(
      serverSeriesMarker(
        ["2026-07-27T08:00:00Z", "2026-07-27T09:00:00Z", "2026-07-27T10:00:00Z"],
        "2026-07-27T10:15:00Z",
      ),
    ).toBe("2026-07-27T10:00:00Z");
  });

  it("clamps analytics as_of to the server series instead of using client time", () => {
    const points = [
      Date.parse("2026-07-27T08:00:00Z"),
      Date.parse("2026-07-27T09:00:00Z"),
      Date.parse("2026-07-27T10:00:00Z"),
    ];

    expect(serverTimeMarker(points, "2026-07-27T09:30:00Z")).toBe(
      Date.parse("2026-07-27T09:30:00Z"),
    );
    expect(serverTimeMarker(points, "2026-07-27T12:00:00Z")).toBe(points[2]);
    expect(serverTimeMarker(points, null)).toBeNull();
  });

  it("keeps the current-time label inside either chart edge", () => {
    const points = ["08:00", "09:00", "10:00"];

    expect(currentMarkerLabelPosition(points, "08:00")).toBe("insideTopRight");
    expect(currentMarkerLabelPosition(points, "10:00")).toBe("insideTopLeft");
    expect(currentMarkerLabelPosition(points, "missing")).toBe("insideTopLeft");
  });

  it("keeps an interpolated server marker inside the nearest chart edge", () => {
    const points = [
      "2026-07-27T08:00:00Z",
      "2026-07-27T09:00:00Z",
      "2026-07-27T10:00:00Z",
    ];

    expect(currentMarkerLabelPosition(points, "2026-07-27T08:30:00Z")).toBe(
      "insideTopRight",
    );
    expect(currentMarkerLabelPosition(points, "2026-07-27T09:30:00Z")).toBe(
      "insideTopLeft",
    );
  });
});
