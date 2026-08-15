import { describe, expect, it } from "vitest";

import { formatRussianCount, russianCountForm } from "@/lib/utils/russianCount";

describe("русские числительные в операторских счётчиках", () => {
  it("согласует строки и запуски по правилам русского языка", () => {
    expect(formatRussianCount(1, "строка", "строки", "строк")).toBe("1 строка");
    expect(formatRussianCount(2, "строка", "строки", "строк")).toBe("2 строки");
    expect(formatRussianCount(5, "строка", "строки", "строк")).toBe("5 строк");
    expect(formatRussianCount(21, "запуск", "запуска", "запусков")).toBe("21 запуск");
    expect(formatRussianCount(22, "запуск", "запуска", "запусков")).toBe("22 запуска");
    expect(formatRussianCount(25, "запуск", "запуска", "запусков")).toBe("25 запусков");
  });

  it("не считает 11 и 14 формами единственного и малого числа", () => {
    expect(russianCountForm(11, "запуск", "запуска", "запусков")).toBe("запусков");
    expect(russianCountForm(14, "запуск", "запуска", "запусков")).toBe("запусков");
  });
});
