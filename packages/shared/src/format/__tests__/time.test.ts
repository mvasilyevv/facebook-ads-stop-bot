import { describe, it, expect, vi, afterEach } from "vitest";
import {
  formatDateTime,
  formatDuration,
  formatRelativeTime,
  formatTimeOfDay,
  formatZonedDateTime,
  formatZonedTime,
  timezoneEvidenceLabel,
} from "../time";

describe("formatDateTime", () => {
  // ISO строка → UTC datetime без микросекунд
  it("парсит ISO и форматирует как UTC", () => {
    expect(formatDateTime("2026-05-28T14:32:00Z")).toBe("2026-05-28 14:32");
  });

  // Объект Date
  it("принимает Date объект", () => {
    expect(formatDateTime(new Date("2026-01-01T00:00:00Z"))).toBe(
      "2026-01-01 00:00",
    );
  });

  // null → "—"
  it("null → —", () => {
    expect(formatDateTime(null)).toBe("—");
  });

  // undefined → "—"
  it("undefined → —", () => {
    expect(formatDateTime(undefined)).toBe("—");
  });

  // Невалидная строка → "—"
  it("невалидная строка → —", () => {
    expect(formatDateTime("not-a-date")).toBe("—");
  });

  // UTC-фиксация: время НЕ сдвигается по локали
  it("UTC-фиксация: 00:00 UTC остаётся 00:00", () => {
    // Если бы был локальный сдвиг +3h — показал бы 03:00, что неверно
    const result = formatDateTime("2026-06-01T00:00:00Z");
    expect(result).toBe("2026-06-01 00:00");
  });
});

describe("formatTimeOfDay", () => {
  // Время суток UTC
  it("форматирует время суток в UTC", () => {
    expect(formatTimeOfDay("2026-05-28T14:32:18Z")).toBe("14:32:18");
  });

  // Полночь UTC
  it("полночь UTC", () => {
    expect(formatTimeOfDay("2026-05-28T00:00:00Z")).toBe("00:00:00");
  });

  // null → "—"
  it("null → —", () => {
    expect(formatTimeOfDay(null)).toBe("—");
  });
});

describe("formatRelativeTime", () => {
  afterEach(() => {
    vi.useRealTimers();
  });

  // "сейчас" — менее 45 секунд
  it("< 45s → сейчас", () => {
    vi.useFakeTimers();
    const now = Date.now();
    vi.setSystemTime(now);
    const iso = new Date(now - 30_000).toISOString();
    expect(formatRelativeTime(iso)).toBe("сейчас");
  });

  // Минуты
  it("5 минут → 5 мин", () => {
    vi.useFakeTimers();
    const now = Date.now();
    vi.setSystemTime(now);
    const iso = new Date(now - 5 * 60_000).toISOString();
    expect(formatRelativeTime(iso)).toBe("5 мин");
  });

  // Часы
  it("2 часа → 2 ч", () => {
    vi.useFakeTimers();
    const now = Date.now();
    vi.setSystemTime(now);
    const iso = new Date(now - 2 * 3600_000).toISOString();
    expect(formatRelativeTime(iso)).toBe("2 ч");
  });

  // Дни
  it("3 дня → 3 дн", () => {
    vi.useFakeTimers();
    const now = Date.now();
    vi.setSystemTime(now);
    const iso = new Date(now - 3 * 86400_000).toISOString();
    expect(formatRelativeTime(iso)).toBe("3 дн");
  });

  // null → "—"
  it("null → —", () => {
    expect(formatRelativeTime(null)).toBe("—");
  });

  // Невалидная строка → "—"
  it("невалидная строка → —", () => {
    expect(formatRelativeTime("bad")).toBe("—");
  });
});

describe("formatDuration", () => {
  // Секунды
  it("42 секунды", () => {
    expect(formatDuration(42)).toBe("42s");
  });

  // Минуты
  it("90 секунд → 2m", () => {
    expect(formatDuration(90)).toBe("2m");
  });

  // Часы + минуты
  it("2h 4m", () => {
    expect(formatDuration(2 * 3600 + 4 * 60)).toBe("2h 4m");
  });

  // Ровные часы (без минут)
  it("3h ровно", () => {
    expect(formatDuration(3 * 3600)).toBe("3h");
  });

  // Дни
  it("2 дня → 2d", () => {
    expect(formatDuration(2 * 86400)).toBe("2d");
  });

  // null → "—"
  it("null → —", () => {
    expect(formatDuration(null)).toBe("—");
  });

  // Отрицательное → "—"
  it("отрицательное → —", () => {
    expect(formatDuration(-1)).toBe("—");
  });

  // Ноль
  it("0 секунд → 0s", () => {
    expect(formatDuration(0)).toBe("0s");
  });
});

describe("operator timezone formatting", () => {
  it("uses the cabinet timezone, not the browser timezone", () => {
    expect(formatZonedTime("2026-07-19T00:30:00Z", "Europe/Kaliningrad")).toBe(
      "02:30",
    );
    expect(
      formatZonedDateTime("2026-07-19T00:30:00Z", "Europe/Kaliningrad"),
    ).toContain("19.07.2026");
  });

  it("fails closed for an invalid timezone", () => {
    expect(formatZonedTime("2026-07-19T00:30:00Z", "invalid/timezone")).toBe(
      "—",
    );
  });

  it("labels single, mixed and unknown evidence without inventing UTC", () => {
    expect(timezoneEvidenceLabel("Asia/Tokyo", "single")).toBe("Asia/Tokyo");
    expect(timezoneEvidenceLabel(null, "mixed")).toBe(
      "Несколько часовых поясов · границы по каждому кабинету",
    );
    expect(timezoneEvidenceLabel(null, "unknown")).toBe("Не подтверждён");
  });
});
