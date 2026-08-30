import { describe, expect, it } from "vitest";

import { nextCampaignRunsOffset, parseTotalCountHeader } from "../campaignRunsPage";

describe("campaign runs history pagination", () => {
  it("parses X-Total-Count without turning a confirmed zero into unknown", () => {
    expect(parseTotalCountHeader("0")).toBe(0);
    expect(parseTotalCountHeader("42")).toBe(42);
    expect(parseTotalCountHeader(null)).toBeNull();
    expect(parseTotalCountHeader("not-a-number")).toBeNull();
    expect(parseTotalCountHeader("-1")).toBeNull();
  });

  it("offers the next offset while accumulated rows stay below a known total", () => {
    expect(
      nextCampaignRunsOffset({ runs: new Array(50).fill(0), total: 120, offset: 0, limit: 50 }),
    ).toBe(50);
    expect(
      nextCampaignRunsOffset({ runs: new Array(20).fill(0), total: 120, offset: 100, limit: 50 }),
    ).toBeNull();
  });

  it("falls back to page fullness when total is unknown", () => {
    expect(
      nextCampaignRunsOffset({ runs: new Array(50).fill(0), total: null, offset: 0, limit: 50 }),
    ).toBe(50);
    expect(
      nextCampaignRunsOffset({ runs: new Array(12).fill(0), total: null, offset: 50, limit: 50 }),
    ).toBeNull();
  });

  it("never claims a next page once the last page reports zero rows", () => {
    expect(
      nextCampaignRunsOffset({ runs: [], total: null, offset: 50, limit: 50 }),
    ).toBeNull();
  });
});
