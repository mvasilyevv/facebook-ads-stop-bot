import { resolveMonitoringState } from "@/components/dashboard/monitoringState";
import { useHealthDetails, useObserverSettings, useObserverStatus } from "@/lib/api/settings";

/**
 * Единый клиентский снимок состояния monitoring-контура.
 * Все экраны читают одну и ту же семантику вместо собственных эвристик.
 */
export function useMonitoringSnapshot() {
  const healthQ = useHealthDetails();
  const observerQ = useObserverSettings();
  const observerStatusQ = useObserverStatus();

  const runtimeStatus = healthQ.data?.observer_runtime?.status;
  // Отсутствие ответа не равно «выключено»: паузу показываем только по явному флагу/runtime.
  const scanOn =
    observerQ.data?.is_scanning_enabled ??
    (runtimeStatus ? runtimeStatus === "running" : true);
  const state = resolveMonitoringState({
    health: healthQ.data,
    healthLoading: healthQ.isLoading,
    healthError: healthQ.isError,
    scanOn,
  });

  const workers = healthQ.data?.workers ?? [];
  const workersOnline = workers.filter((worker) => worker.status === "ONLINE").length;
  const lastScanAt =
    observerStatusQ.data?.last_scan_at ??
    (typeof healthQ.data?.observer_runtime?.last_successful_scan_at === "string"
      ? healthQ.data.observer_runtime.last_successful_scan_at
      : null);

  const offlineWorkers = workers
    .filter((worker) => worker.status !== "ONLINE")
    .map((worker) => worker.name);

  return {
    state,
    scanOn,
    lastScanAt,
    workersOnline,
    workersExpected: workers.length,
    offlineWorkers,
    health: healthQ.data,
    observer: observerQ.data,
    observerStatus: observerStatusQ.data,
    isLoading: healthQ.isLoading || observerQ.isLoading,
  };
}
