import { useMemo } from "react";
import { createFileRoute, useRouter } from "@tanstack/react-router";
import { ArrowRight, Bot, CheckCircle2, CircleOff, ShieldAlert } from "lucide-react";

import { Eyebrow } from "@/components/data/Eyebrow";
import { Card } from "@/components/ui/Card";
import { Button } from "@/components/ui/Button";
import { ErrorState } from "@/components/ui/ErrorState";
import { CriticalHealthBanner } from "@/components/dashboard/CriticalHealthBanner";
import { ScanBlockedBanner } from "@/components/dashboard/ScanBlockedBanner";
import { ScanCluster, type ScanProgress } from "@/components/dashboard/ScanCluster";
import { LiveTail } from "@/components/dashboard/LiveTail";
import { TaskQueues } from "@/components/dashboard/TaskQueues";
import { BudgetLineChart } from "@/components/analytics/BudgetLineChart";
import { FunnelChart } from "@/components/analytics/FunnelChart";
import {
  MONITORING_STATE_LABEL,
  type MonitoringState,
} from "@/components/dashboard/monitoringState";
import {
  useConfirmEnableRecommendation,
  useDashboardBatch,
  useEnableRecommendations,
} from "@/lib/api/dashboard";
import { useDisableTasks, useEnableTasks } from "@/lib/api/ads";
import { useAnalyticsLiveBudget, useAnalyticsPerformance } from "@/lib/api/analytics";
import { useToggleScanning } from "@/lib/api/settings";
import { useMonitoringSnapshot } from "@/lib/hooks/useMonitoringSnapshot";
import { useRealtimeInvalidation } from "@/lib/websocket/useRealtimeInvalidation";
import { apiSend } from "@/lib/api/client";
import { formatDisplayDateTime } from "@/lib/timezone";
import { analyticsRouteSearch } from "@/lib/analyticsSearch";
import type { TaskQueueRow } from "@fb/shared";

export const Route = createFileRoute("/")({ component: DashboardPage });

function DashboardPage() {
  const router = useRouter();
  useRealtimeInvalidation();
  const batchQ = useDashboardBatch();
  const monitoring = useMonitoringSnapshot();
  const toggleScanning = useToggleScanning();
  const performanceQ = useAnalyticsPerformance({
    period: "today",
    level: "campaign",
    sort: "base_delta",
    direction: "desc",
    page: 1,
    page_size: 50,
  });
  const budgetQ = useAnalyticsLiveBudget({});
  const recommendationsQ = useEnableRecommendations("PENDING");
  const confirmRecommendation = useConfirmEnableRecommendation();
  const disableTasksQ = useDisableTasks({ status: "PENDING,RUNNING,RETRYING", limit: 20 });
  const enableTasksQ = useEnableTasks({ status: "PENDING,RUNNING,RETRYING", limit: 20 });

  const stats = batchQ.data?.stats;
  const totals = performanceQ.data?.totals;
  const totalBudget = performanceQ.data?.total_live_budget;
  const recommendations = recommendationsQ.data ?? [];
  const warningRecommendations = recommendations.filter(
    (item) => (item.recommendation_level ?? item.reason)?.toLowerCase() === "warning",
  );
  const taskErrors = [
    ...(batchQ.data?.recent_disable_tasks ?? []),
    ...(batchQ.data?.recent_enable_tasks ?? []),
  ].filter((task) => task.status === "FAILED");
  const autoActions = (batchQ.data?.recent_enable_tasks ?? []).filter(
    (task) => task.requested_by === "auto_enable_recommendation_worker",
  );
  const attentionCount =
    (stats?.ads_in_stop ?? 0) +
    (stats?.ads_in_warning ?? 0) +
    taskErrors.length +
    warningRecommendations.length;

  const scanProgress = useMemo<ScanProgress | null>(() => {
    const extra = (monitoring.observerStatus?.extra ?? {}) as Record<string, unknown>;
    const total = typeof extra.accounts_total === "number" ? extra.accounts_total : null;
    if (!total || total < 1) return null;
    return {
      total,
      done: typeof extra.accounts_done === "number" ? extra.accounts_done : null,
      current: typeof extra.current_account_id === "string" ? extra.current_account_id : null,
    };
  }, [monitoring.observerStatus]);
  const runtimeExtra = (monitoring.observerStatus?.extra ?? {}) as Record<string, unknown>;
  const nextScanAt =
    typeof runtimeExtra.next_scan_at === "string" ? runtimeExtra.next_scan_at : null;
  const scanMode = typeof runtimeExtra.scan_mode === "string" ? runtimeExtra.scan_mode : null;
  const runtimeReason = monitoringReason(
    monitoring.state,
    stats?.scan_blocked_reason,
    monitoring.offlineWorkers,
    monitoring.observerStatus?.last_scan_outcome,
  );

  if (batchQ.isError && !batchQ.data) {
    return (
      <ErrorState
        title="Не удалось загрузить операторский обзор."
        error={batchQ.error}
        onRetry={() => void batchQ.refetch()}
      />
    );
  }

  const scanNow = () => {
    void apiSend("POST", "/settings/observer/scan-now").catch(() => undefined);
  };

  return (
    <div className="min-w-0" aria-label="Dashboard">
      <div className="mb-5 flex flex-col items-start justify-between gap-5 lg:flex-row">
        <div>
          <Eyebrow num="01">ОБЗОР · {MONITORING_STATE_LABEL[monitoring.state]}</Eyebrow>
          <h1 className="m-0 mt-2 font-display text-[30px] font-medium tracking-[-0.02em] text-bg-11">
            Что требует внимания
          </h1>
        </div>
        <ScanCluster
          scanOn={monitoring.scanOn}
          monitoringState={monitoring.state}
          lastScanAt={stats?.last_scan_at ?? monitoring.lastScanAt}
          nextScanAt={nextScanAt}
          scanMode={scanMode}
          intervalSeconds={monitoring.observer?.default_interval_seconds ?? 30}
          scanProgress={scanProgress}
          onScan={scanNow}
          onDisable={() => toggleScanning.mutate(false)}
        />
      </div>

      <CriticalHealthBanner alerts={monitoring.health?.critical_alerts ?? []} />
      {monitoring.scanOn && stats?.scan_blocked_reason ? (
        <div className="mb-4">
          <ScanBlockedBanner
            reason={stats.scan_blocked_reason}
            onNavigate={() => void router.navigate({ to: "/campaigns" })}
          />
        </div>
      ) : null}

      <StatusStrip
        state={monitoring.state}
        reason={runtimeReason}
        nextScanAt={nextScanAt}
        normal={stats?.ads_in_normal ?? 0}
        warning={stats?.ads_in_warning ?? 0}
        stop={stats?.ads_in_stop ?? 0}
        disabled={stats?.ads_in_disabled ?? 0}
        autoEnabled={monitoring.observer?.auto_enable_recommendations ?? false}
        autoActions={autoActions.length}
      />

      <div className="mb-5 grid gap-5 xl:grid-cols-[minmax(0,1.35fr)_minmax(360px,0.65fr)]">
        <Card padded className="min-w-0 p-5">
          <div className="mb-1 flex items-start justify-between gap-4">
            <div>
              <Eyebrow>ЭКОНОМИКА СЕГОДНЯ</Eyebrow>
              <div className="mt-2 flex flex-wrap items-baseline gap-x-5 gap-y-1">
                <MoneyStat label="Факт" value={totals?.spend} />
                <MoneyStat label="База" value={totalBudget?.base_budget} />
                <MoneyStat label="Stop" value={totalBudget?.stop_budget} />
                <MoneyStat
                  label="Δ базы"
                  value={totalBudget?.base_delta}
                  signed
                  critical={Number(totalBudget?.base_delta ?? 0) > 0}
                />
              </div>
            </div>
            <Button
              variant="ghost"
              size="sm"
              rightIcon={<ArrowRight size={13} />}
              onClick={() =>
                void router.navigate({ to: "/analytics", search: analyticsRouteSearch() })
              }
            >
              Аналитика
            </Button>
          </div>
          <BudgetLineChart data={budgetQ.data} loading={budgetQ.isLoading} height={220} />
        </Card>

        <Card padded className="p-5">
          <div className="mb-4 flex items-start justify-between">
            <div>
              <Eyebrow>РИСКИ И РЕШЕНИЯ</Eyebrow>
              <div className="mt-2 font-display text-[26px] tabular-nums text-bg-11">
                {attentionCount}
              </div>
            </div>
            {attentionCount ? (
              <ShieldAlert size={22} className="text-warning" />
            ) : (
              <CheckCircle2 size={22} className="text-success" />
            )}
          </div>
          <div className="grid grid-cols-2 gap-px overflow-hidden rounded-[var(--radius-2)] border border-[var(--hairline)] bg-[var(--hairline)]">
            <AttentionCell label="STOP" value={stats?.ads_in_stop ?? 0} tone="danger" />
            <AttentionCell label="WARNING" value={stats?.ads_in_warning ?? 0} tone="warning" />
            <AttentionCell
              label="Ошибки задач"
              value={taskErrors.length}
              tone={taskErrors.length ? "danger" : "neutral"}
            />
            <AttentionCell
              label="Ручные решения"
              value={warningRecommendations.length}
              tone={warningRecommendations.length ? "warning" : "neutral"}
            />
          </div>
          <div className="mt-5">
            <Eyebrow>ВОРОНКА META → TRACKER</Eyebrow>
            <div className="mt-3">
              <FunnelChart
                clicks={totals?.clicks ?? 0}
                registrations={totals?.registrations ?? 0}
                ftds={totals?.ftds ?? 0}
                confirmedDeposits={totals?.confirmed_deposits ?? 0}
              />
            </div>
          </div>
        </Card>
      </div>

      <div className="mb-5 grid gap-5 xl:grid-cols-[minmax(0,1fr)_minmax(360px,0.72fr)]">
        <Card padded={false}>
          <div className="border-b border-[var(--hairline)] px-5 py-4">
            <Eyebrow>ТОП ОТКЛОНЕНИЙ · КАМПАНИИ</Eyebrow>
          </div>
          <CampaignDeltaList
            rows={performanceQ.data?.rows.slice(0, 5) ?? []}
            loading={performanceQ.isLoading}
            onOpen={(campaignId) =>
              void router.navigate({
                to: "/analytics",
                search: analyticsRouteSearch({ campaign_id: campaignId }),
              })
            }
          />
        </Card>
        <RecommendationPanel
          recommendations={warningRecommendations.slice(0, 5)}
          autoActions={autoActions.slice(0, 3)}
          loading={recommendationsQ.isLoading}
          pendingId={confirmRecommendation.isPending ? confirmRecommendation.variables : undefined}
          onConfirm={(id) => confirmRecommendation.mutate(id)}
        />
      </div>

      <details
        className="mb-4 rounded-[var(--radius-3)] border border-[var(--hairline)] bg-bg-1"
        open={attentionCount > 0}
      >
        <summary className="cursor-pointer px-5 py-4 font-display text-[11px] uppercase tracking-[0.08em] text-bg-9">
          События и очереди {attentionCount > 0 ? `· ${attentionCount} требуют внимания` : "· тихо"}
        </summary>
        <div className="grid gap-5 border-t border-[var(--hairline)] p-5 xl:grid-cols-2">
          <Card padded={false}>
            <LiveTail
              events={batchQ.data?.recent_alerts ?? []}
              max={8}
              frozen={monitoring.state !== "healthy"}
              monitoringState={monitoring.state}
              onRow={(event) =>
                event.fb_ad_id &&
                void router.navigate({ to: "/ads/$fbAdId", params: { fbAdId: event.fb_ad_id } })
              }
            />
          </Card>
          <TaskQueues
            disableTasks={disableTasksQ.data ?? []}
            enableTasks={enableTasksQ.data ?? []}
            disableLoading={disableTasksQ.isLoading}
            enableLoading={enableTasksQ.isLoading}
            disableError={disableTasksQ.isError}
            enableError={enableTasksQ.isError}
            onRetryDisable={() => void disableTasksQ.refetch()}
            onRetryEnable={() => void enableTasksQ.refetch()}
          />
        </div>
      </details>
    </div>
  );
}

function StatusStrip({
  state,
  reason,
  nextScanAt,
  normal,
  warning,
  stop,
  disabled,
  autoEnabled,
  autoActions,
}: {
  state: MonitoringState;
  reason: string;
  nextScanAt: string | null;
  normal: number;
  warning: number;
  stop: number;
  disabled: number;
  autoEnabled: boolean;
  autoActions: number;
}) {
  const stateColor =
    state === "healthy" ? "bg-success" : state === "offline" ? "bg-danger" : "bg-warning";
  return (
    <div className="mb-5 grid gap-px overflow-hidden rounded-[var(--radius-3)] border border-[var(--hairline)] bg-[var(--hairline)] lg:grid-cols-[minmax(260px,1.6fr)_repeat(4,minmax(90px,0.45fr))_minmax(190px,0.8fr)]">
      <div className="bg-bg-1 px-4 py-3">
        <div className="flex items-center gap-2 text-[11px] text-bg-11">
          <span className={`size-2 rounded-full ${stateColor}`} />
          {MONITORING_STATE_LABEL[state]}
        </div>
        <div className="mt-1 truncate text-[10px] text-bg-7" title={reason}>
          {reason}
          {nextScanAt ? ` · следующий ${formatDisplayDateTime(nextScanAt)}` : ""}
        </div>
      </div>
      <MiniState label="Норма" value={normal} />
      <MiniState label="Warning" value={warning} />
      <MiniState label="Stop" value={stop} />
      <MiniState label="Off" value={disabled} />
      <div className="flex items-center justify-between gap-3 bg-bg-1 px-4 py-3">
        <div>
          <div className="font-display text-[9px] uppercase tracking-[0.08em] text-bg-7">
            Auto-enable
          </div>
          <div className="mt-1 text-[11px] text-bg-10">
            {autoEnabled ? "OK исполняются" : "Только вручную"}
          </div>
        </div>
        <div className="text-right font-display text-[16px] tabular-nums text-bg-11">
          {autoActions}
          <small className="ml-1 text-[9px] text-bg-7">recent</small>
        </div>
      </div>
    </div>
  );
}

function MiniState({ label, value }: { label: string; value: number }) {
  return (
    <div className="bg-bg-1 px-3 py-3 text-center">
      <div className="font-display text-[17px] tabular-nums text-bg-11">{value}</div>
      <div className="text-[9px] uppercase tracking-[0.06em] text-bg-7">{label}</div>
    </div>
  );
}
function MoneyStat({
  label,
  value,
  signed = false,
  critical = false,
}: {
  label: string;
  value?: string | null;
  signed?: boolean;
  critical?: boolean;
}) {
  const parsed = value == null ? null : Number(value);
  return (
    <div>
      <div className="font-display text-[9px] uppercase tracking-[0.08em] text-bg-7">{label}</div>
      <div
        className={`font-display text-[18px] tabular-nums ${critical ? "text-danger" : "text-bg-11"}`}
      >
        {parsed == null || Number.isNaN(parsed)
          ? "—"
          : `${signed && parsed > 0 ? "+" : ""}$${parsed.toFixed(2)}`}
      </div>
    </div>
  );
}
function AttentionCell({
  label,
  value,
  tone,
}: {
  label: string;
  value: number;
  tone: "danger" | "warning" | "neutral";
}) {
  return (
    <div className="flex items-center justify-between bg-bg-1 px-3 py-2.5">
      <span className="text-[10px] text-bg-8">{label}</span>
      <strong
        className={`font-display text-[14px] tabular-nums ${tone === "danger" ? "text-danger" : tone === "warning" ? "text-warning" : "text-bg-10"}`}
      >
        {value}
      </strong>
    </div>
  );
}

function CampaignDeltaList({
  rows,
  loading,
  onOpen,
}: {
  rows: NonNullable<ReturnType<typeof useAnalyticsPerformance>["data"]>["rows"];
  loading: boolean;
  onOpen: (id: string) => void;
}) {
  if (loading && !rows.length)
    return <div className="px-5 py-8 text-[11px] text-bg-7">Считаем отклонения…</div>;
  if (!rows.length)
    return <div className="px-5 py-8 text-[11px] text-bg-7">Нет кампаний с расходом сегодня</div>;
  return (
    <div>
      {rows.map((row) => {
        const delta = Number(row.live_budget?.base_delta ?? 0);
        const base = Math.max(Number(row.live_budget?.base_budget ?? 0), 0.01);
        const width = Math.min(100, Math.abs(delta / base) * 100);
        return (
          <button
            key={row.id}
            type="button"
            onClick={() => onOpen(row.id)}
            className="grid w-full grid-cols-[minmax(0,1fr)_130px_90px] items-center gap-4 border-b border-[var(--hairline)] px-5 py-3 text-left last:border-0 hover:bg-bg-2"
          >
            <div className="min-w-0">
              <div className="truncate text-[12px] text-bg-11">{row.name}</div>
              <div className="mt-0.5 text-[9px] text-bg-7">{row.offer_code ?? "без оффера"}</div>
            </div>
            <div className="h-1.5 overflow-hidden rounded-full bg-bg-3">
              <div
                className={`h-full rounded-full ${delta > 0 ? "bg-danger" : "bg-success"}`}
                style={{ width: `${width}%` }}
              />
            </div>
            <div
              className={`text-right font-display text-[12px] tabular-nums ${delta > 0 ? "text-danger" : "text-success"}`}
            >
              {delta > 0 ? "+" : ""}${delta.toFixed(2)}
            </div>
          </button>
        );
      })}
    </div>
  );
}

function RecommendationPanel({
  recommendations,
  autoActions,
  loading,
  pendingId,
  onConfirm,
}: {
  recommendations: NonNullable<ReturnType<typeof useEnableRecommendations>["data"]>;
  autoActions: TaskQueueRow[];
  loading: boolean;
  pendingId?: string;
  onConfirm: (id: string) => void;
}) {
  return (
    <Card padded={false}>
      <div className="flex items-center justify-between border-b border-[var(--hairline)] px-5 py-4">
        <Eyebrow>РУЧНЫЕ ENABLE-РЕШЕНИЯ</Eyebrow>
        <Bot size={15} className="text-bg-7" />
      </div>
      {loading ? (
        <div className="px-5 py-8 text-[11px] text-bg-7">Проверяем рекомендации…</div>
      ) : recommendations.length ? (
        <div>
          {recommendations.map((item) => (
            <div
              key={item.id}
              className="border-b border-[var(--hairline)] px-5 py-3 last:border-0"
            >
              <div className="flex items-start justify-between gap-3">
                <div className="min-w-0">
                  <div className="truncate text-[12px] text-bg-11">
                    {item.ad_name ?? item.fb_ad_id ?? "Объявление"}
                  </div>
                  <div className="mt-1 line-clamp-2 text-[10px] leading-4 text-bg-8">
                    {recommendationReason(item.metrics_payload)}
                  </div>
                </div>
                <Button
                  size="sm"
                  variant="secondary"
                  loading={pendingId === item.id}
                  onClick={() => onConfirm(item.id)}
                >
                  Включить
                </Button>
              </div>
            </div>
          ))}
        </div>
      ) : (
        <div className="flex items-center gap-2 px-5 py-8 text-[11px] text-bg-7">
          <CircleOff size={14} />
          Нет WARNING-рекомендаций
        </div>
      )}
      {autoActions.length ? (
        <div className="border-t border-[var(--hairline)]">
          <div className="px-5 pb-1 pt-3 font-display text-[9px] uppercase tracking-[0.08em] text-bg-7">
            Последние автоматические включения
          </div>
          {autoActions.map((task) => (
            <div
              key={task.id}
              className="flex items-center justify-between gap-3 px-5 py-2 text-[10px]"
            >
              <span className="min-w-0 truncate text-bg-10">
                {task.ad_name ?? task.fb_ad_id ?? "Объявление"}
              </span>
              <span className="shrink-0 font-display text-bg-7">
                {task.status} · {formatDisplayDateTime(task.created_at)}
              </span>
            </div>
          ))}
        </div>
      ) : null}
    </Card>
  );
}

function recommendationReason(payload?: Record<string, unknown> | null): string {
  const codes = Array.isArray(payload?.canonical_rule_codes)
    ? payload.canonical_rule_codes.join(", ")
    : null;
  return codes
    ? `Остались пограничные правила: ${codes}`
    : "Недостаточно уверенности для auto-enable — требуется решение оператора.";
}
function monitoringReason(
  state: MonitoringState,
  blocked: string | null | undefined,
  offlineWorkers: string[],
  lastOutcome?: string | null,
): string {
  if (blocked) return blocked;
  if (offlineWorkers.length) return `Недоступны: ${offlineWorkers.join(", ")}`;
  if (state === "paused") return "Сканирование выключено оператором";
  if (state === "unknown") return "Ожидаем первый подтвержденный скан";
  if (lastOutcome && lastOutcome !== "success") return `Последний скан: ${lastOutcome}`;
  return "Контур мониторинга работает штатно";
}
