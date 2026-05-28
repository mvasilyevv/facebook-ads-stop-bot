// Тест: форматтеры безопасны к null/undefined и корректно форматируют значения.
import { describe, it, expect } from "vitest";
import {
  formatSpend,
  formatCompact,
  formatInt,
  truncateAdId,
  formatDuration,
  formatRelativeTime,
} from "@/lib/utils/format";

describe("format", () => {
  // Тест: formatSpend форматирует доллары и отдаёт "—" при null.
  it("formatSpend корректно работает", () => {
    expect(formatSpend(1234.5)).toBe("$1,234.50");
    expect(formatSpend(null)).toBe("—");
    expect(formatSpend(undefined)).toBe("—");
    expect(formatSpend("invalid")).toBe("—");
  });

  // Тест: formatCompact ужимает большие числа.
  it("formatCompact ужимает числа", () => {
    expect(formatCompact(12400)).toMatch(/12\.4K/i);
    expect(formatCompact(1_200_000)).toMatch(/1\.2M/i);
    expect(formatCompact(null)).toBe("—");
  });

  // Тест: formatInt с разделителями.
  it("formatInt с тысячами", () => {
    expect(formatInt(1234)).toBe("1,234");
    expect(formatInt(null)).toBe("—");
  });

  // Тест: truncateAdId укорачивает длинные ID.
  it("truncateAdId укорачивает", () => {
    expect(truncateAdId("12021187654321")).toMatch(/^120211\.\.\.\d{4}$/);
    expect(truncateAdId("short")).toBe("short");
    expect(truncateAdId(null)).toBe("—");
  });

  // Тест: formatDuration в человекочитаемый вид.
  it("formatDuration", () => {
    expect(formatDuration(45)).toBe("45s");
    expect(formatDuration(120)).toBe("2m");
    expect(formatDuration(7200)).toBe("2h");
    expect(formatDuration(null)).toBe("—");
  });

  // Тест: formatRelativeTime для прошлых и будущих дат.
  it("formatRelativeTime", () => {
    const past = new Date(Date.now() - 5 * 60 * 1000).toISOString();
    const out = formatRelativeTime(past);
    // RelativeTimeFormat в стиле "narrow" даёт "5m ago" / "5 min. ago" / "5 мин. назад".
    // Проверяем что в строке есть либо m, либо min.
    expect(out).toMatch(/m/);
    expect(formatRelativeTime(null)).toBe("—");
  });
});
