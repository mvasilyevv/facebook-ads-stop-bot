// Тест: useDashboardSocket делает reconnect и fallback'ит на polling после 3 ошибок.

import { describe, it, expect, beforeEach, afterEach, vi } from "vitest";
import { renderHook, act } from "@testing-library/react";
import { useDashboardSocket } from "@/lib/websocket/useDashboardSocket";

/**
 * Мокаем глобальный WebSocket. При создании сразу зовём close, чтобы
 * имитировать неудачное подключение (например backend WS не реализован).
 */
class FailingWebSocket {
  static instances: FailingWebSocket[] = [];
  url: string;
  listeners: Record<string, Array<(ev: unknown) => void>> = {};

  constructor(url: string) {
    this.url = url;
    FailingWebSocket.instances.push(this);
    // Сразу закрываем через microtask — даём hook'у зарегистрировать listeners.
    queueMicrotask(() => {
      const cbs = this.listeners["close"];
      if (cbs) for (const fn of cbs) fn({});
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
    vi.restoreAllMocks();
  });

  // Тест: после 3 неудачных reconnect'ов hook переключается на polling.
  it("переключается на polling после 3 неудач", async () => {
    const { result } = renderHook(() => useDashboardSocket({ enabled: true }));

    // Ждём первый microtask (первый close).
    await act(async () => {
      await Promise.resolve();
    });

    // Прокручиваем все backoff-таймеры (1s + 2s + ещё раз) → 3 неудачи → polling.
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

  // Тест: reconnectAttempt растёт с каждой неудачей.
  it("reconnectAttempt инкрементируется", async () => {
    const { result } = renderHook(() => useDashboardSocket({ enabled: true }));

    await act(async () => {
      await Promise.resolve();
    });
    // После первого close.
    await act(async () => {
      await vi.advanceTimersByTimeAsync(1_500);
    });
    // Должно быть хотя бы 1.
    expect(result.current.reconnectAttempt).toBeGreaterThanOrEqual(1);
  });

  // Тест: onMessage вызывается при получении сообщения.
  it("onMessage вызывается при успешном сообщении", async () => {
    /** Успешный WS — не закрывается сразу. */
    class SuccessWebSocket {
      static instances: SuccessWebSocket[] = [];
      url: string;
      listeners: Record<string, Array<(ev: unknown) => void>> = {};

      constructor(url: string) {
        this.url = url;
        SuccessWebSocket.instances.push(this);
        // Эмулируем open через microtask.
        queueMicrotask(() => {
          const cbs = this.listeners["open"];
          if (cbs) for (const fn of cbs) fn({});
        });
      }
      addEventListener(name: string, cb: (ev: unknown) => void) {
        (this.listeners[name] ??= []).push(cb);
      }
      removeEventListener() {}
      close() {}

      /** Вспомогательный метод — шлёт message всем слушателям. */
      emit(data: string) {
        const cbs = this.listeners["message"];
        if (cbs) for (const fn of cbs) fn({ data });
      }
    }

    (globalThis as unknown as { WebSocket: unknown }).WebSocket = SuccessWebSocket;

    const onMessage = vi.fn();
    renderHook(() => useDashboardSocket({ enabled: true, onMessage }));

    await act(async () => {
      await Promise.resolve();
    });

    // Шлём событие через последний инстанс.
    const ws = SuccessWebSocket.instances[SuccessWebSocket.instances.length - 1]!;
    act(() => {
      ws.emit(JSON.stringify({ type: "scan_finished" }));
    });

    expect(onMessage).toHaveBeenCalledWith({ type: "scan_finished" });
  });
});
