/**
 * Тесты fetchJson: Bearer header, 401-retry, ошибки.
 */
import { describe, it, expect, vi, beforeEach, afterEach } from "vitest";
import { fetchJson } from "@/lib/api";

// Мокаем auth-модуль
vi.mock("@/lib/auth", () => ({
  getStoredToken: vi.fn().mockReturnValue("mock_token"),
  loginToBackend: vi.fn().mockResolvedValue({ token: "new_token", role: "owner" }),
  logout: vi.fn(),
}));

vi.mock("@/lib/tg", () => ({
  getInitData: () => "",
  initTheme: vi.fn(),
}));

function createMockResponse(status: number, body: object): Response {
  return {
    ok: status >= 200 && status < 300,
    status,
    json: () => Promise.resolve(body),
  } as unknown as Response;
}

describe("fetchJson", () => {
  beforeEach(() => {
    vi.clearAllMocks();
  });

  afterEach(() => {
    vi.restoreAllMocks();
  });

  // Передаёт Bearer токен в заголовке
  it("передаёт Authorization Bearer из getStoredToken", async () => {
    const mockFetch = vi.fn().mockResolvedValue(createMockResponse(200, { ok: true }));
    global.fetch = mockFetch;
    await fetchJson("/test");
    const [, opts] = mockFetch.mock.calls[0] as [string, RequestInit];
    expect((opts.headers as Record<string, string>)["Authorization"]).toBe("Bearer mock_token");
  });

  // 200 возвращает JSON
  it("возвращает JSON при успешном ответе", async () => {
    global.fetch = vi.fn().mockResolvedValue(createMockResponse(200, { data: 42 }));
    const result = await fetchJson<{ data: number }>("/test");
    expect(result.data).toBe(42);
  });

  // 401 → повтор после loginToBackend
  it("при 401 вызывает loginToBackend и повторяет запрос", async () => {
    const { loginToBackend, logout } = await import("@/lib/auth");
    let calls = 0;
    global.fetch = vi.fn().mockImplementation(() => {
      calls++;
      if (calls === 1) return Promise.resolve(createMockResponse(401, { detail: "Expired" }));
      return Promise.resolve(createMockResponse(200, { ok: true }));
    });
    await fetchJson("/test");
    expect(logout).toHaveBeenCalledOnce();
    expect(loginToBackend).toHaveBeenCalledOnce();
    expect(global.fetch).toHaveBeenCalledTimes(2);
  });

  // 500 бросает Error с detail
  it("бросает Error при 500", async () => {
    global.fetch = vi.fn().mockResolvedValue(createMockResponse(500, { detail: "Server Error" }));
    await expect(fetchJson("/fail")).rejects.toThrow("Server Error");
  });

  // Без токена: нет Authorization header
  it("не ставит Authorization если токена нет", async () => {
    const auth = await import("@/lib/auth");
    vi.spyOn(auth, "getStoredToken").mockReturnValue(null);
    const mockFetch = vi.fn().mockResolvedValue(createMockResponse(200, {}));
    global.fetch = mockFetch;
    await fetchJson("/no-auth");
    const [, opts] = mockFetch.mock.calls[0] as [string, RequestInit];
    expect((opts.headers as Record<string, string>)["Authorization"]).toBeUndefined();
  });
});
