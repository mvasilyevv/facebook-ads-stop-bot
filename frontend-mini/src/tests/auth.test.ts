/**
 * Тесты auth flow.
 * Портировано из frontend-mini/src/tests/auth.test.js → TypeScript.
 */
import { act, renderHook } from "@testing-library/react";
import { describe, it, expect, beforeEach, vi, afterEach } from "vitest";
import {
  loginToBackend,
  getStoredToken,
  getStoredRole,
  logout,
  useStoredToken,
} from "@/lib/auth";

// Мокаем tg.getInitData — в jsdom window.Telegram нет
vi.mock("@/lib/tg", () => ({
  getInitData: () => "mock_init_data",
  initTheme: vi.fn(),
}));

// Помощник: мокаем fetch
function mockFetch(status: number, body: object) {
  global.fetch = vi.fn().mockResolvedValueOnce(
    new Response(JSON.stringify(body), {
      status,
      headers: { "Content-Type": "application/json" },
    }),
  );
}

describe("loginToBackend", () => {
  beforeEach(() => {
    logout();
    localStorage.clear();
    sessionStorage.clear();
  });

  afterEach(() => {
    vi.restoreAllMocks();
  });

  // Успешный логин сохраняет токен и роль только в памяти WebView.
  it("заменяет in-memory токен и роль при успехе", async () => {
    localStorage.setItem("tma_token", "legacy_owner_token");
    localStorage.setItem("tma_role", "owner");
    mockFetch(200, { token: "test_jwt", role: "owner" });
    const result = await loginToBackend();
    expect(result.token).toBe("test_jwt");
    expect(result.role).toBe("owner");
    expect(getStoredToken()).toBe("test_jwt");
    expect(getStoredRole()).toBe("owner");
    expect(localStorage.getItem("tma_token")).toBeNull();
    expect(localStorage.getItem("tma_role")).toBeNull();
  });

  // 403 — бросает Error из единого ApiProblem.
  it("бросает ошибку при 403 (нет доступа)", async () => {
    mockFetch(403, {
      code: "FORBIDDEN",
      message: "Нет доступа",
      correlation_id: "corr-auth-403",
      field_errors: null,
    });
    await expect(loginToBackend()).rejects.toThrow("Нет доступа");
  });

  // 503 — бросает Error
  it("бросает ошибку при 503 (Telegram не настроен)", async () => {
    mockFetch(503, {
      code: "TELEGRAM_UNAVAILABLE",
      message: "Telegram-бот не настроен",
      correlation_id: "corr-auth-503",
      field_errors: null,
    });
    await expect(loginToBackend()).rejects.toThrow("Telegram-бот не настроен");
  });
});

describe("getStoredToken / logout", () => {
  beforeEach(() => {
    logout();
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

  it("реактивно публикует ротацию и очистку токена transport-подписчикам", async () => {
    mockFetch(200, { token: "expired_token", role: "owner" });
    await loginToBackend();
    const { result } = renderHook(() => useStoredToken());
    expect(result.current).toBe("expired_token");

    mockFetch(200, { token: "rotated_token", role: "owner" });
    await act(async () => {
      await loginToBackend();
    });
    expect(result.current).toBe("rotated_token");

    act(() => logout());
    expect(result.current).toBeNull();
  });
});
