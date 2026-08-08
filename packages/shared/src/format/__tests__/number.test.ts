import { describe, it, expect } from "vitest";
import {
  formatSpend,
  formatSpendPerUnit,
  formatCompact,
  formatInt,
  formatPercent,
  formatPercentValue,
  isSupportedCurrencyCode,
} from "../number";

describe("formatSpend", () => {
  it("formats a major-unit value with an explicit currency code", () => {
    expect(formatSpend(1234.56, "USD")).toBe("USD\u00a01,234.56");
  });

  it("uses the ISO exponent instead of assuming two decimals", () => {
    expect(formatSpend("1234", "JPY")).toBe("JPY\u00a01,234");
    expect(formatSpend("1.234", "KWD")).toBe("KWD\u00a01.234");
  });

  it("uses the repo-owned currency contract instead of the runtime ICU list", () => {
    expect(isSupportedCurrencyCode("VED")).toBe(true);
    expect(formatSpend("1.23", "VED")).toBe("VED\u00a01.23");
  });

  it("preserves exact decimal strings beyond Number.MAX_SAFE_INTEGER", () => {
    expect(formatSpend("9007199254740993.01", "USD")).toBe(
      "USD\u00a09,007,199,254,740,993.01",
    );
  });

  it("formats a confirmed zero", () => {
    expect(formatSpend(0, "EUR")).toBe("EUR\u00a00.00");
  });

  it("does not invent a unit for unknown evidence", () => {
    expect(formatSpend("12.50", null)).toBe("—");
    expect(formatSpend("12.50", "")).toBe("—");
    expect(formatSpend("12.50", "ZZZ")).toBe("—");
  });

  it("returns unknown for absent and invalid amounts", () => {
    expect(formatSpend(null, "USD")).toBe("—");
    expect(formatSpend(undefined, "USD")).toBe("—");
    expect(formatSpend("", "USD")).toBe("—");
    expect(formatSpend("abc", "USD")).toBe("—");
    expect(formatSpend("12.50oops", "USD")).toBe("—");
    expect(formatSpend(Number.POSITIVE_INFINITY, "USD")).toBe("—");
  });

  it("formats negative adjustments without losing the currency", () => {
    expect(formatSpend(-50, "USD")).toBe("-USD\u00a050.00");
  });
});

describe("formatSpendPerUnit", () => {
  it("divides and rounds in minor units without Number coercion", () => {
    expect(formatSpendPerUnit("9007199254740993.01", 3, "USD")).toBe(
      "USD\u00a03,002,399,751,580,331.00",
    );
    expect(formatSpendPerUnit("10.00", 3, "USD")).toBe("USD\u00a03.33");
    expect(formatSpendPerUnit("10.00", 6, "USD")).toBe("USD\u00a01.67");
  });

  it("uses the confirmed currency exponent", () => {
    expect(formatSpendPerUnit("10", 4, "JPY")).toBe("JPY\u00a03");
    expect(formatSpendPerUnit("1", 6, "KWD")).toBe("KWD\u00a00.167");
  });

  it("fails closed for absent, zero or unsupported evidence", () => {
    expect(formatSpendPerUnit(null, 3, "USD")).toBe("—");
    expect(formatSpendPerUnit("10", 0, "USD")).toBe("—");
    expect(formatSpendPerUnit("10", 3, null)).toBe("—");
    expect(formatSpendPerUnit("10", 3, "XAU")).toBe("—");
  });
});

describe("formatCompact", () => {
  // Тысячи → K
  it("форматирует тысячи", () => {
    expect(formatCompact(12400)).toBe("12.4K");
  });

  // Миллионы → M
  it("форматирует миллионы", () => {
    expect(formatCompact(1200000)).toBe("1.2M");
  });

  // null → "—"
  it("null → —", () => {
    expect(formatCompact(null)).toBe("—");
  });

  // Ноль
  it("ноль", () => {
    expect(formatCompact(0)).toBe("0");
  });
});

describe("formatInt", () => {
  // Целое с разделителями
  it("форматирует целое", () => {
    expect(formatInt(1234567)).toBe("1,234,567");
  });

  // null → "—"
  it("null → —", () => {
    expect(formatInt(null)).toBe("—");
  });

  // Ноль
  it("ноль", () => {
    expect(formatInt(0)).toBe("0");
  });

  // Отрицательное
  it("отрицательное", () => {
    expect(formatInt(-42)).toBe("-42");
  });
});

describe("formatPercent", () => {
  // Дробь 0..1 → процент
  it("дробь 0.124 → 12.4%", () => {
    expect(formatPercent(0.124)).toBe("12.4%");
  });

  // Ноль → 0.0%
  it("ноль → 0.0%", () => {
    expect(formatPercent(0)).toBe("0.0%");
  });

  // null → "—"
  it("null → —", () => {
    expect(formatPercent(null)).toBe("—");
  });

  // 100% = 1.0
  it("100% из дроби 1", () => {
    expect(formatPercent(1)).toBe("100.0%");
  });
});

describe("formatPercentValue", () => {
  // Готовый процент: 12.4 → "12.4%"
  it("12.4 → 12.4%", () => {
    expect(formatPercentValue(12.4)).toBe("12.4%");
  });

  // Строка
  it("строка '5' → 5.0%", () => {
    expect(formatPercentValue("5")).toBe("5.0%");
  });

  // null → "—"
  it("null → —", () => {
    expect(formatPercentValue(null)).toBe("—");
  });

  // Пустая строка → "—"
  it("пустая строка → —", () => {
    expect(formatPercentValue("")).toBe("—");
  });
});
