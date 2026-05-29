// Тест: API client пишет X-API-Key, обрабатывает JSON detail и пробрасывает ApiError.
import { describe, it, expect, beforeEach, vi } from "vitest";
import { apiClient, ApiError, buildQuery } from "@/lib/api/client";
import { useAuthStore } from "@/stores/auth";

describe("api client", () => {
  beforeEach(() => {
    useAuthStore.setState({ apiKey: "test-key" });
    vi.restoreAllMocks();
  });

  // Тест: GET передаёт X-API-Key.
  it("передаёт X-API-Key", async () => {
    const fetchMock = vi.fn().mockResolvedValue(
      new Response(JSON.stringify({ ok: true }), { status: 200, headers: { "content-type": "application/json" } }),
    );
    globalThis.fetch = fetchMock;
    await apiClient.get("/health");
    expect(fetchMock).toHaveBeenCalledWith(
      "/api/health",
      expect.objectContaining({
        headers: expect.objectContaining({ "X-API-Key": "test-key" }),
      }),
    );
  });

  // Тест: при 422 с detail-массивом — собирается читаемое сообщение.
  it("разбирает detail-массив в ошибке", async () => {
    const body = { detail: [{ msg: "field required" }, { msg: "bad value" }] };
    // Каждый вызов fetch отдаёт свежий Response — Response body одноразовый.
    globalThis.fetch = vi.fn().mockImplementation(
      () =>
        Promise.resolve(
          new Response(JSON.stringify(body), {
            status: 422,
            headers: { "content-type": "application/json" },
          }),
        ),
    );
    await expect(apiClient.get("/offers")).rejects.toBeInstanceOf(ApiError);
    try {
      await apiClient.get("/offers");
    } catch (e) {
      expect((e as Error).message).toMatch(/422.*field required.*bad value/);
    }
  });

  // Тест: 204 No Content возвращает null.
  it("204 возвращает null", async () => {
    globalThis.fetch = vi.fn().mockResolvedValue(new Response(null, { status: 204 }));
    const out = await apiClient.delete("/offers/abc");
    expect(out).toBeNull();
  });

  // Тест: buildQuery фильтрует null/undefined/empty.
  it("buildQuery фильтрует пустые", () => {
    expect(buildQuery({ a: 1, b: null, c: "", d: "x" })).toBe("?a=1&d=x");
    expect(buildQuery({})).toBe("");
    expect(buildQuery(undefined)).toBe("");
  });
});
