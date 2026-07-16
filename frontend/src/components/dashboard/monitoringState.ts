import type { HealthDetails } from "@fb/shared";

export type MonitoringState = "healthy" | "paused" | "degraded" | "offline" | "unknown";

interface ResolveMonitoringStateInput {
  health: HealthDetails | null | undefined;
  healthLoading: boolean;
  healthError: boolean;
  scanOn: boolean;
}

/**
 * Единая клиентская семантика runtime-статуса.
 * UNKNOWN/OFFLINE никогда не схлопываются в зелёный ноль.
 */
export function resolveMonitoringState({
  health,
  healthLoading,
  healthError,
  scanOn,
}: ResolveMonitoringStateInput): MonitoringState {
  if (healthLoading || healthError || !health) return "unknown";

  const observerOffline = health.workers.some(
    (worker) => worker.name === "observer" && worker.status === "OFFLINE",
  );
  if (observerOffline) return "offline";
  // Money-critical (например, billing растёт при стоящей per-ad отчётности) делает
  // систему ограниченной, но не выключает живой observer и его ручные controls.
  if ((health.critical_alerts?.length ?? 0) > 0) return "degraded";
  if (health.overall === "CRITICAL") return "offline";

  const runtimeStatus = health.observer_runtime?.status;
  const allWorkersOnline =
    health.workers.length > 0 && health.workers.every((worker) => worker.status === "ONLINE");
  // Намеренная пауза при всех живых процессах не является деградацией. Проверяем
  // это до overall=DEGRADED для совместимости со старым API, где skipped Meta probe
  // ошибочно понижал overall.
  if ((runtimeStatus === "paused" || !scanOn) && allWorkersOnline) return "paused";
  if (health.overall === "DEGRADED") return "degraded";
  if (runtimeStatus === "paused" || !scanOn) return "paused";
  if (runtimeStatus !== "running") return "unknown";
  // UNKNOWN означает, что отдельный watchdog-probe ещё не выполнялся (например,
  // сразу после включения сканирования), а не отказ канала. Подтверждённый отказ
  // приходит как DEGRADED и уже учтён через health.overall выше.
  return "healthy";
}

export const MONITORING_STATE_LABEL: Record<MonitoringState, string> = {
  healthy: "LIVE",
  paused: "ПАУЗА",
  degraded: "ОГРАНИЧЕН",
  offline: "OFFLINE",
  unknown: "НЕТ ДАННЫХ",
};
