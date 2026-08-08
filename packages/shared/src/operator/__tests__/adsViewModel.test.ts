import { describe, expect, it } from "vitest";

import {
  classifyOperatorDelivery,
  confirmedOperatorCurrency,
  formatOperatorCount,
  operatorActiveActionLabel,
} from "../adsViewModel";

describe("operator ad view model", () => {
  it("does not turn an unknown delivery state into an action", () => {
    expect(classifyOperatorDelivery(null)).toBe("unknown");
    expect(classifyOperatorDelivery("learning limited")).toBe("unknown");
  });

  it("maps active and inactive Meta statuses", () => {
    expect(classifyOperatorDelivery("ACTIVE")).toBe("active");
    expect(classifyOperatorDelivery("ON")).toBe("active");
    expect(classifyOperatorDelivery("ENABLED")).toBe("active");
    expect(classifyOperatorDelivery("DELIVERING")).toBe("active");
    expect(classifyOperatorDelivery("OFF")).toBe("inactive");
    expect(classifyOperatorDelivery("PAUSED")).toBe("inactive");
    expect(classifyOperatorDelivery("INACTIVE")).toBe("inactive");
    expect(classifyOperatorDelivery("DISABLED")).toBe("inactive");
    expect(classifyOperatorDelivery("ARCHIVED")).toBe("unknown");
    expect(classifyOperatorDelivery("PAUSED_BY_USER")).toBe("unknown");
    expect(classifyOperatorDelivery("NOT_DELIVERING")).toBe("unknown");
  });

  it("keeps confirmed zero distinct from unknown", () => {
    expect(formatOperatorCount(0)).toBe("0");
    expect(formatOperatorCount(null)).toBe("—");
  });

  it("returns currency only for a confirmed single-currency scope", () => {
    expect(
      confirmedOperatorCurrency({
        currency_state: "single",
        currency: "KWD",
      }),
    ).toBe("KWD");
    expect(
      confirmedOperatorCurrency({
        currency_state: "mixed",
        currency: null,
      }),
    ).toBeNull();
    expect(
      confirmedOperatorCurrency({
        currency_state: "unknown",
        currency: null,
      }),
    ).toBeNull();
  });

  it("never presents an ambiguous action as queued", () => {
    expect(operatorActiveActionLabel("queued")).toBe("в очереди");
    expect(operatorActiveActionLabel("unknown")).toBe(
      "результат не подтверждён",
    );
    expect(operatorActiveActionLabel("unknown")).not.toBe(
      operatorActiveActionLabel("queued"),
    );
  });

  it("keeps confirmed money actions visibly blocked until data reconciliation", () => {
    expect(operatorActiveActionLabel("confirmed")).toBe(
      "Подтверждено · сверяем данные",
    );
  });
});
