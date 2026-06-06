/**
 * Dashboard — главный экран Mini App.
 * KPI-плитки 2×2, активные сигналы с инлайн-действием, очередь задач, scan-now.
 * Данные: useDashboardBatch (батч-запрос stats + incidents + disable-tasks).
 */
import { createFileRoute, useNavigate } from "@tanstack/react-router";
import { useState } from "react";
import {
  normalizeAlertState,
  normalizeTaskStatus,
  formatRelativeTime,
} from "@fb/shared";
import type { DashboardBatch } from "@fb/shared";
import {
  useDashboardBatch,
  useToggleScanning,
  useTriggerScan,
  useTmaDisable,
  useObserverSettings,
} from "@/lib/api";
import { haptic, tgConfirm, tgAlert } from "@/lib/tg";
import { MiniHeader } from "@/components/layout/MiniHeader";
import {
  KpiPlate,
  AlertStateBadge,
  TaskStatusBadge,
  Button,
  Skeleton,
  ErrorState,
  EmptyState,
} from "@/components/ui";
import { cn } from "@/lib/cn";

export const Route = createFileRoute("/")({
  component: DashboardPage,
});

// ─── Вспомогательные функции ───────────────────────────────────────────────

/** Вариант для KPI плитки по количеству */
function kpiVariant(value: number | null | undefined, warnThreshold = 1): "default" | "warn" | "stop" {
  if (!value) return "default";
  if (value >= warnThreshold * 3) return "stop";
  if (value >= warnThreshold) return "warn";
  return "default";
}

// ─── Компонент ────────────────────────────────────────────────────────────

function DashboardPage() {
  const navigate = useNavigate();
  const { data, isLoading, isError, error, refetch } = useDashboardBatch({ refetchInterval: 20_000 });
  const { data: obsSettings } = useObserverSettings();
  const toggleScanMutation = useToggleScanning();
  const triggerScanMutation = useTriggerScan();
  const disableMutation = useTmaDisable();

  const [toastMsg, setToastMsg] = useState<{ text: string; ok: boolean } | null>(null);

  function showToast(text: string, ok = true) {
    setToastMsg({ text, ok });
    setTimeout(() => setToastMsg(null), 3000);
  }

  // ─── Scan-now ──────────────────────────────────────────────────────────
  const handleScanNow = async () => {
    haptic.impact("medium");
    try {
      await triggerScanMutation.mutateAsync();
      haptic.notify("success");
      showToast("Сканирование запущено");
      // Обновим данные через 3 секунды
      setTimeout(() => void refetch(), 3_000);
    } catch (e: unknown) {
      haptic.notify("error");
      showToast((e as Error).message ?? "Ошибка", false);
    }
  };

  // ─── Toggle scanning ──────────────────────────────────────────────────
  const handleToggleScanning = async () => {
    const enabled = !(obsSettings?.is_scanning_enabled ?? true);
    haptic.impact("medium");
    try {
      await toggleScanMutation.mutateAsync({ enabled });
      haptic.notify("success");
    } catch (e: unknown) {
      haptic.notify("error");
      showToast((e as Error).message ?? "Ошибка", false);
    }
  };

  // ─── Inline disable ──────────────────────────────────────────────────
  const handleDisable = async (fbAdId: string, adName: string | null) => {
    haptic.impact("heavy");
    const confirmed = await tgConfirm(`Отключить объявление "${adName ?? fbAdId}"?`);
    if (!confirmed) return;
    try {
      await disableMutation.mutateAsync({ fbAdId, reason: "Ручное из Mini App" });
      haptic.notify("success");
      await tgAlert(`Задача на отключение "${adName ?? fbAdId}" создана`);
    } catch (e: unknown) {
      haptic.notify("error");
      await tgAlert(`Ошибка: ${(e as Error).message}`);
    }
  };

  // ─── Render ──────────────────────────────────────────────────────────
  const scanning = obsSettings?.is_scanning_enabled ?? true;
  const batch = data as DashboardBatch | undefined;
  const stats = batch?.stats;

  return (
    <div className="flex flex-col gap-0">
      <MiniHeader
        eyebrow="FB Stop Bot"
        title="Дашборд"
        right={
          stats?.last_scan_at ? (
            <span className="text-[11px] text-[var(--color-bg-9)] font-mono">
              {formatRelativeTime(stats.last_scan_at)} назад
            </span>
          ) : undefined
        }
      />

      {/* ── Ошибка ── */}
      {isError && (
        <div className="px-4 pt-4">
          <ErrorState
            message={(error as Error)?.message ?? "Ошибка загрузки"}
            onRetry={() => void refetch()}
          />
        </div>
      )}

      {/* ── Контент ── */}
      <div className="flex flex-col gap-px">

        {/* KPI 2×2 */}
        <section aria-label="Статистика" className="grid grid-cols-2 gap-px bg-[var(--color-bg-5)]">
          {isLoading ? (
            <>
              {[...Array(4)].map((_, i) => (
                <div key={i} className="bg-[var(--color-bg-1)] p-3 space-y-2">
                  <Skeleton className="h-3 w-20" />
                  <Skeleton className="h-7 w-12" />
                  <Skeleton className="h-3 w-16" />
                </div>
              ))}
            </>
          ) : (
            <>
              <KpiPlate
                eyebrow="Всего"
                label="активных"
                value={stats?.total_ads_monitored}
                variant="default"
              />
              <KpiPlate
                eyebrow="Стоп"
                label="сигналов"
                value={stats?.ads_in_stop}
                variant={kpiVariant(stats?.ads_in_stop)}
              />
              <KpiPlate
                eyebrow="Предупреждений"
                label="warning"
                value={stats?.ads_in_warning}
                variant={kpiVariant(stats?.ads_in_warning)}
              />
              <KpiPlate
                eyebrow="Отключено"
                label="сегодня"
                value={stats?.ads_in_disabled}
                variant="ok"
              />
            </>
          )}
        </section>

        {/* Активные сигналы */}
        <section aria-label="Активные сигналы" className="bg-[var(--color-bg-0)]">
          <div className="px-4 py-3 border-b border-[var(--color-bg-5)] flex items-center justify-between">
            <p className="text-[11px] uppercase tracking-[0.08em] text-[var(--color-bg-9)] font-mono">
              Активные сигналы
            </p>
            {batch?.recent_incidents && batch.recent_incidents.length > 0 && (
              <span className="inline-flex items-center font-mono text-[11px] font-medium px-[6px] py-[3px] bg-[var(--color-danger-bg)] text-[var(--color-danger)]">
                {batch.recent_incidents.length}
              </span>
            )}
          </div>

          {isLoading ? (
            <div className="p-4 space-y-3">
              {[...Array(2)].map((_, i) => <Skeleton key={i} className="h-16 w-full" />)}
            </div>
          ) : !batch?.recent_incidents?.length ? (
            <EmptyState
              title="Нет активных сигналов"
              description="Всё в норме"
            />
          ) : (
            <div className="divide-y divide-[var(--color-bg-5)]">
              {batch.recent_incidents.slice(0, 6).map((rawInc) => {
                const inc = rawInc as Record<string, unknown>;
                const fbAdId = String(inc["fb_ad_id"] ?? "");
                const adName = inc["ad_name"] != null ? String(inc["ad_name"]) : null;
                const state = normalizeAlertState(String(inc["alert_state"] ?? "normal"));
                const codes = [
                  ...((inc["stop_rule_codes"] as string[]) ?? []),
                  ...((inc["warning_rule_codes"] as string[]) ?? []),
                ];
                return (
                  <div key={fbAdId} className="px-4 py-3 flex flex-col gap-2">
                    {/* Верхняя строка: имя + badge */}
                    <button
                      type="button"
                      className="flex items-start justify-between gap-2 w-full text-left min-h-[44px]"
                      onClick={() => {
                        haptic.selection();
                        void navigate({ to: "/ads/$fbAdId", params: { fbAdId } });
                      }}
                    >
                      <div className="flex-1 min-w-0">
                        <p className="text-[13px] font-medium text-[var(--color-bg-11)] truncate leading-tight">
                          {adName ?? fbAdId}
                        </p>
                        {codes.length > 0 && (
                          <p className="text-[11px] text-[var(--color-bg-9)] font-mono mt-0.5">
                            {codes.slice(0, 3).join(" · ")}
                          </p>
                        )}
                      </div>
                      <AlertStateBadge state={state} />
                    </button>
                    {/* Inline кнопка отключения */}
                    {state !== "disabled" && (
                      <Button
                        variant="danger"
                        size="sm"
                        loading={disableMutation.isPending}
                        onClick={() => void handleDisable(fbAdId, adName)}
                        className="self-end"
                      >
                        Отключить
                      </Button>
                    )}
                  </div>
                );
              })}
            </div>
          )}
        </section>

        {/* Очередь задач */}
        <section aria-label="Очередь задач" className="bg-[var(--color-bg-0)]">
          <div className="px-4 py-3 border-b border-[var(--color-bg-5)]">
            <p className="text-[11px] uppercase tracking-[0.08em] text-[var(--color-bg-9)] font-mono">
              Очередь задач
            </p>
          </div>

          {isLoading ? (
            <div className="p-4 space-y-2">
              {[...Array(2)].map((_, i) => <Skeleton key={i} className="h-10 w-full" />)}
            </div>
          ) : !batch?.recent_disable_tasks?.length ? (
            <div className="px-4 py-4">
              <p className="text-[12px] text-[var(--color-bg-9)]">Очередь пуста</p>
            </div>
          ) : (
            <div className="divide-y divide-[var(--color-bg-5)]">
              {batch.recent_disable_tasks
                .filter((rawT) => {
                  const t = rawT as Record<string, unknown>;
                  return ["PENDING", "RUNNING", "RETRYING", "FAILED"].includes(
                    normalizeTaskStatus(String(t["status"] ?? ""))
                  );
                })
                .slice(0, 5)
                .map((rawTask) => {
                  const task = rawTask as Record<string, unknown>;
                  const status = normalizeTaskStatus(String(task["status"] ?? ""));
                  const taskId = String(task["id"] ?? "");
                  return (
                    <div key={taskId} className="px-4 py-3 flex items-center justify-between gap-2 min-h-[44px]">
                      <div className="min-w-0">
                        <p className="text-[13px] text-[var(--color-bg-11)] truncate">
                          {String(task["ad_name"] ?? task["fb_ad_id"] ?? taskId)}
                        </p>
                        {task["last_error"] != null && (
                          <p className="text-[11px] text-[var(--color-danger)] font-mono mt-0.5 truncate">
                            {String(task["last_error"])}
                          </p>
                        )}
                      </div>
                      <TaskStatusBadge status={status} />
                    </div>
                  );
                })}
            </div>
          )}
        </section>

        {/* Управление сканированием */}
        <section aria-label="Сканирование" className="bg-[var(--color-bg-1)] border-t border-[var(--color-bg-5)] px-4 py-4">
          <p className="text-[11px] uppercase tracking-[0.08em] text-[var(--color-bg-9)] font-mono mb-3">
            Сканирование
          </p>
          <div className="flex items-center gap-3 flex-wrap">
            <Button
              variant="secondary"
              size="sm"
              loading={toggleScanMutation.isPending}
              onClick={() => void handleToggleScanning()}
            >
              {scanning ? "Приостановить" : "Возобновить"}
            </Button>
            <Button
              variant="secondary"
              size="sm"
              loading={triggerScanMutation.isPending}
              onClick={() => void handleScanNow()}
            >
              Сканировать сейчас
            </Button>
            <Button
              variant="ghost"
              size="sm"
              onClick={() => void refetch()}
            >
              Обновить
            </Button>
          </div>
          <p className={cn(
            "mt-2 text-[12px] font-mono",
            scanning ? "text-[var(--color-success)]" : "text-[var(--color-warning)]",
          )}>
            {scanning ? "● активно" : "● приостановлено"}
          </p>
        </section>

      </div>

      {/* Toast */}
      {toastMsg && (
        <div
          role="status"
          aria-live="polite"
          className={cn(
            "fixed bottom-[80px] left-4 right-4 max-w-[480px] mx-auto z-50",
            "px-4 py-3 text-[13px] font-body border",
            toastMsg.ok
              ? "bg-[var(--color-success-bg)] text-[var(--color-success)] border-[var(--color-success)]"
              : "bg-[var(--color-danger-bg)] text-[var(--color-danger)] border-[var(--color-danger)]",
          )}
        >
          {toastMsg.text}
        </div>
      )}
    </div>
  );
}
