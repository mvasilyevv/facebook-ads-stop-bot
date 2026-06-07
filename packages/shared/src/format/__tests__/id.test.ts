import { describe, it, expect } from "vitest";
import { truncateAdId } from "../id";

describe("truncateAdId", () => {
  // Длинный ID обрезается
  it("длинный ID → head...tail", () => {
    expect(truncateAdId("120211234567898761")).toBe("120211...8761");
  });

  // Короткий ID не обрезается (≤ headLen+tailLen+3)
  it("короткий ID остаётся как есть", () => {
    expect(truncateAdId("12345")).toBe("12345");
  });

  // null → "—"
  it("null → —", () => {
    expect(truncateAdId(null)).toBe("—");
  });

  // undefined → "—"
  it("undefined → —", () => {
    expect(truncateAdId(undefined)).toBe("—");
  });

  // Кастомные headLen/tailLen
  it("кастомные headLen=4, tailLen=3", () => {
    expect(truncateAdId("1234567890123", 4, 3)).toBe("1234...123");
  });

  // Граничный случай: ровно headLen+tailLen+3 символов
  it("граница: ровно headLen+tailLen+3 — не обрезается", () => {
    // 6+4+3 = 13 символов → не обрезается
    expect(truncateAdId("1234567890123")).toBe("1234567890123");
  });

  // 14 символов — уже обрезается
  it("14 символов — обрезается", () => {
    expect(truncateAdId("12345678901234")).toBe("123456...1234");
  });
});
