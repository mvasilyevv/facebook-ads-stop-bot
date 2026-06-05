// Тест useRealtimeInvalidation: WS-события инвалидируют нужные query-ключи (live-update).

import { describe, it, expect, vi, beforeEach } from "vitest";
import { renderHook } from "@testing-library/react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { createElement, type ReactNode } from "react";

// Мок useDashboardSocket — захватываем переданный onMessage, чтобы дёргать его вручную.
let captured: ((d: unknown) => void) | undefined;
vi.mock("@/lib/websocket/useDashboardSocket", () => ({
  useDashboardSocket: (opts: { onMessage?: (d: unknown) => void }) => {
    captured = opts.onMessage;
    return {
      status: "connected",
      pollingFallback: false,
      reconnectAttempt: 0,
      forceReconnect: vi.fn(),
    };
  },
}));

import { useRealtimeInvalidation } from "@/lib/websocket/useRealtimeInvalidation";

function setup() {
  const qc = new QueryClient();
  const spy = vi.spyOn(qc, "invalidateQueries");
  const wrapper = ({ children }: { children: ReactNode }) =>
    createElement(QueryClientProvider, { client: qc }, children);
  renderHook(() => useRealtimeInvalidation(), { wrapper });
  return spy;
}

function invalidatedKeys(spy: ReturnType<typeof setup>): string[] {
  return spy.mock.calls.map((c) => (c[0] as { queryKey: string[] }).queryKey[0]);
}

describe("useRealtimeInvalidation", () => {
  beforeEach(() => {
    captured = undefined;
  });

  // scan_finished → обновляем дашборд, список ads и observer.
  it("scan_finished инвалидирует dashboard/ads/observer", () => {
    const spy = setup();
    captured?.({ type: "scan_finished" });
    const keys = invalidatedKeys(spy);
    expect(keys).toContain("dashboard");
    expect(keys).toContain("ads");
    expect(keys).toContain("observer");
  });

  // alert_created → дашборд + ads.
  it("alert_created инвалидирует dashboard/ads", () => {
    const spy = setup();
    captured?.({ type: "alert_created" });
    const keys = invalidatedKeys(spy);
    expect(keys).toContain("dashboard");
    expect(keys).toContain("ads");
  });

  // Неизвестный/служебный тип (ping) — без инвалидаций.
  it("ping не инвалидирует ничего", () => {
    const spy = setup();
    captured?.({ type: "ping" });
    expect(spy).not.toHaveBeenCalled();
  });

  // Сообщение без type — не падает и не инвалидирует.
  it("сообщение без type безопасно игнорируется", () => {
    const spy = setup();
    captured?.({ foo: "bar" });
    expect(spy).not.toHaveBeenCalled();
  });
});
