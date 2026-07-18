/**
 * useDashboardSocket — WebSocket hook с exponential backoff reconnect
 * и автоматическим polling fallback'ом при недоступности WS-соединения.
 *
 * Контракт:
 *  - На mount подключается к WS_URL (по умолчанию /ws/dashboard).
 *  - При success — слушает events, эмитит коллбэк.
 *  - При error/close — backoff: 1s → 2s → 4s → 8s → 16s, cap 30s.
 *  - После 3 неудачных reconnect'ов подряд → переход в "polling" mode:
 *    отдаёт стейт `pollingFallback=true` чтобы консьюмер мог переключиться
 *    на TanStack Query refetchInterval.
 *  - На unmount — закрывает socket и чистит таймеры.
 *
 * Backend-эндпоинт /ws/dashboard реализован (apps/api/routers/ws.py) и
 * форвардит 4 канала: scan_finished, alert_created, task_changed, health_updated.
 * Polling fallback — на случай разрыва соединения, а не отсутствия backend'а.
 */

import { useEffect, useRef, useState, useCallback } from "react";

const DEFAULT_WS_PATH = "/ws/dashboard";
const RECONNECT_DELAYS_MS = [1000, 2000, 4000, 8000, 16000, 30000];
const POLLING_THRESHOLD = 3;

export type SocketStatus = "idle" | "connecting" | "connected" | "reconnecting" | "polling";

export interface DashboardSocketOptions {
  /** Кастомный путь, если /ws/dashboard не подходит. */
  path?: string;
  /** Включить/отключить hook. */
  enabled?: boolean;
  /** Обработчик каждого входящего сообщения. */
  onMessage?: (data: unknown) => void;
}

export interface DashboardSocketState {
  status: SocketStatus;
  /** true когда статус === "polling" — UI должен переключиться на refetch. */
  pollingFallback: boolean;
  /** Счётчик попыток reconnect (для UI индикатора). */
  reconnectAttempt: number;
  /** Принудительный reconnect (например при visibilitychange). */
  forceReconnect: () => void;
}

export function useDashboardSocket(options: DashboardSocketOptions = {}): DashboardSocketState {
  const { path = DEFAULT_WS_PATH, enabled = true, onMessage } = options;
  const [status, setStatus] = useState<SocketStatus>("idle");
  const [reconnectAttempt, setReconnectAttempt] = useState(0);

  // Ref'ы держим, чтобы лишний раз не пересобирать connect через deps.
  const wsRef = useRef<WebSocket | null>(null);
  const reconnectTimerRef = useRef<number | null>(null);
  const failuresRef = useRef(0);
  const enabledRef = useRef(enabled);
  const onMessageRef = useRef(onMessage);
  const pathRef = useRef(path);

  // Синхронизируем ref'ы в effect'е (а не в render-body) — требование React 19.
  useEffect(() => {
    enabledRef.current = enabled;
    onMessageRef.current = onMessage;
    pathRef.current = path;
  }, [enabled, onMessage, path]);

  /**
   * Декларируем connect через ref, чтобы внутри handleFailure можно было
   * вызывать его без forward-reference (eslint react-hooks/refs).
   */
  const connectRef = useRef<() => void>(() => {});

  const clearReconnectTimer = useCallback(() => {
    if (reconnectTimerRef.current != null) {
      window.clearTimeout(reconnectTimerRef.current);
      reconnectTimerRef.current = null;
    }
  }, []);

  const handleFailure = useCallback(() => {
    failuresRef.current += 1;
    setReconnectAttempt(failuresRef.current);

    if (failuresRef.current >= POLLING_THRESHOLD) {
      setStatus("polling");
      return;
    }

    const delayIdx = Math.min(failuresRef.current - 1, RECONNECT_DELAYS_MS.length - 1);
    const delay = RECONNECT_DELAYS_MS[delayIdx] ?? 30000;
    setStatus("reconnecting");

    clearReconnectTimer();
    reconnectTimerRef.current = window.setTimeout(() => connectRef.current(), delay);
  }, [clearReconnectTimer]);

  const connect = useCallback(() => {
    if (!enabledRef.current) return;
    if (typeof window === "undefined") return;
    setStatus("connecting");

    const protocol = window.location.protocol === "https:" ? "wss" : "ws";
    // Browser reuses the same-origin panel session for the upgrade; Caddy validates
    // it with forward_auth and injects the server-only key upstream.
    const url = `${protocol}://${window.location.host}${pathRef.current}`;
    let ws: WebSocket;
    try {
      ws = new WebSocket(url);
    } catch {
      handleFailure();
      return;
    }

    wsRef.current = ws;

    ws.addEventListener("open", () => {
      failuresRef.current = 0;
      setReconnectAttempt(0);
      setStatus("connected");
    });

    ws.addEventListener("message", (event) => {
      try {
        const parsed = JSON.parse(event.data as string);
        onMessageRef.current?.(parsed);
      } catch {
        onMessageRef.current?.(event.data);
      }
    });

    ws.addEventListener("close", () => {
      if (!enabledRef.current) return;
      handleFailure();
    });

    ws.addEventListener("error", () => {
      // close сработает следом — ничего тут не делаем.
    });
  }, [handleFailure]);

  // Держим в ref последнюю версию connect — для timeout-callbacks.
  useEffect(() => {
    connectRef.current = connect;
  }, [connect]);

  const forceReconnect = useCallback(() => {
    clearReconnectTimer();
    if (wsRef.current) {
      wsRef.current.close();
      wsRef.current = null;
    }
    failuresRef.current = 0;
    setReconnectAttempt(0);
    connect();
  }, [connect, clearReconnectTimer]);

  useEffect(() => {
    if (!enabled) {
      return () => {
        clearReconnectTimer();
      };
    }
    connect();
    return () => {
      clearReconnectTimer();
      if (wsRef.current) {
        wsRef.current.close();
        wsRef.current = null;
      }
    };
  }, [enabled, connect, clearReconnectTimer]);

  // При отключении hook'а сбрасываем статус в idle.
  useEffect(() => {
    if (!enabled && status !== "idle") {
      setStatus("idle");
    }
  }, [enabled, status]);

  return {
    status,
    pollingFallback: status === "polling",
    reconnectAttempt,
    forceReconnect,
  };
}
