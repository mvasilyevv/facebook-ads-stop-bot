import { describe, expect, it } from "vitest";

import { makeOperatorSnapshot } from "../testFixture";
import {
  isClientRankedAdsSort,
  operatorAdsQuerySort,
  operatorCabinetOptions,
  parseOperatorActionsRouteSearch,
  parseOperatorAdsRouteSearch,
} from "../routeFilters";

describe("operator route filters", () => {
  it("parses bounded ad URL state and rejects unsupported values", () => {
    expect(
      parseOperatorAdsRouteSearch({
        q: "  campaign  ",
        account_id: "  123  ",
        severity: "critical",
        sort: "spend",
        direction: "asc",
      }),
    ).toEqual({
      q: "campaign",
      account_id: "123",
      severity: "critical",
      sort: "spend",
      direction: "asc",
    });
    expect(
      parseOperatorAdsRouteSearch({
        severity: "danger",
        sort: "profit",
        direction: "sideways",
      }),
    ).toEqual({
      q: undefined,
      account_id: undefined,
      severity: undefined,
      sort: undefined,
      direction: undefined,
    });
  });

  it("accepts cancelled action state and rejects unknown query states", () => {
    expect(
      parseOperatorActionsRouteSearch({
        account_id: "456",
        state: "cancelled",
      }),
    ).toEqual({ account_id: "456", state: "cancelled" });
    expect(parseOperatorActionsRouteSearch({ state: "done" })).toEqual({
      account_id: undefined,
      state: undefined,
    });
  });

  it("sends stop proximity sorting to the server, keeping the URL readable", () => {
    expect(parseOperatorAdsRouteSearch({ sort: "stop_proximity" }).sort).toBe(
      "stop_proximity",
    );
    // Порядок обязан считать сервер: клиент видит только текущую страницу, и
    // самое опасное объявление может лежать на следующей.
    expect(operatorAdsQuerySort("stop_proximity")).toBe("percent_to_stop");
    expect(operatorAdsQuerySort("spend")).toBe("spend");
    expect(operatorAdsQuerySort(undefined)).toBe("percent_to_stop");
    expect(isClientRankedAdsSort("stop_proximity")).toBe(false);
    expect(isClientRankedAdsSort("spend")).toBe(false);
  });

  it("derives cabinet IDs and labels from the typed global snapshot without currency inference", () => {
    const snapshot = makeOperatorSnapshot();
    snapshot.portfolio.data!.currency_groups.push({
      ...snapshot.portfolio.data!.currency_groups[0]!,
      id: "UNKNOWN",
      currency: null,
      cabinets: [
        {
          ...snapshot.portfolio.data!.currency_groups[0]!.cabinets[0]!,
          id: "999",
          name: "NO_CURRENCY_EVIDENCE",
          currency: null,
        },
      ],
    });

    expect(operatorCabinetOptions(snapshot)).toEqual([
      { value: "123", label: "GH_CR2" },
      { value: "456", label: "PL_VIP" },
      { value: "789", label: "ES_CORE" },
      { value: "999", label: "NO_CURRENCY_EVIDENCE" },
    ]);
  });
});
