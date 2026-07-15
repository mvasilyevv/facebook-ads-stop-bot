/**
 * useRealtimeInvalidation — поверх useDashboardSocket: слушает WS-события browser-agent
 * и инвалидирует TanStack Query при изменениях. Делает UI динамическим — после скана
 * данные (статусы объявлений, метрики, счётчики) обновляются сразу, без ручного рефреша.
 *
 * События (ws.py форвардит): scan_finished, alert_created, task_changed, health_updated.
 * Возвращает тот же DashboardSocketState, что и useDashboardSocket (для индикатора связи).
 */

import { useCallback, useEffect } from "react";
import { useQueryClient } from "@tanstack/react-query";
import { useChatWidget, type AlertCreatedNotificationPayload } from "@/stores/chatWidget";

import { useDashboardSocket, type DashboardSocketState } from "./useDashboardSocket";

/** Период активного поллинга, когда WS недоступен (мс). */
const POLLING_INVALIDATE_MS = 15_000;

export function useRealtimeInvalidation(): DashboardSocketState {
  const qc = useQueryClient();

  const onMessage = useCallback(
    (data: unknown) => {
      const type = (data as { type?: string } | null)?.type;
      if (!type) return;
      switch (type) {
        case "scan_finished":
          // Полный цикл скана завершён — обновляем всё, что зависит от метрик/статусов.
          qc.invalidateQueries({ queryKey: ["dashboard"] });
          qc.invalidateQueries({ queryKey: ["ads"] });
          qc.invalidateQueries({ queryKey: ["observer"] });
          // Статистика залива (воронка/трекер) тоже зависит от свежих метрик скана.
          qc.invalidateQueries({ queryKey: ["stats"] });
          break;
        case "alert_created": {
          qc.invalidateQueries({ queryKey: ["dashboard"] });
          qc.invalidateQueries({ queryKey: ["ads"] });
          // Пуш в AI-виджет (плавающий чат): пометка появляется в ленте + unread-бейдж,
          // пока панель закрыта. payload — fb_agent:alert:created (alert_dispatcher.py),
          // поля-опционал (ad_name/offer_code) читаем defensively — не все каналы их шлют.
          const payload = (data as { payload?: AlertCreatedNotificationPayload } | null)
            ?.payload;
          if (payload && typeof payload.stage === "string") {
            useChatWidget.getState().pushNotification(payload);
          }
          break;
        }
        case "task_changed":
          qc.invalidateQueries({ queryKey: ["dashboard"] });
          qc.invalidateQueries({ queryKey: ["tasks"] });
          // meta_api_mutation (pause_ad/activate_ad) — тоже task_queue-задача (H-8):
          // без этого таблица /ads и AdDrawer показывали устаревший FSM-статус
          // после реального pause/activate через meta_api_worker до ручного рефреша.
          qc.invalidateQueries({ queryKey: ["ads"] });
          // campaign_create — тоже task_queue-задача (M10): без этого история
          // заливов (CampaignRunsHistory) не обновлялась live при смене статуса.
          qc.invalidateQueries({ queryKey: ["campaigns", "runs"] });
          break;
        case "health_updated":
          qc.invalidateQueries({ queryKey: ["health"] });
          break;
        default:
          break;
      }
    },
    [qc],
  );

  const state = useDashboardSocket({ onMessage });

  // Polling fallback: WS недоступен (status=polling, напр. basic-auth режет WS-рукопожатие) →
  // активно инвалидируем ключевые запросы по таймеру. Без этого дашборд «замерзает» до ручного
  // рефреша: WS — единственный источник live-инвалидации, а в polling-режиме WS-сообщений нет.
  useEffect(() => {
    if (!state.pollingFallback) return;
    const id = window.setInterval(() => {
      qc.invalidateQueries({ queryKey: ["dashboard"] });
      qc.invalidateQueries({ queryKey: ["ads"] });
      qc.invalidateQueries({ queryKey: ["observer"] });
      qc.invalidateQueries({ queryKey: ["tasks"] });
      qc.invalidateQueries({ queryKey: ["health"] });
      qc.invalidateQueries({ queryKey: ["campaigns", "runs"] });
      qc.invalidateQueries({ queryKey: ["stats"] });
    }, POLLING_INVALIDATE_MS);
    return () => window.clearInterval(id);
  }, [state.pollingFallback, qc]);

  return state;
}
