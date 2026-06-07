/**
 * Тесты auth flow.
 * Портировано из frontend-mini/src/tests/auth.test.js → TypeScript.
 */
import { describe, it, expect, beforeEach, vi, afterEach } from "vitest";
import { loginToBackend, getStoredToken, getStoredRole, logout } from "@/lib/auth";

// Мокаем tg.getInitData — в jsdom window.Telegram нет
vi.mock("@/lib/tg", () => ({
  getInitData: () => "mock_init_data",
  initTheme: vi.fn(),
}));

// Помощник: мокаем fetch
function mockFetch(status: number, body: object) {
  global.fetch = vi.fn().mockResolvedValueOnce({
    ok: status >= 200 && status < 300,
    status,
    json: () => Promise.resolve(body),
  } as Response);
}

describe("loginToBackend", () => {
  beforeEach(() => {
    localStorage.clear();
    sessionStorage.clear();
  });

  afterEach(() => {
    vi.restoreAllMocks();
  });

  // Успешный логин сохраняет токен и роль
  it("сохраняет токен и роль в localStorage при успехе", async () => {
    mockFetch(200, { token: "test_jwt", role: "owner" });
    const result = await loginToBackend();
    expect(result.token).toBe("test_jwt");
    expect(result.role).toBe("owner");
    expect(localStorage.getItem("tma_token")).toBe("test_jwt");
    expect(localStorage.getItem("tma_role")).toBe("owner");
  });

  // 403 — бросает Error с detail
  it("бросает ошибку при 403 (нет доступа)", async () => {
    mockFetch(403, { detail: "Нет доступа" });
    await expect(loginToBackend()).rejects.toThrow("Нет доступа");
  });

  // 503 — бросает Error
  it("бросает ошибку при 503 (Telegram не настроен)", async () => {
    mockFetch(503, { detail: "Telegram-бот не настроен" });
    await expect(loginToBackend()).rejects.toThrow("Telegram-бот не настроен");
  });
});

describe("getStoredToken / logout", () => {
  beforeEach(() => {
    localStorage.clear();
    sessionStorage.clear();
  });

  // getStoredToken возвращает null если нет токена
  it("возвращает null если токена нет", () => {
    expect(getStoredToken()).toBeNull();
  });

  // после loginToBackend токен доступен
  it("возвращает токен после loginToBackend", async () => {
    mockFetch(200, { token: "abc", role: "recipient" });
    await loginToBackend();
    expect(getStoredToken()).toBe("abc");
  });

  // logout очищает хранилища
  it("logout очищает localStorage и sessionStorage", async () => {
    mockFetch(200, { token: "abc", role: "owner" });
    await loginToBackend();
    logout();
    expect(getStoredToken()).toBeNull();
    expect(getStoredRole()).toBeNull();
  });
});
