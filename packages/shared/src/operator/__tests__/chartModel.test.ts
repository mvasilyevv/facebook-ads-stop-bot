import { describe, expect, it } from "vitest";

import { currentMarkerLabelPosition, serverSeriesMarker } from "../chartModel";

const points = [
  "2026-07-27T08:00:00Z",
  "2026-07-27T09:00:00Z",
  "2026-07-27T10:00:00Z",
];

describe("operator chart model", () => {
  it("uses the latest confirmed point at or before server time", () => {
    expect(serverSeriesMarker(points, "2026-07-27T09:30:00Z")).toBe(points[1]);
    expect(serverSeriesMarker(points, "2026-07-27T12:00:00Z")).toBe(points[2]);
    expect(serverSeriesMarker(points, "2026-07-27T07:00:00Z")).toBeNull();
  });

  it("fails closed for absent or invalid server time", () => {
    expect(serverSeriesMarker(points, null)).toBeNull();
    expect(serverSeriesMarker(points, "not-a-date")).toBeNull();
    expect(serverSeriesMarker([], "2026-07-27T09:30:00Z")).toBeNull();
  });

  it("selects an inward label direction at both plot edges", () => {
    expect(currentMarkerLabelPosition(points, points[0]!)).toBe(
      "insideTopRight",
    );
    expect(currentMarkerLabelPosition(points, points[2]!)).toBe(
      "insideTopLeft",
    );
    expect(currentMarkerLabelPosition(points, "missing")).toBe("insideTopLeft");
  });
});
