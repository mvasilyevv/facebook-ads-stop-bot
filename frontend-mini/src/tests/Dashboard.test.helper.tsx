/**
 * Helper для теста Dashboard — экспортирует компонент без createFileRoute-обёртки.
 * TanStack Router's createFileRoute возвращает объект, не компонент — обходим.
 */
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import type { DashboardBatch } from "@fb/shared";
import { normalizeAlertState } from "@fb/shared";
import { AlertStateBadge, KpiPlate, Button, ErrorState } from "@/components/ui";
import { MiniHeader } from "@/components/layout/MiniHeader";
import { useDashboardBatch, useTriggerScan } from "@/lib/api";

function TestDashboard() {
  const { data, isLoading, isError, error, refetch } = useDashboardBatch();
  const triggerScanMutation = useTriggerScan();

  const batch = data as DashboardBatch | undefined;
  const stats = batch?.stats;

  if (isLoading) return <div>Загрузка...</div>;
  if (isError) return <ErrorState message={(error as Error)?.message} onRetry={() => void refetch()} />;

  return (
    <div>
      <MiniHeader eyebrow="FB Stop Bot" title="Дашборд" />
      <div className="grid grid-cols-2 gap-px">
        <KpiPlate eyebrow="Всего" label="активных" value={stats?.total_ads_monitored} />
        <KpiPlate eyebrow="Стоп" label="сигналов" value={stats?.ads_in_stop} />
        <KpiPlate eyebrow="Предупреждений" label="warning" value={stats?.ads_in_warning} />
        <KpiPlate eyebrow="Отключено" label="сегодня" value={stats?.ads_in_disabled} />
      </div>

      {/* Инциденты — DashboardBatchOut.recent_incidents — [key: string]: unknown[] */}
      {batch?.recent_incidents?.map((rawInc, idx) => {
        const inc = rawInc as Record<string, unknown>;
        const fbAdId = String(inc["fb_ad_id"] ?? idx);
        const adName = inc["ad_name"] != null ? String(inc["ad_name"]) : null;
        const state = normalizeAlertState(String(inc["alert_state"] ?? "normal"));
        return (
          <div key={fbAdId}>
            <p>{adName ?? fbAdId}</p>
            <AlertStateBadge state={state} />
          </div>
        );
      })}

      <Button onClick={() => void triggerScanMutation.mutateAsync()}>Сканировать сейчас</Button>
    </div>
  );
}

const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } });

export default function DashboardWithProviders() {
  return (
    <QueryClientProvider client={qc}>
      <TestDashboard />
    </QueryClientProvider>
  );
}
