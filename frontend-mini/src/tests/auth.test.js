import { beforeEach, describe, expect, it, vi } from "vitest";
import { getStoredRole, getStoredToken, loginToBackend, logout } from "../auth.js";

// Авторизация TMA: initData → /tma/auth → token+role в localStorage.
describe("auth flow", () => {
  beforeEach(() => {
    localStorage.clear();
    sessionStorage.clear();
    window.Telegram = { WebApp: { initData: "user=test&hash=abc" } };
  });

  // Успешный логин сохраняет token+role и шлёт initData на /tma/auth
  it("loginToBackend сохраняет token и role", async () => {
    global.fetch = vi.fn().mockResolvedValue({
      ok: true,
      json: async () => ({ token: "tok123", role: "owner" }),
    });
    const data = await loginToBackend();
    expect(data.token).toBe("tok123");
    expect(getStoredToken()).toBe("tok123");
    expect(getStoredRole()).toBe("owner");
    const [url, opts] = global.fetch.mock.calls[0];
    expect(url).toContain("/tma/auth");
    expect(JSON.parse(opts.body).init_data).toBe("user=test&hash=abc");
  });

  // При !ok бросаем Error с detail от бэка
  it("loginToBackend бросает ошибку при отказе", async () => {
    global.fetch = vi.fn().mockResolvedValue({
      ok: false,
      json: async () => ({ detail: "Нет доступа" }),
    });
    await expect(loginToBackend()).rejects.toThrow("Нет доступа");
  });

  // logout очищает оба хранилища
  it("logout чистит token", () => {
    localStorage.setItem("tma_token", "x");
    localStorage.setItem("tma_role", "owner");
    logout();
    expect(getStoredToken()).toBeNull();
    expect(getStoredRole()).toBeNull();
  });
});
