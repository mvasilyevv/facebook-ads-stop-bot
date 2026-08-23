import { describe, expect, it } from "vitest";

import {
  formatRussianCount,
  russianCountForm,
  russianCountIsOne,
} from "../russianCount";

describe("russianCountForm — форма без числа", () => {
  it("1 → одна форма (одна строка)", () => {
    expect(russianCountForm(1, "строка", "строки", "строк")).toBe("строка");
  });

  it("2 → вторая форма (строки)", () => {
    expect(russianCountForm(2, "строка", "строки", "строк")).toBe("строки");
  });

  it("5 → третья форма (строк)", () => {
    expect(russianCountForm(5, "строка", "строки", "строк")).toBe("строк");
  });

  it("11 → третья форма, несмотря на остаток 1 по mod 10", () => {
    expect(russianCountForm(11, "запуск", "запуска", "запусков")).toBe("запусков");
  });

  it("14 → третья форма, несмотря на остаток 4 по mod 10", () => {
    expect(russianCountForm(14, "запуск", "запуска", "запусков")).toBe("запусков");
  });

  it("21 → первая форма (снова одна)", () => {
    expect(russianCountForm(21, "запуск", "запуска", "запусков")).toBe("запуск");
  });
});

describe("formatRussianCount — число + форма", () => {
  it("1 → «1 строка»", () => {
    expect(formatRussianCount(1, "строка", "строки", "строк")).toBe("1 строка");
  });

  it("2 → «2 строки»", () => {
    expect(formatRussianCount(2, "строка", "строки", "строк")).toBe("2 строки");
  });

  it("5 → «5 строк»", () => {
    expect(formatRussianCount(5, "строка", "строки", "строк")).toBe("5 строк");
  });

  it("11 → «11 запусков»", () => {
    expect(formatRussianCount(11, "запуск", "запуска", "запусков")).toBe("11 запусков");
  });

  it("21 → «21 запуск»", () => {
    expect(formatRussianCount(21, "запуск", "запуска", "запусков")).toBe("21 запуск");
  });

  it("использует русское форматирование разрядов (пробел-разделитель)", () => {
    // 1 000 — неразрывный пробел в ru-RU
    const result = formatRussianCount(1000, "строка", "строки", "строк");
    expect(result).toMatch(/^1.000 строк$/);
  });
});

describe("russianCountIsOne", () => {
  it("1 → true", () => {
    expect(russianCountIsOne(1)).toBe(true);
  });

  it("11 → false", () => {
    expect(russianCountIsOne(11)).toBe(false);
  });

  it("21 → true", () => {
    expect(russianCountIsOne(21)).toBe(true);
  });

  it("отрицательное -1 → true (по модулю)", () => {
    expect(russianCountIsOne(-1)).toBe(true);
  });
});
