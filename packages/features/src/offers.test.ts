import { describe, expect, it } from "vitest";

import {
  buildOfferRulesBody,
  isOfferCpaValid,
  parseOfferAccountIds,
  parseOfferCountries,
  rulesValuesFromOut,
  rulesValuesToPayload,
} from "./offers";

describe("offer feature model", () => {
  it("normalizes account and country tokens identically for every shell", () => {
    expect(parseOfferAccountIds("act_123, 456;123")).toEqual(["123", "456"]);
    expect(parseOfferAccountIds("act_bad")).toBeNull();
    expect(parseOfferCountries("gh, KE gh")).toEqual(["GH", "KE"]);
    expect(parseOfferCountries("GHA")).toBeNull();
  });

  it("preserves unknown CPA and defaults invalid percentages safely", () => {
    expect(rulesValuesFromOut(undefined)).toEqual({
      cpa: "",
      currency: "",
      stop_percent_of_rule: 80,
      warning_percent_of_stop: 80,
    });
    expect(
      rulesValuesFromOut({
        cpa_threshold: null,
        currency: null,
        frequency_threshold: null,
        stop_percent_of_rule: "NaN",
        warning_percent_of_stop: "101",
      }),
    ).toMatchObject({
      cpa: "",
      currency: "",
      stop_percent_of_rule: 80,
      warning_percent_of_stop: 80,
    });
  });

  it("builds one validated API payload for web and TMA", () => {
    const draft = rulesValuesToPayload(
      {
        cpa: " 3.50 ",
        currency: "usd",
        stop_percent_of_rule: 70,
        warning_percent_of_stop: 60,
      },
      "",
    );
    expect(buildOfferRulesBody(draft)).toEqual({
      cpa_threshold: "3.50",
      currency: "USD",
      frequency_threshold: null,
      stop_percent_of_rule: "70",
      warning_percent_of_stop: "60",
    });
  });

  it("rejects percentages outside the contract", () => {
    expect(() =>
      rulesValuesToPayload({
        cpa: "3",
        currency: "USD",
        stop_percent_of_rule: 0,
        warning_percent_of_stop: 80,
      }),
    ).toThrow(/1 до 100/);
  });

  it("rejects a CPA without an explicit ISO currency", () => {
    expect(() =>
      rulesValuesToPayload({
        cpa: "3",
        currency: "",
        stop_percent_of_rule: 80,
        warning_percent_of_stop: 80,
      }),
    ).toThrow(/ISO-код валюты/);
  });

  it("keeps a large KWD decimal string exact without Number coercion", () => {
    const cpa = "9007199254740.123";
    expect(isOfferCpaValid(cpa)).toBe(true);
    expect(
      rulesValuesToPayload({
        cpa,
        currency: "KWD",
        stop_percent_of_rule: 80,
        warning_percent_of_stop: 80,
      }).cpa_threshold,
    ).toBe(cpa);
    expect(isOfferCpaValid("1e3")).toBe(false);
    expect(isOfferCpaValid("0.000")).toBe(false);
  });
});
