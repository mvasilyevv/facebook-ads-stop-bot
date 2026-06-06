// Тест: buildApiError и apiGetWithCount — базовые контракты HTTP-клиента.

import { describe, it, expect, vi, afterEach } from "vitest";
import { buildApiError, buildQuery, ApiError } from "@/lib/api/client";

// Мок auth store — без ключа API (модуль-уровень: hoisted корректно).
vi.mock("@/stores/auth", () => ({
  useAuthStore: { getState: () => ({ apiKey: null }) },
}));

// ─── buildApiError ─────────────────────────────────────────────────────────────

describe("buildApiError — разбор FastAPI detail", () => {
  // Тест: detail — строка → прямо вставляется в message.
  it("detail = string", async () => {
    const resp = new Response(JSON.stringify({ detail: "Объявление не найдено" }), {
      status: 404,
      headers: { "Content-Type": "application/json" },
    });
    const err = await buildApiError(resp);
    expect(err).toBeInstanceOf(ApiError);
    expect(err.status).toBe(404);
    expect(err.message).toContain("Объявление не найдено");
    expect(err.detail).toBe("Объявление не найдено");
  });

  // Тест: detail — массив объектов валидации (Pydantic) → msg конкатенируется.
  it("detail = array[{msg, loc, type}] (Pydantic validation)", async () => {
    const detail = [
      { msg: "field required", loc: ["body", "minutes"], type: "missing" },
      { msg: "value too small", loc: ["body", "limit"], type: "value_error" },
    ];
    const resp = new Response(JSON.stringify({ detail }), {
      status: 422,
      headers: { "Content-Type": "application/json" },
    });
    const err = await buildApiError(resp);
    expect(err.status).toBe(422);
    expect(err.message).toContain("field required");
    expect(err.message).toContain("value too small");
    expect(Array.isArray(err.detail)).toBe(true);
  });

  // Тест: detail — объект (не строка, не массив) → JSON.stringify.
  it("detail = object → JSON.stringify", async () => {
    const detail = { code: "RATE_LIMITED", retry_after: 60 };
    const resp = new Response(JSON.stringify({ detail }), {
      status: 429,
      headers: { "Content-Type": "application/json" },
    });
    const err = await buildApiError(resp);
    expect(err.status).toBe(429);
    expect(err.message).toContain("RATE_LIMITED");
  });

  // Тест: ответ без JSON (plain text) → текст вставляется в message.
  it("plain text response", async () => {
    const resp = new Response("Internal Server Error", {
      status: 500,
      headers: { "Content-Type": "text/plain" },
    });
    const err = await buildApiError(resp);
    expect(err.status).toBe(500);
    expect(err.message).toContain("Internal Server Error");
  });

  // Тест: пустое тело → дефолтное сообщение.
  it("пустое тело → дефолтное сообщение", async () => {
    const resp = new Response("", {
      status: 503,
      statusText: "Service Unavailable",
    });
    const err = await buildApiError(resp);
    expect(err.status).toBe(503);
    expect(err.message).toMatch(/503/);
  });
});

// ─── buildQuery ────────────────────────────────────────────────────────────────

describe("buildQuery — сборка query-string", () => {
  // Тест: null/undefined/пустая строка фильтруются.
  it("фильтрует null/undefined/пустую строку", () => {
    const qs = buildQuery({ a: null, b: undefined, c: "", d: "ok" });
    expect(qs).toBe("?d=ok");
  });

  // Тест: числа и булевые конвертируются в строку.
  it("числа и булевые → строка", () => {
    const qs = buildQuery({ limit: 10, include_inactive: true });
    expect(qs).toContain("limit=10");
    expect(qs).toContain("include_inactive=true");
  });

  // Тест: пустой объект → пустая строка.
  it("пустой объект → пустая строка", () => {
    expect(buildQuery({})).toBe("");
    expect(buildQuery(undefined)).toBe("");
  });
});

// ─── apiGetWithCount — X-Total-Count парсинг ──────────────────────────────────

describe("apiGetWithCount — парсинг X-Total-Count", () => {
  afterEach(() => {
    vi.restoreAllMocks();
    vi.unstubAllGlobals();
  });

  // Тест: X-Total-Count есть и корректный → total=число.
  it("возвращает total из X-Total-Count", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn().mockResolvedValue(
        new Response(JSON.stringify([{ id: "1" }]), {
          status: 200,
          headers: {
            "Content-Type": "application/json",
            "X-Total-Count": "42",
          },
        }),
      ),
    );

    const { apiGetWithCount } = await import("@/lib/api/client");
    const result = await apiGetWithCount<{ id: string }[]>("/test");
    expect(result.total).toBe(42);
    expect(result.data).toHaveLength(1);
  });

  // Тест: заголовок отсутствует → total=null.
  it("нет X-Total-Count → total=null", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn().mockResolvedValue(
        new Response(JSON.stringify([]), {
          status: 200,
          headers: { "Content-Type": "application/json" },
        }),
      ),
    );

    const { apiGetWithCount } = await import("@/lib/api/client");
    const result = await apiGetWithCount<unknown[]>("/test");
    expect(result.total).toBeNull();
  });

  // Тест: заголовок пустая строка → total=null.
  it("X-Total-Count пустая строка → total=null", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn().mockResolvedValue(
        new Response(JSON.stringify([]), {
          status: 200,
          headers: { "Content-Type": "application/json", "X-Total-Count": "" },
        }),
      ),
    );

    const { apiGetWithCount } = await import("@/lib/api/client");
    const result = await apiGetWithCount<unknown[]>("/test");
    expect(result.total).toBeNull();
  });
});
