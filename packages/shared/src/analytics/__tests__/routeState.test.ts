import { describe, expect, it } from "vitest";

import { parseAnalyticsRouteSearch } from "../routeState";

describe("analytics route state", () => {
  it("parses one canonical deep-link contract for web and TMA", () => {
    expect(
      parseAnalyticsRouteSearch({
        tab: "events",
        period: "custom",
        from_date: "2026-08-01",
        to_date: "2026-08-08",
        account_id: " act_1 ",
        preset: "funnel",
        sort: "ftds",
        direction: "asc",
        page: "3",
      }),
    ).toEqual(
      expect.objectContaining({
        tab: "events",
        period: "custom",
        from_date: "2026-08-01",
        to_date: "2026-08-08",
        account_id: "act_1",
        preset: "funnel",
        sort: "ftds",
        direction: "asc",
        page: 3,
      }),
    );
  });

  it("fails closed to supported defaults for malformed URL values", () => {
    const parsed = parseAnalyticsRouteSearch({
      period: "forever",
      from_date: "2026-02-30",
      to_date: "08/01/2026",
      preset: "everything",
      sort: "profit",
      page: "-1",
    });
    expect(parsed.period).toBe("today");
    expect(parsed.from_date).toBeUndefined();
    expect(parsed.to_date).toBeUndefined();
    expect(parsed.preset).toBe("economy");
    expect(parsed.sort).toBe("spend");
    expect(parsed.direction).toBe("desc");
    expect(parsed.page).toBe(1);
  });
});
