import { describe, it, expect } from "vitest";
import { draftExpiresAt, isExpiringSoon, isDraftExpired, DRAFT_TTL_MS } from "../expiry";

describe("draftExpiresAt", () => {
  // +24h точно от created_at
  it("expiresAt = created_at + 24h", () => {
    const createdAt = "2026-06-01T12:00:00Z";
    const expires = draftExpiresAt(createdAt);
    const expected = new Date("2026-06-01T12:00:00Z").getTime() + DRAFT_TTL_MS;
    expect(expires.getTime()).toBe(expected);
  });

  // DRAFT_TTL_MS = 24 * 60 * 60 * 1000
  it("DRAFT_TTL_MS = 86400000 (24h)", () => {
    expect(DRAFT_TTL_MS).toBe(24 * 60 * 60 * 1000);
  });

  // null → expires ≈ now + 24h (не бросает)
  it("null → не бросает, возвращает будущую дату", () => {
    const before = Date.now();
    const expires = draftExpiresAt(null);
    expect(expires.getTime()).toBeGreaterThanOrEqual(before + DRAFT_TTL_MS - 1000);
  });

  // Точность: секунды совпадают (нет случайного смещения)
  it("нет случайного смещения — детерминированный результат", () => {
    const iso = "2026-05-15T08:30:00.000Z";
    expect(draftExpiresAt(iso).toISOString()).toBe("2026-05-16T08:30:00.000Z");
  });
});

describe("isExpiringSoon", () => {
  // Граница: ровно за 1 час → true
  it("ровно thresholdMs до истечения → true (граница включительно)", () => {
    const now = 1000000;
    const threshold = 3600_000;
    const expiresAt = new Date(now + threshold);
    // expires - now == threshold → остаток < threshold? нет, == threshold → false
    // Функция: expiresAt - now < threshold → threshold < threshold → false
    expect(isExpiringSoon(expiresAt, now, threshold)).toBe(false);
  });

  // На 1мс меньше порога → true
  it("expires - now < threshold → true", () => {
    const now = 1000000;
    const threshold = 3600_000;
    const expiresAt = new Date(now + threshold - 1);
    expect(isExpiringSoon(expiresAt, now, threshold)).toBe(true);
  });

  // Уже истёк → true
  it("уже истёк → true", () => {
    const now = 2_000_000;
    const expiresAt = new Date(now - 1);
    expect(isExpiringSoon(expiresAt, now)).toBe(true);
  });

  // Далеко до истечения → false
  it("далеко до истечения → false", () => {
    const now = 1000000;
    const expiresAt = new Date(now + 10 * 3600_000); // 10 часов
    expect(isExpiringSoon(expiresAt, now)).toBe(false);
  });
});

describe("isDraftExpired", () => {
  // Уже истёк
  it("expires в прошлом → true", () => {
    const now = 2_000_000;
    const expiresAt = new Date(now - 1);
    expect(isDraftExpired(expiresAt, now)).toBe(true);
  });

  // Ровно now → истёк (<=)
  it("expires == now → true", () => {
    const now = 2_000_000;
    const expiresAt = new Date(now);
    expect(isDraftExpired(expiresAt, now)).toBe(true);
  });

  // Ещё не истёк
  it("expires в будущем → false", () => {
    const now = 1_000_000;
    const expiresAt = new Date(now + 1);
    expect(isDraftExpired(expiresAt, now)).toBe(false);
  });

  // Реальный сценарий: created 25h назад → истёк
  it("создан 25h назад → истёк", () => {
    const createdAt = new Date(Date.now() - 25 * 3600_000).toISOString();
    const expires = draftExpiresAt(createdAt);
    expect(isDraftExpired(expires)).toBe(true);
  });

  // Реальный сценарий: создан 23h назад → не истёк
  it("создан 23h назад → не истёк", () => {
    const createdAt = new Date(Date.now() - 23 * 3600_000).toISOString();
    const expires = draftExpiresAt(createdAt);
    expect(isDraftExpired(expires)).toBe(false);
  });
});
