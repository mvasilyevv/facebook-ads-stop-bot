import { describe, expect, it } from "vitest";

import {
  buildOfferRulesBody,
  DEFAULT_OFFER_RULES_VALUES,
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
    expect(rulesValuesFromOut(undefined)).toEqual(DEFAULT_OFFER_RULES_VALUES);
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
      currency: "USD",
      stop_percent_of_rule: 80,
      warning_percent_of_stop: 80,
    });
  });

  it("builds one validated API payload for web and TMA", () => {
    const draft = rulesValuesToPayload(
      {
        ...DEFAULT_OFFER_RULES_VALUES,
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
      cpc_percent_of_cpa: null,
      cpl_percent_of_cpa: null,
      cpr_percent_of_cpa: null,
      regs_no_dep_stop_count: null,
      spend_no_dep_from_percent: null,
      spend_no_dep_to_percent: null,
      spend_with_dep_from_percent: null,
      spend_with_dep_to_percent: null,
      min_ratio_denominator: null,
    });
  });

  it("rejects percentages outside the contract", () => {
    expect(() =>
      rulesValuesToPayload({
        ...DEFAULT_OFFER_RULES_VALUES,
        cpa: "3",
        currency: "USD",
        stop_percent_of_rule: 0,
        warning_percent_of_stop: 80,
      }),
    ).toThrow(/1 до 100/);
  });

  it("rejects a CPA outside the product dollar context", () => {
    expect(() =>
      rulesValuesToPayload({
        ...DEFAULT_OFFER_RULES_VALUES,
        cpa: "3",
        currency: "",
        stop_percent_of_rule: 80,
        warning_percent_of_stop: 80,
      }),
    ).toThrow(/только в USD/);
  });

  it("keeps a large USD decimal string exact without Number coercion", () => {
    const cpa = "9007199254740.123";
    expect(isOfferCpaValid(cpa)).toBe(true);
    expect(
      rulesValuesToPayload({
        ...DEFAULT_OFFER_RULES_VALUES,
        cpa,
        currency: "USD",
        stop_percent_of_rule: 80,
        warning_percent_of_stop: 80,
      }).cpa_threshold,
    ).toBe(cpa);
    expect(() =>
      rulesValuesToPayload({
        ...DEFAULT_OFFER_RULES_VALUES,
        cpa: "3.50",
        currency: "EUR",
        stop_percent_of_rule: 80,
        warning_percent_of_stop: 80,
      }),
    ).toThrow(/только в USD/);
    expect(isOfferCpaValid("1e3")).toBe(false);
    expect(isOfferCpaValid("0.000")).toBe(false);
  });
});

describe("rulesValuesFromOut — точность денег", () => {
  it("показывает CPA в точности валюты, а не в точности колонки БД", () => {
    // cpa_threshold в БД — numeric(20,6), поэтому Postgres отдаёт «3.000000».
    // Оператор видит шесть знаков там, где у доллара их два, и правит поле
    // мимо реальной точности.
    expect(
      rulesValuesFromOut({
        cpa_threshold: "3.000000",
        currency: "USD",
        stop_percent_of_rule: 100,
        warning_percent_of_stop: 80,
      } as never).cpa,
    ).toBe("3.00");
  });

  it("не округляет значащие знаки", () => {
    expect(
      rulesValuesFromOut({
        cpa_threshold: "3.456700",
        currency: "USD",
        stop_percent_of_rule: 100,
        warning_percent_of_stop: 80,
      } as never).cpa,
    ).toBe("3.46");
  });

  it("пустой CPA остаётся пустым, а не превращается в 0.00", () => {
    // Ноль — подтверждённая ставка «бесплатно», пустая строка — «не задана».
    expect(
      rulesValuesFromOut({
        cpa_threshold: null,
        currency: "USD",
        stop_percent_of_rule: 100,
        warning_percent_of_stop: 80,
      } as never).cpa,
    ).toBe("");
  });
});
