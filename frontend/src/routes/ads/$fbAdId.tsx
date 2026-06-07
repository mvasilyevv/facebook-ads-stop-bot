/**
 * AdDetailDrawer — drawer деталей объявления.
 *
 * Открывается при переходе на /ads/:fbAdId.
 * Esc / close-кнопка → navigate назад к /ads/.
 *
 * Структура:
 *   Header: eyebrow "06 · AD DETAIL" / ad_name + badge / "Open token · Xм"
 *   Body:
 *     KVGrid снапшот метрик (spend/cpl/ctr/leads/deposits) с warn/bad
 *     MiniChart (spend за 6h)
 *     Timeline алертов и задач (DESC)
 *   Footer:
 *     "Открыть в Ads Manager ↗" / "Снуз 1ч" / "Отключить" (ConfirmDialog)
 */

import { createFileRoute, useRouter, useParams } from "@tanstack/react-router";
import { ExternalLink } from "lucide-react";
import { useMemo } from "react";
import { useState } from "react";

import { Drawer } from "@/components/ui/Drawer";
import { Button } from "@/components/ui/Button";
import { Badge } from "@/components/ui/Badge";
import { ConfirmDialog } from "@/components/ui/ConfirmDialog";
import { Skeleton } from "@/components/ui/Skeleton";
import { ErrorState } from "@/components/ui/ErrorState";
import { KVGrid } from "@/components/data/timeline/KVGrid";
import { MiniChart, type MiniChartPoint } from "@/components/data/charts/MiniChart";
import { Timeline, type TimelineItem } from "@/components/data/timeline/Timeline";

import { useAdTimeline, useSnoozeAd, useBulkDisable } from "@/lib/api/ads";
import { useSpendHistory } from "@/lib/api/dashboard";

import {
  formatSpend,
  formatInt,
  alertStateToBadgeVariant,
  ALERT_STATE_LABELS,
  formatRelativeTime,
  type AlertState,
} from "@fb/shared";
import type { components } from "@fb/shared/api/generated";

type AlertRow = components["schemas"]["AlertRow"];
type TaskRow = components["schemas"]["TaskRow"];
type MetricsBlock = components["schemas"]["MetricsBlock"];

// ─── Route ────────────────────────────────────────────────────────────────────

export const Route = createFileRoute("/ads/$fbAdId")({
  component: AdDetailDrawer,
});

// ─── Компонент ────────────────────────────────────────────────────────────────

function AdDetailDrawer() {
  const router = useRouter();
  const { fbAdId } = useParams({ from: "/ads/$fbAdId" });

  const [disableConfirmOpen, setDisableConfirmOpen] = useState(false);

  function handleClose() {
    void router.navigate({ to: "/ads" });
  }

  // ── Данные ────────────────────────────────────────────────────────────────
  const { data: timeline, isLoading, isError, error } = useAdTimeline(fbAdId, {
    include_metrics: true,
    include_alerts: true,
    include_tasks: true,
  });

  // Spend история 6h для MiniChart
  const { data: spendHistory } = useSpendHistory({ hours: 6, fb_ad_id: fbAdId });

  // ── Мутации ───────────────────────────────────────────────────────────────
  const snooze = useSnoozeAd(fbAdId);
  const bulkDisable = useBulkDisable();

  // ── KVGrid из последних метрик ────────────────────────────────────────────
  const metrics = useMemo<MetricsBlock | null>(() => {
    if (!timeline?.metrics?.length) return null;
    // Метрики идут по времени — берём последние
    return timeline.metrics[timeline.metrics.length - 1] as unknown as MetricsBlock;
  }, [timeline]);

  const kvItems = useMemo(() => {
    if (!metrics) return [];
    const cpl = parseFloat(metrics.cost_per_lead ?? "");
    const spend = parseFloat(metrics.spend ?? "");
    const ctr = parseFloat(metrics.ctr ?? "");
    const freq = parseFloat(metrics.frequency ?? "");

    type KVState = "default" | "warn" | "bad";
    const spendState: KVState = spend > 500 ? "bad" : spend > 300 ? "warn" : "default";
    const cplState: KVState = cpl > 30 ? "bad" : cpl > 20 ? "warn" : "default";
    const ctrState: KVState = ctr < 0.5 ? "bad" : ctr < 1 ? "warn" : "default";
    const freqState: KVState = freq > 4 ? "bad" : freq > 3 ? "warn" : "default";

    return [
      {
        label: "Spend",
        value: formatSpend(metrics.spend),
        state: spendState,
      },
      {
        label: "CPL",
        value: formatSpend(metrics.cost_per_lead),
        state: cplState,
      },
      {
        label: "CTR",
        value: `${ctr.toFixed(2)}%`,
        state: ctrState,
      },
      {
        label: "Leads",
        value: formatInt(metrics.leads ?? null),
        state: "default" as KVState,
      },
      {
        label: "Freq",
        value: freq > 0 ? freq.toFixed(1) : "—",
        state: freqState,
      },
      {
        label: "Депозиты",
        value: formatInt(metrics.deposits ?? null),
        state: "default" as KVState,
      },
    ];
  }, [metrics]);

  // ── MiniChart data из spend history ──────────────────────────────────────
  const miniPoints = useMemo<MiniChartPoint[]>(() => {
    if (!spendHistory) return [];
    return spendHistory.map((p) => ({
      label: p.cycle_ts,
      spend: Number(p.spend ?? 0),
    }));
  }, [spendHistory]);

  // ── Timeline items из alerts + tasks ─────────────────────────────────────
  const timelineItems = useMemo<TimelineItem[]>(() => {
    const items: TimelineItem[] = [];

    // Alert events
    for (const alert of (timeline?.alerts ?? []) as AlertRow[]) {
      items.push({
        id: alert.id,
        ts: alert.created_at,
        type: alert.stage === "stop" ? "stop" : "warning",
        title: alert.stage === "stop" ? "STOP triggered" : "WARNING triggered",
        ruleCodes: alert.matched_rule_codes,
      });
    }

    // Task events
    for (const task of (timeline?.tasks ?? []) as TaskRow[]) {
      const isDisable = task.task_type === "disable" || task.task_type === "meta_api_mutation";
      items.push({
        id: String(task.id),
        ts: task.created_at,
        type: "task",
        title: isDisable ? "Disable task dispatched" : `Task: ${task.task_type}`,
        meta: `статус: ${task.status} · ${task.requested_by}`,
      });
    }

    return items;
  }, [timeline]);

  // ── Eyebrow: open token time ──────────────────────────────────────────────
  // Берём время самого раннего события (начало инцидента)
  const oldestEvent = timelineItems.length > 0
    ? timelineItems.reduce((min, e) =>
        new Date(e.ts) < new Date(min.ts) ? e : min,
      )
    : null;

  const alertState = (timeline as { alert_state?: string } | null | undefined)?.alert_state as AlertState | undefined;

  // ── Ads Manager ссылка ────────────────────────────────────────────────────
  const adsManagerUrl = `https://adsmanager.facebook.com/adsmanager/manage/ads?act=ACCOUNT&selected_ad_ids=${fbAdId}`;

  // ── Disable single ad ─────────────────────────────────────────────────────
  async function handleDisableConfirm() {
    await bulkDisable.mutateAsync({
      fb_ad_ids: [fbAdId],
      reason: `manual disable via drawer idempotency:${crypto.randomUUID()}`,
    });
    handleClose();
  }

  // ── Snooze 1h ─────────────────────────────────────────────────────────────
  function handleSnooze() {
    void snooze.mutateAsync({ minutes: 60 });
  }

  return (
    <>
      <Drawer
        open
        onOpenChange={(open) => {
          if (!open) handleClose();
        }}
        eyebrow="06 · AD DETAIL"
        title={
          isLoading ? (
            <Skeleton height={20} width="70%" />
          ) : (
            <span className="flex items-center gap-2 flex-wrap">
              <span className="truncate">{timeline?.ad_name ?? fbAdId}</span>
              {alertState && (
                <Badge variant={alertStateToBadgeVariant(alertState)} size="sm">
                  {ALERT_STATE_LABELS[alertState] ?? alertState}
                </Badge>
              )}
            </span>
          )
        }
        description={
          <span className="flex items-center gap-2 flex-wrap">
            <span className="font-mono text-[11px] text-bg-9">{fbAdId}</span>
            {timeline?.offer_code && (
              <span className="font-display text-[10.5px] text-bg-8 uppercase tracking-wider">
                · {timeline.offer_code}
              </span>
            )}
            {oldestEvent && (
              <span className="font-display text-[10.5px] text-bg-8">
                · Open token {formatRelativeTime(oldestEvent.ts)}
              </span>
            )}
          </span>
        }
        width={640}
        footer={
          <DrawerFooter
            adsManagerUrl={adsManagerUrl}
            onSnooze={handleSnooze}
            onDisable={() => setDisableConfirmOpen(true)}
            isPending={snooze.isPending || bulkDisable.isPending}
          />
        }
      >
        {isError ? (
          <ErrorState
            title="Не удалось загрузить данные объявления."
            error={error}
          />
        ) : isLoading ? (
          <DrawerSkeleton />
        ) : (
          <DrawerBody
            kvItems={kvItems}
            miniPoints={miniPoints}
            timelineItems={timelineItems}
          />
        )}
      </Drawer>

      {/* ── MONEY: ConfirmDialog для отключения ────────────────────────────── */}
      <ConfirmDialog
        open={disableConfirmOpen}
        onOpenChange={setDisableConfirmOpen}
        title="Отключить объявление?"
        description={`Будет создана задача отключения через Marketing API для объявления ${fbAdId}. Действие необратимо без ручного включения.`}
        confirmLabel="Отключить"
        confirmVariant="danger"
        onConfirm={handleDisableConfirm}
      />
    </>
  );
}

// ─── Sub-компоненты ───────────────────────────────────────────────────────────

interface DrawerBodyProps {
  kvItems: Parameters<typeof KVGrid>[0]["items"];
  miniPoints: MiniChartPoint[];
  timelineItems: TimelineItem[];
}

function DrawerBody({ kvItems, miniPoints, timelineItems }: DrawerBodyProps) {
  return (
    <div className="flex flex-col gap-6">
      {/* Метрики */}
      {kvItems.length > 0 && (
        <KVGrid items={kvItems} />
      )}

      {/* MiniChart 6h spend */}
      {miniPoints.length > 0 && (
        <section>
          <div className="font-display text-[10px] tracking-[0.14em] uppercase text-bg-8 mb-3">
            Spend rate · 6h
          </div>
          <MiniChart
            data={miniPoints}
            tint="danger"
            height={120}
            aria-label="Динамика трат за 6 часов"
          />
        </section>
      )}

      {/* STOP Timeline */}
      <section>
        <div className="font-display text-[10px] tracking-[0.14em] uppercase text-bg-8 mb-3">
          История событий
        </div>
        <Timeline
          items={timelineItems}
          emptyMessage="Событий нет — объявление без инцидентов."
        />
      </section>
    </div>
  );
}

function DrawerSkeleton() {
  return (
    <div className="flex flex-col gap-6" role="status" aria-label="Загрузка данных объявления">
      {/* KVGrid skeleton */}
      <div className="grid grid-cols-2 gap-y-3 gap-x-6 py-4 border-t border-bg-3 border-b">
        {Array.from({ length: 6 }).map((_, i) => (
          <div key={i} className="flex flex-col gap-1">
            <Skeleton height={10} width="50%" />
            <Skeleton height={18} width="65%" />
          </div>
        ))}
      </div>
      {/* MiniChart skeleton */}
      <Skeleton height={120} className="w-full" />
      {/* Timeline skeleton */}
      <div className="flex flex-col gap-3">
        {Array.from({ length: 4 }).map((_, i) => (
          <div key={i} className="flex items-start gap-4 py-2.5">
            <Skeleton width={14} height={14} className="rounded-full mt-1 shrink-0" />
            <div className="flex-1">
              <Skeleton height={10} width="30%" className="mb-1" />
              <Skeleton height={13} width="70%" className="mb-1" />
              <Skeleton height={10} width="50%" />
            </div>
          </div>
        ))}
      </div>
    </div>
  );
}

interface DrawerFooterProps {
  adsManagerUrl: string;
  onSnooze: () => void;
  onDisable: () => void;
  isPending?: boolean;
}

function DrawerFooter({ adsManagerUrl, onSnooze, onDisable, isPending }: DrawerFooterProps) {
  return (
    <div className="flex items-center justify-between w-full gap-3">
      {/* Левая часть: внешняя ссылка */}
      <a
        href={adsManagerUrl}
        target="_blank"
        rel="noopener noreferrer"
        className="inline-flex items-center gap-1.5 font-display text-[12px] text-bg-9 hover:text-bg-11 transition-colors"
        aria-label="Открыть в Ads Manager"
      >
        <ExternalLink size={12} aria-hidden="true" />
        Ads Manager ↗
      </a>

      {/* Правая часть: действия */}
      <div className="flex items-center gap-2">
        <Button
          variant="secondary"
          size="sm"
          onClick={onSnooze}
          disabled={isPending}
          aria-label="Снуз на 1 час"
        >
          Снуз 1ч
        </Button>
        <Button
          variant="danger"
          size="sm"
          onClick={onDisable}
          disabled={isPending}
          loading={isPending}
          aria-label="Отключить объявление вручную"
        >
          Отключить
        </Button>
      </div>
    </div>
  );
}
