import { describe, it, expect } from "vitest";
import {
  formatSpend,
  formatCompact,
  formatInt,
  formatPercent,
  formatPercentValue,
} from "../number";

describe("formatSpend", () => {
  // Обычное положительное число
  it("форматирует положительное число", () => {
    expect(formatSpend(1234.56)).toBe("$1,234.56");
  });

  // Строковый вход (бэк отдаёт Decimal как строку)
  it("принимает строку", () => {
    expect(formatSpend("99.5")).toBe("$99.50");
  });

  // Ноль — валидный кейс
  it("форматирует ноль", () => {
    expect(formatSpend(0)).toBe("$0.00");
  });

  // null → "—"
  it("null → —", () => {
    expect(formatSpend(null)).toBe("—");
  });

  // undefined → "—"
  it("undefined → —", () => {
    expect(formatSpend(undefined)).toBe("—");
  });

  // Пустая строка → "—"
  it("пустая строка → —", () => {
    expect(formatSpend("")).toBe("—");
  });

  // Не-число строка → "—"
  it("невалидная строка → —", () => {
    expect(formatSpend("abc")).toBe("—");
  });

  // Отрицательное (бывает при корректировках)
  it("форматирует отрицательное число", () => {
    expect(formatSpend(-50)).toBe("-$50.00");
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
