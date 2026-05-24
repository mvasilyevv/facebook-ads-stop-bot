/**
 * useWebSocket — хук для WebSocket-соединения с автореконнектом.
 *
 * @param {string} url - URL для подключения (ws:// или wss://)
 * @param {object} options
 * @param {boolean} [options.enabled=true] - включён ли WebSocket
 * @param {boolean} [options.autoReconnect=true] - автореконнект при обрыве
 * @param {function} [options.onMessage] - колбэк при получении сообщения (event: object) => void
 * @returns {{ connected: boolean, lastMessage: object|null, sendMessage: function }}
 */

import { useCallback, useEffect, useRef, useState } from 'react';

// Параметры реконнекта: экспоненциальный backoff от 1 сек до 30 сек
const RECONNECT_BASE_MS = 1_000;
const RECONNECT_MAX_MS = 30_000;

export function useWebSocket(url, { enabled = true, autoReconnect = true, onMessage } = {}) {
  const [connected, setConnected] = useState(false);
  const [lastMessage, setLastMessage] = useState(null);

  const wsRef = useRef(null);
  const reconnectTimeoutRef = useRef(null);
  const reconnectAttemptRef = useRef(0);
  const enabledRef = useRef(enabled);
  const onMessageRef = useRef(onMessage);
  const disposedRef = useRef(false);

  // Обновляем ref при изменении enabled и onMessage, чтобы не перепривязывать эффект
  useEffect(() => { enabledRef.current = enabled; }, [enabled]);
  useEffect(() => { onMessageRef.current = onMessage; }, [onMessage]);

  const disconnect = useCallback(() => {
    if (reconnectTimeoutRef.current !== null) {
      clearTimeout(reconnectTimeoutRef.current);
      reconnectTimeoutRef.current = null;
    }
    if (wsRef.current) {
      // Помечаем как намеренное закрытие: reconnect не нужен
      wsRef.current._intentional = true;
      wsRef.current.close();
      wsRef.current = null;
    }
    setConnected(false);
  }, []);

  const connect = useCallback(() => {
    // Убеждаемся, что предыдущее соединение закрыто
    if (wsRef.current && wsRef.current.readyState !== WebSocket.CLOSED) {
      wsRef.current._intentional = true;
      wsRef.current.close();
    }

    let ws;
    try {
      ws = new WebSocket(url);
    } catch (e) {
      return;
    }
    wsRef.current = ws;

    ws.onopen = () => {
      if (disposedRef.current) {
        ws.close();
        return;
      }
      reconnectAttemptRef.current = 0;
      setConnected(true);
    };

    ws.onmessage = (event) => {
      let data;
      try {
        data = JSON.parse(event.data);
      } catch {
        return;
      }
      // Ping-фреймы игнорируем
      if (data?.type === 'ping') return;

      setLastMessage(data);
      if (onMessageRef.current) {
        onMessageRef.current(data);
      }
    };

    ws.onclose = () => {
      setConnected(false);
      if (disposedRef.current || ws._intentional) return;
      if (!autoReconnect || !enabledRef.current) return;

      // Экспоненциальный backoff
      const attempt = reconnectAttemptRef.current;
      const delay = Math.min(RECONNECT_BASE_MS * 2 ** attempt, RECONNECT_MAX_MS);
      reconnectAttemptRef.current = attempt + 1;

      reconnectTimeoutRef.current = setTimeout(() => {
        if (!disposedRef.current && enabledRef.current) {
          connect();
        }
      }, delay);
    };

    ws.onerror = () => {
      // Ошибки обрабатываем через onclose (всегда следует за onerror)
    };
  }, [url, autoReconnect]);

  useEffect(() => {
    disposedRef.current = false;

    if (!url || !enabled) {
      disconnect();
      return;
    }

    connect();

    return () => {
      disposedRef.current = true;
      disconnect();
    };
    // connect включает url в deps через useCallback
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [url, enabled]);

  const sendMessage = useCallback((data) => {
    if (wsRef.current && wsRef.current.readyState === WebSocket.OPEN) {
      wsRef.current.send(typeof data === 'string' ? data : JSON.stringify(data));
    }
  }, []);

  return { connected, lastMessage, sendMessage };
}
