/**
 * useRealtimeInvalidation — поверх useDashboardSocket: слушает WS-события browser-agent
 * и инвалидирует TanStack Query при изменениях. Делает UI динамическим — после скана
 * данные (статусы объявлений, метрики, счётчики) обновляются сразу, без ручного рефреша.
 *
 * События (ws.py форвардит): scan_finished, alert_created, task_changed, health_updated.
 * Возвращает тот же DashboardSocketState, что и useDashboardSocket (для индикатора связи).
 */

import { useCallback } from "react";
import { useQueryClient } from "@tanstack/react-query";

import { useDashboardSocket, type DashboardSocketState } from "./useDashboardSocket";

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
          break;
        case "alert_created":
          qc.invalidateQueries({ queryKey: ["dashboard"] });
          qc.invalidateQueries({ queryKey: ["ads"] });
          break;
        case "task_changed":
          qc.invalidateQueries({ queryKey: ["dashboard"] });
          qc.invalidateQueries({ queryKey: ["tasks"] });
          break;
        default:
          break;
      }
    },
    [qc],
  );

  return useDashboardSocket({ onMessage });
}
