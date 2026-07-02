/**
 * Тесты API-хуков useStatsToday/useStatsPeriod — форма queryKey и путь запроса.
 * Мокаем apiGet (низкоуровневый клиент), не сеть — паттерн изолированного юнит-теста хука.
 */
import { describe, it, expect, vi, beforeEach } from "vitest";
import { renderHook, waitFor } from "@testing-library/react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import type { ReactNode } from "react";

const mockApiGet = vi.fn();

vi.mock("@/lib/api/client", () => ({
  apiGet: (...args: unknown[]) => mockApiGet(...args),
}));

import { useStatsToday, useStatsPeriod } from "@/lib/api/stats";

function wrapper(qc: QueryClient) {
  return function Wrapper({ children }: { children: ReactNode }) {
    return <QueryClientProvider client={qc}>{children}</QueryClientProvider>;
  };
}

describe("useStatsToday", () => {
  let qc: QueryClient;

  beforeEach(() => {
    qc = new QueryClient({ defaultOptions: { queries: { retry: false } } });
    mockApiGet.mockReset();
    mockApiGet.mockResolvedValue({ cabinet_day_start: "", generated_at: "", meta: {}, tracker: {} });
  });

  // Без breakdown — queryKey без параметров, путь /stats/today без query.
  it("без breakdown queryKey = ['stats','today',undefined]", async () => {
    const { result } = renderHook(() => useStatsToday(), { wrapper: wrapper(qc) });
    await waitFor(() => expect(result.current.isSuccess).toBe(true));

    expect(mockApiGet).toHaveBeenCalledWith("/stats/today", undefined, expect.anything());
    const queries = qc.getQueryCache().findAll({ queryKey: ["stats", "today"] });
    expect(queries).toHaveLength(1);
    expect(queries[0]!.queryKey).toEqual(["stats", "today", undefined]);
  });

  // С breakdown="offer" — параметр попадает и в queryKey (разный кэш), и в запрос.
  it("с breakdown='offer' queryKey включает параметр и путь содержит его в query", async () => {
    const { result } = renderHook(() => useStatsToday("offer"), { wrapper: wrapper(qc) });
    await waitFor(() => expect(result.current.isSuccess).toBe(true));

    expect(mockApiGet).toHaveBeenCalledWith("/stats/today", { breakdown: "offer" }, expect.anything());
    const queries = qc.getQueryCache().findAll({ queryKey: ["stats", "today"] });
    expect(queries[0]!.queryKey).toEqual(["stats", "today", { breakdown: "offer" }]);
  });

  // breakdown="offer" и breakdown="campaign" — разные записи кэша (не перезаписывают друг друга).
  it("разные breakdown дают разные queryKey (раздельный кэш)", async () => {
    renderHook(() => useStatsToday("offer"), { wrapper: wrapper(qc) });
    renderHook(() => useStatsToday("campaign"), { wrapper: wrapper(qc) });
    await waitFor(() => {
      const queries = qc.getQueryCache().findAll({ queryKey: ["stats", "today"] });
      expect(queries).toHaveLength(2);
    });
  });
});

describe("useStatsPeriod", () => {
  let qc: QueryClient;

  beforeEach(() => {
    qc = new QueryClient({ defaultOptions: { queries: { retry: false } } });
    mockApiGet.mockReset();
    mockApiGet.mockResolvedValue({ from_iso: "", to_iso: "", meta: {}, tracker: {} });
  });

  // queryKey включает from_iso/to_iso, путь запроса — /stats/period с этими параметрами.
  it("queryKey включает from_iso/to_iso и передаёт их в apiGet", async () => {
    const params = { from_iso: "2026-06-01T00:00:00Z", to_iso: "2026-07-01T00:00:00Z" };
    const { result } = renderHook(() => useStatsPeriod(params), { wrapper: wrapper(qc) });
    await waitFor(() => expect(result.current.isSuccess).toBe(true));

    expect(mockApiGet).toHaveBeenCalledWith("/stats/period", params, expect.anything());
    const queries = qc.getQueryCache().findAll({ queryKey: ["stats", "period"] });
    expect(queries[0]!.queryKey).toEqual(["stats", "period", params]);
  });
});
