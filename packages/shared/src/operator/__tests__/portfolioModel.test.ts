import { describe, expect, it } from "vitest";

import { makeOperatorSnapshot } from "../testFixture";
import {
  buildOperatorPortfolioScale,
  findOperatorCabinetLedgerRow,
  operatorPortfolioScalePosition,
} from "../portfolioModel";

function fixtureGroup() {
  return makeOperatorSnapshot().portfolio.data!.currency_groups[0]!;
}

describe("operator portfolio scale model", () => {
  it("uses one rounded scale for every confirmed USD cabinet", () => {
    const scale = buildOperatorPortfolioScale(fixtureGroup(), true);

    expect(scale).toEqual({
      usdConfirmed: true,
      maximum: 20,
      ticks: [0, 5, 10, 15, 20],
    });
    expect(operatorPortfolioScalePosition("10.00", scale)).toBe(50);
    expect(operatorPortfolioScalePosition("0", scale)).toBe(0);
  });

  it("fails closed when scope or group currency is not confirmed USD", () => {
    const group = fixtureGroup();
    const unconfirmed = buildOperatorPortfolioScale(group, false);
    group.currency = "EUR";
    const nonUsd = buildOperatorPortfolioScale(group, true);

    expect(unconfirmed.usdConfirmed).toBe(false);
    expect(nonUsd.usdConfirmed).toBe(false);
    expect(operatorPortfolioScalePosition("18.40", unconfirmed)).toBeNull();
    expect(operatorPortfolioScalePosition("18.40", nonUsd)).toBeNull();
  });

  it("keeps missing and invalid decimals unknown and clamps overflow", () => {
    const scale = buildOperatorPortfolioScale(fixtureGroup(), true);

    expect(operatorPortfolioScalePosition(null, scale)).toBeNull();
    expect(operatorPortfolioScalePosition("not-a-number", scale)).toBeNull();
    expect(operatorPortfolioScalePosition("999", scale)).toBe(100);
  });
});

describe("findOperatorCabinetLedgerRow", () => {
  it("finds the requested cabinet across currency groups", () => {
    const portfolio = makeOperatorSnapshot().portfolio.data!;

    const row = findOperatorCabinetLedgerRow(portfolio, "123");

    expect(row?.id).toBe("123");
    expect(row?.risk_label).toBe("Stop превышен");
    expect(row?.risk_reason).toBe("Факт $18.40 ≥ stop $18.00");
  });

  it("returns null for an unknown cabinet or a missing portfolio", () => {
    const portfolio = makeOperatorSnapshot().portfolio.data!;

    expect(findOperatorCabinetLedgerRow(portfolio, "does-not-exist")).toBeNull();
    expect(findOperatorCabinetLedgerRow(null, "123")).toBeNull();
  });
});
