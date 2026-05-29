// Тест: useDashboardSocket делает reconnect и fallback'ит на polling после 3 ошибок.
import { describe, it, expect, beforeEach, afterEach, vi } from "vitest";
import { renderHook, act } from "@testing-library/react";
import { useDashboardSocket } from "@/lib/websocket/useDashboardSocket";

/**
 * Мокаем глобальный WebSocket. При создании сразу зовём onclose, чтобы
 * имитировать неудачное подключение (например backend WS не реализован).
 */
class FailingWebSocket {
  static instances: FailingWebSocket[] = [];
  url: string;
  onclose: ((ev: unknown) => void) | null = null;
  listeners: Record<string, Array<(ev: unknown) => void>> = {};

  constructor(url: string) {
    this.url = url;
    FailingWebSocket.instances.push(this);
    queueMicrotask(() => {
      const cb = this.listeners.close;
      if (cb) for (const fn of cb) fn({});
    });
  }
  addEventListener(name: string, cb: (ev: unknown) => void) {
    (this.listeners[name] ??= []).push(cb);
  }
  removeEventListener() {}
  close() {}
}

describe("useDashboardSocket", () => {
  beforeEach(() => {
    vi.useFakeTimers();
    FailingWebSocket.instances = [];
    (globalThis as unknown as { WebSocket: unknown }).WebSocket = FailingWebSocket;
  });

  afterEach(() => {
    vi.useRealTimers();
  });

  // Тест: после 3 неудачных reconnect'ов hook переключается на polling.
  it("переключается на polling после 3 неудач", async () => {
    const { result } = renderHook(() => useDashboardSocket({ enabled: true }));

    // Дать microtask'у отработать первый close.
    await act(async () => {
      await Promise.resolve();
    });
    expect(["connecting", "reconnecting"]).toContain(result.current.status);

    // Прокручиваем таймеры на 30+ секунд, чтобы успели сработать все reconnect-таймеры.
    await act(async () => {
      await vi.advanceTimersByTimeAsync(30_000);
    });

    expect(result.current.pollingFallback).toBe(true);
    expect(result.current.status).toBe("polling");
  });

  // Тест: disabled hook не открывает WebSocket вообще.
  it("disabled не открывает WS", () => {
    renderHook(() => useDashboardSocket({ enabled: false }));
    expect(FailingWebSocket.instances).toHaveLength(0);
  });
});
