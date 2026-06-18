/**
 * Dashboard («Панель») — главный экран Mini App под канон mini-dashboard.jsx.
 * scan-header (sticky) → hero 64px + HealthBar → SpendChart → KPI 2×2 →
 * лента событий → очереди DISABLE/ENABLE. Данные: useDashboardBatch + useSpendSeries.
 */
import { createFileRoute, useNavigate } from "@tanstack/react-router";
import { useEffect, useState } from "react";
import { RefreshCw, Play } from "lucide-react";
import {
  formatSpend,
  formatRelativeTime,
  normalizeAlertState,
  normalizeTaskStatus,
} from "@fb/shared";
import type { DashboardBatch } from "@fb/shared";
import {
  useDashboardBatch,
  useObserverSettings,
  useToggleScanning,
  useTriggerScan,
  useSpendSeries,
} from "@/lib/api";
import { haptic } from "@/lib/tg";
import {
  Eyebrow,
  PulseDot,
  CountdownRing,
  PausedRing,
  SpendChart,
  HealthBar,
  RulePills,
} from "@/components/data";
import { KpiPlate, AlertStateBadge, TaskStatusBadge, Skeleton, EmptyState } from "@/components/ui";
import { useCountUp } from "@/lib/hooks/useCountUp";
import { cn } from "@/lib/cn";

export const Route = createFileRoute("/")({
  component: DashboardPage,
});

// ─── Живой обратный отсчёт до следующего скана ──────────────────────────────

function useScanCountdown(intervalSec: number, lastScanIso: string | null | undefined) {
  const [now, setNow] = useState(() => Date.now());
  useEffect(() => {
    const t = setInterval(() => setNow(Date.now()), 1000);
    return () => clearInterval(t);
  }, []);
  if (!intervalSec || intervalSec <= 0) return { next: 0, interval: 0 };
  const last = lastScanIso ? new Date(lastScanIso).getTime() : now;
  const elapsed = Math.max(0, Math.floor((now - last) / 1000));
  const next = Math.max(0, intervalSec - (elapsed % intervalSec));
  return { next, interval: intervalSec };
}

// ─── Hero-число с count-up ──────────────────────────────────────────────────

function HeroNumber({ value }: { value: number }) {
  const animated = useCountUp(value);
  return (
    <span
      className="font-display text-bg-11"
      style={{ fontSize: 64, fontWeight: 500, lineHeight: 0.82, letterSpacing: "-0.03em" }}
    >
      {animated.toLocaleString("en-US")}
    </span>
  );
}

// ─── Компонент ──────────────────────────────────────────────────────────────

function DashboardPage() {
  const navigate = useNavigate();
  const { data, isLoading, isError, error, refetch } = useDashboardBatch({ refetchInterval: 20_000 });
  const { data: obsSettings } = useObserverSettings();
  const { data: spend } = useSpendSeries(24);
  const toggleScanMutation = useToggleScanning();
  const triggerScanMutation = useTriggerScan();

  const [toast, setToast] = useState<{ text: string; ok: boolean } | null>(null);
  const showToast = (text: string, ok = true) => {
    setToast({ text, ok });
    setTimeout(() => setToast(null), 3000);
  };

  const batch = data as DashboardBatch | undefined;
  const stats = batch?.stats;
  const scanOn = obsSettings?.is_scanning_enabled ?? true;
  const interval = obsSettings?.default_interval_seconds ?? 60;
  const { next } = useScanCountdown(interval, stats?.last_scan_at);

  const total = stats?.total_ads_monitored ?? 0;
  const normal = stats?.ads_in_normal ?? 0;
  const warning = stats?.ads_in_warning ?? 0;
  const stop = stats?.ads_in_stop ?? 0;
  const disabled = stats?.ads_in_disabled ?? 0;
  const live = warning > 0 || stop > 0;

  const spendSeries = spend ?? [];
  const spendSum = spendSeries.reduce((a, b) => a + b, 0);

  const handleScanNow = async () => {
    haptic.impact("medium");
    try {
      await triggerScanMutation.mutateAsync();
      haptic.notify("success");
      showToast("Сканирование запущено");
      setTimeout(() => void refetch(), 3000);
    } catch (e: unknown) {
      haptic.notify("error");
      showToast((e as Error).message ?? "Ошибка", false);
    }
  };

  const handleResume = async () => {
    haptic.impact("medium");
    try {
      await toggleScanMutation.mutateAsync({ enabled: true });
      haptic.notify("success");
      showToast("Observer возобновлён");
    } catch (e: unknown) {
      haptic.notify("error");
      showToast((e as Error).message ?? "Ошибка", false);
    }
  };

  const incidents = batch?.recent_incidents ?? [];
  const disableTasks = (batch?.recent_disable_tasks ?? []).filter((raw) => {
    const t = raw as Record<string, unknown>;
    return ["PENDING", "RUNNING", "RETRYING", "FAILED"].includes(
      normalizeTaskStatus(String(t["status"] ?? "")),
    );
  });
  const enableTasks = (batch?.recent_enable_tasks ?? []).filter((raw) => {
    const t = raw as Record<string, unknown>;
    return ["PENDING", "RUNNING", "RETRYING", "FAILED"].includes(
      normalizeTaskStatus(String(t["status"] ?? "")),
    );
  });

  return (
    <div className="flex flex-col">
      {/* ── scan-header (sticky) ── */}
      <header className="sticky top-0 z-10 px-4 pt-2 pb-3 border-b border-[var(--hairline)] bg-bg-0">
        <div className="flex items-center justify-between gap-3">
          <div className="min-w-0">
            <Eyebrow num="01">{`ОБЗОР · ${scanOn ? "LIVE" : "ПАУЗА"}`}</Eyebrow>
            <h1
              className="font-display font-medium text-bg-11 m-0 mt-1 leading-[1.05]"
              style={{ fontSize: 26, letterSpacing: "-0.02em" }}
            >
              Панель
            </h1>
          </div>
          <div className="flex items-center gap-3 shrink-0">
            {scanOn ? (
              <>
                <div className="flex items-center gap-2">
                  <CountdownRing value={next} max={interval} size={30} />
                  <div className="leading-tight">
                    <div className="font-display text-[8px] uppercase tracking-[0.12em] text-bg-9">
                      СЛЕД.
                    </div>
                    <div className="font-display tabular-nums text-[11px] text-bg-10">{next}с</div>
                  </div>
                </div>
                <button
                  type="button"
                  aria-label="Сканировать сейчас"
                  onClick={() => void handleScanNow()}
                  disabled={triggerScanMutation.isPending}
                  className="inline-flex items-center justify-center bg-accent text-bg-0 w-11 h-11 rounded-[var(--radius-2)] disabled:opacity-60"
                >
                  <RefreshCw
                    size={18}
                    strokeWidth={1.8}
                    className={triggerScanMutation.isPending ? "animate-spin" : undefined}
                  />
                </button>
              </>
            ) : (
              <>
                <PausedRing size={30} />
                <button
                  type="button"
                  aria-label="Возобновить Observer"
                  onClick={() => void handleResume()}
                  disabled={toggleScanMutation.isPending}
                  className="inline-flex items-center justify-center bg-accent text-bg-0 w-11 h-11 rounded-[var(--radius-2)] disabled:opacity-60"
                >
                  <Play size={18} strokeWidth={1.8} />
                </button>
              </>
            )}
          </div>
        </div>
        <div className="font-display tabular-nums text-[12px] text-bg-9 mt-2 flex items-center gap-1.5">
          скан {stats?.last_scan_at ? `${formatRelativeTime(stats.last_scan_at)} назад` : "—"}
          <span className="text-bg-7">·</span>
          наблюдатель {scanOn ? "активен" : "пауза"}
        </div>
      </header>

      {isError && (
        <div className="px-4 pt-4">
          <EmptyState
            title="Ошибка загрузки"
            description={(error as Error)?.message ?? "Повторите позже"}
          />
        </div>
      )}

      <div className="flex flex-col gap-5 p-4">
        {/* ── hero ── */}
        <section>
          <div className="flex items-center gap-2 mb-2.5">
            <PulseDot size={9} color={live ? "var(--warning)" : "var(--success)"} />
            <Eyebrow className={live ? "text-warning" : "text-success"}>
              {live ? "ТРЕБУЕТ ВНИМАНИЯ" : "СИСТЕМА В НОРМЕ"}
            </Eyebrow>
          </div>
          <div className="flex items-baseline gap-3 mb-4">
            {isLoading ? (
              <Skeleton className="h-14 w-24" />
            ) : (
              <HeroNumber value={total} />
            )}
            <span className="text-[14px] text-bg-10 max-w-[130px] leading-tight">
              объявлений под контролем
            </span>
          </div>
          <HealthBar normal={normal} warning={warning} stop={stop} compact />
        </section>

        {/* ── spend chart ── */}
        <section className="border border-[var(--hairline)] rounded-[var(--radius-3)] bg-bg-1 p-4">
          <div className="flex items-baseline justify-between mb-2.5">
            <Eyebrow>SPEND × ЧАС · 24Ч</Eyebrow>
            <span className="font-display tabular-nums text-[15px] text-bg-11">
              {formatSpend(spendSum)}
            </span>
          </div>
          <SpendChart data={spendSeries} height={120} live={scanOn} animate />
        </section>

        {/* ── KPI 2×2 ── */}
        <section
          aria-label="Статистика"
          className="grid grid-cols-2 gap-px bg-[var(--hairline)] rounded-[var(--radius-3)] overflow-hidden"
        >
          {isLoading ? (
            [...Array(4)].map((_, i) => (
              <div key={i} className="bg-bg-1 p-3 space-y-2">
                <Skeleton className="h-3 w-16" />
                <Skeleton className="h-7 w-12" />
              </div>
            ))
          ) : (
            <>
              <KpiPlate eyebrow="ВСЕГО" label="активных" value={total} variant="default" />
              <KpiPlate eyebrow="СТОП" label="сигналов" value={stop} variant={stop > 0 ? "stop" : "default"} />
              <KpiPlate eyebrow="ПРЕДУПР." label="warning" value={warning} variant={warning > 0 ? "warn" : "default"} />
              <KpiPlate eyebrow="ОТКЛЮЧЕНО" label="за сутки" value={disabled} variant="default" />
            </>
          )}
        </section>

        {/* ── лента событий ── */}
        <section>
          <div className="flex items-center justify-between mb-2.5">
            <Eyebrow num="02">СОБЫТИЯ ПО ОБЪЯВЛЕНИЯМ</Eyebrow>
            <span className="text-[11px] text-bg-9 inline-flex items-center gap-1.5">
              <PulseDot
                size={6}
                color={!scanOn ? "var(--warning)" : live ? "var(--success)" : "var(--bg-7)"}
              />
              {!scanOn ? "пауза" : live ? "live" : "тихо"}
            </span>
          </div>
          <div className="border border-[var(--hairline)] rounded-[var(--radius-3)] overflow-hidden bg-bg-1">
            {isLoading ? (
              <div className="p-4 space-y-3">
                {[...Array(2)].map((_, i) => <Skeleton key={i} className="h-12 w-full" />)}
              </div>
            ) : incidents.length === 0 ? (
              <EmptyState title="Алертов за 24ч нет" description="Правила работают, трафик ровный" />
            ) : (
              <div className="divide-y divide-[var(--hairline)]">
                {incidents.slice(0, 6).map((raw) => {
                  const inc = raw as Record<string, unknown>;
                  const fbAdId = String(inc["fb_ad_id"] ?? "");
                  const adName = inc["ad_name"] != null ? String(inc["ad_name"]) : null;
                  const state = normalizeAlertState(String(inc["alert_state"] ?? "normal"));
                  const codes = [
                    ...((inc["stop_rule_codes"] as string[]) ?? []),
                    ...((inc["warning_rule_codes"] as string[]) ?? []),
                  ];
                  return (
                    <button
                      key={fbAdId}
                      type="button"
                      className="w-full text-left px-4 py-3 min-h-[44px] flex items-start justify-between gap-3 active:bg-bg-2"
                      onClick={() => {
                        haptic.selection();
                        void navigate({ to: "/ads/$fbAdId", params: { fbAdId } });
                      }}
                    >
                      <div className="flex-1 min-w-0">
                        <p className="font-display text-[13px] text-bg-11 truncate leading-tight">
                          {adName ?? fbAdId}
                        </p>
                        {codes.length > 0 && (
                          <RulePills codes={codes} max={3} className="mt-1.5" />
                        )}
                      </div>
                      <AlertStateBadge state={state} size="sm" withDot />
                    </button>
                  );
                })}
              </div>
            )}
          </div>
        </section>

        {/* ── очереди задач ── */}
        <section>
          <Eyebrow num="03" className="mb-2.5 flex">ОЧЕРЕДЬ ЗАДАЧ</Eyebrow>
          <div className="flex flex-col gap-3">
            {[
              { title: "DISABLE QUEUE", rows: disableTasks, color: "var(--danger)" },
              { title: "ENABLE QUEUE", rows: enableTasks, color: "var(--success)" },
            ].map((q) => (
              <div key={q.title} className="border border-[var(--hairline)] rounded-[var(--radius-3)] overflow-hidden bg-bg-1">
                <div className="flex items-center justify-between px-3.5 py-2.5 border-b border-[var(--hairline)]">
                  <Eyebrow>{q.title}</Eyebrow>
                  <span
                    className="font-display tabular-nums text-[13px]"
                    style={{ color: q.rows.length ? q.color : "var(--bg-8)" }}
                  >
                    {q.rows.length}
                  </span>
                </div>
                {q.rows.length === 0 ? (
                  <div className="px-4 py-3 text-[12px] text-bg-9">Очередь пуста</div>
                ) : (
                  <div className="divide-y divide-[var(--hairline)]">
                    {q.rows.slice(0, 5).map((raw, i) => {
                      const t = raw as Record<string, unknown>;
                      const status = normalizeTaskStatus(String(t["status"] ?? ""));
                      const title = String(t["ad_name"] ?? t["fb_ad_id"] ?? t["id"] ?? i);
                      return (
                        <div
                          key={String(t["id"] ?? i)}
                          className="px-4 py-2.5 flex items-center justify-between gap-3 min-h-[44px]"
                        >
                          <span className="font-display text-[13px] text-bg-11 truncate">{title}</span>
                          <TaskStatusBadge status={status} size="sm" />
                        </div>
                      );
                    })}
                  </div>
                )}
              </div>
            ))}
          </div>
        </section>
      </div>

      {/* Toast */}
      {toast && (
        <div
          role="status"
          aria-live="polite"
          className={cn(
            "fixed bottom-[80px] left-4 right-4 max-w-[440px] mx-auto z-50 px-4 py-3 text-[13px] border rounded-[var(--radius-2)]",
            toast.ok
              ? "bg-success-bg text-success border-success"
              : "bg-danger-bg text-danger border-danger",
          )}
        >
          {toast.text}
        </div>
      )}
    </div>
  );
}
