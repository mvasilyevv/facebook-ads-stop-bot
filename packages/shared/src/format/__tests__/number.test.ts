import { describe, it, expect } from "vitest";
import {
  compareDecimalStrings,
  formatDecimalPercent,
  formatDecimalValue,
  formatSpend,
  formatSpendPerUnit,
  formatCompact,
  formatInt,
  formatPercent,
  formatPercentValue,
  isDecimalString,
  isSupportedCurrencyCode,
} from "../number";

describe("formatSpend", () => {
  it("formats the product currency with the dollar symbol", () => {
    expect(formatSpend(1234.56, "USD")).toBe("$1,234.56");
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
      "$9,007,199,254,740,993.01",
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
    expect(formatSpend(-50, "USD")).toBe("-$50.00");
  });
});

describe("formatSpendPerUnit", () => {
  it("divides and rounds in minor units without Number coercion", () => {
    expect(formatSpendPerUnit("9007199254740993.01", 3, "USD")).toBe(
      "$3,002,399,751,580,331.00",
    );
    expect(formatSpendPerUnit("10.00", 3, "USD")).toBe("$3.33");
    expect(formatSpendPerUnit("10.00", 6, "USD")).toBe("$1.67");
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

describe("isDecimalString", () => {
  it("принимает только точные десятичные строки контракта", () => {
    expect(isDecimalString("85.40")).toBe(true);
    expect(isDecimalString("-3")).toBe(true);
    expect(isDecimalString("1e3")).toBe(false);
    expect(isDecimalString("")).toBe(false);
    expect(isDecimalString(85.4)).toBe(false);
    expect(isDecimalString(null)).toBe(false);
  });
});

describe("compareDecimalStrings", () => {
  it("сравнивает разные масштабы без потери хвоста", () => {
    expect(compareDecimalStrings("99.999999999999996", "100")).toBe(-1);
    expect(compareDecimalStrings("100", "99.999999999999996")).toBe(1);
    expect(compareDecimalStrings("85.40", "85.4")).toBe(0);
  });

  it("сохраняет порядок за пределами Number.MAX_SAFE_INTEGER", () => {
    expect(
      compareDecimalStrings("9007199254740993.01", "9007199254740993.02"),
    ).toBe(-1);
  });

  it("учитывает знак", () => {
    expect(compareDecimalStrings("-1.5", "0")).toBe(-1);
    expect(compareDecimalStrings("-1.5", "-2.5")).toBe(1);
  });
});

describe("formatDecimalPercent", () => {
  it("усекает долю вместо округления вверх", () => {
    expect(formatDecimalPercent("85.40")).toBe("85.4%");
    expect(formatDecimalPercent("99.99")).toBe("99.9%");
    expect(formatDecimalPercent("100.00")).toBe("100%");
  });

  it("оставляет прочерк для неизвестного значения", () => {
    expect(formatDecimalPercent(null)).toBe("—");
    expect(formatDecimalPercent("")).toBe("—");
    expect(formatDecimalPercent("много")).toBe("—");
  });

  it("не подменяет подтверждённый ноль прочерком", () => {
    expect(formatDecimalPercent("0.00")).toBe("0%");
  });
});

describe("formatDecimalValue", () => {
  it("усекает хвост и убирает незначащие нули", () => {
    expect(formatDecimalValue("1.234567")).toBe("1.23");
    expect(formatDecimalValue("3.000")).toBe("3");
    expect(formatDecimalValue("2.5", 0)).toBe("2");
  });

  it("оставляет прочерк для неизвестного значения", () => {
    expect(formatDecimalValue(null)).toBe("—");
    expect(formatDecimalValue("0")).toBe("0");
  });
});
