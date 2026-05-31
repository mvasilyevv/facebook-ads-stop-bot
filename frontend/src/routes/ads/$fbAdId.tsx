/**
 * /ads/$fbAdId — drawer-детальный вид объявления.
 *
 * Отображает:
 *   - Snapshot метрик (KV-сетка)
 *   - Лента алертов (AlertEventRow)
 *   - Лента задач (TaskQueueRow)
 *
 * Источники данных:
 *   - useAdTimeline(fb_ad_id) — шапка (имя/кампания/оффер) + метрики + alerts + tasks.
 *   - Доп. snapshot-метрики (CPL/CTR/Частота) берём из кэша useAds, если есть.
 *     При прямом заходе по URL (кэш пуст) шапку и базовые метрики даёт timeline.
 *
 * Открывается как overlay поверх /ads/ через Drawer-компонент.
 */

import { useState } from "react";
import { useNavigate } from "@tanstack/react-router";
import { createFileRoute } from "@tanstack/react-router";
import { useQueryClient } from "@tanstack/react-query";
import { X } from "lucide-react";

import { Badge, alertStateToBadge } from "@/components/ui/Badge";
import { Button } from "@/components/ui/Button";
import { Skeleton } from "@/components/ui/Skeleton";
import { ErrorState } from "@/components/ui/ErrorState";
import { EmptyState } from "@/components/ui/EmptyState";
import { ConfirmDialog } from "@/components/ui/ConfirmDialog";
import { AlertEventRow } from "@/components/domain/AlertEventRow";
import { TaskQueueRow } from "@/components/domain/TaskQueueRow";
import { useAdTimeline, useCreateDisableTask, type AdTimeline } from "@/lib/api/ads";
import { ALERT_STATE_LABELS } from "@/lib/constants/states";
import { formatSpend, formatRelativeTime, formatDateTime, truncateAdId } from "@/lib/utils/format";
import { toast } from "@/components/ui/Toast";
import { cn } from "@/lib/utils/cn";
import type { AdSnapshot } from "@/lib/types/api";

export const Route = createFileRoute("/ads/$fbAdId")({
  component: AdDrawerPage,
});

function AdDrawerPage() {
  const { fbAdId } = Route.useParams();
  const navigate = useNavigate();
  const qc = useQueryClient();

  const timelineQuery = useAdTimeline(fbAdId);
  const disableMutation = useCreateDisableTask();
  const [confirmOpen, setConfirmOpen] = useState(false);

  // Snapshot из кэша списка ads (полные метрики). При прямом URL — null.
  const cachedAd = findCachedAd(qc, fbAdId);
  const tl = timelineQuery.data;

  // Шапка: кэш → timeline → усечённый id.
  const adName = cachedAd?.ad_name ?? tl?.ad_name ?? truncateAdId(fbAdId);
  const campaignName = cachedAd?.campaign_name ?? tl?.campaign_name ?? null;
  const adsetName = cachedAd?.adset_name ?? tl?.adset_name ?? null;
  const offerCode = cachedAd?.offer_code ?? tl?.offer_code ?? null;

  const snapshot = buildSnapshotFields(cachedAd, tl);

  const handleClose = () => {
    navigate({ to: "/ads" });
  };

  const handleDisable = () => {
    disableMutation.mutate(fbAdId, {
      onSuccess: () =>
        toast.success("Отключение запущено", `Задача для «${adName}» добавлена в очередь.`),
      onError: (err) =>
        toast.error(
          "Не удалось запустить отключение",
          err instanceof Error ? err.message : String(err),
        ),
    });
  };

  return (
    <div
      className="fixed inset-0 z-[200]"
      role="dialog"
      aria-modal="true"
      aria-label={`Детали объявления ${adName}`}
    >
      {/* Backdrop */}
      <div
        className="absolute inset-0 bg-bg-0/65 backdrop-blur-sm"
        onClick={handleClose}
        aria-hidden="true"
      />

      {/* Panel */}
      <aside className="absolute right-0 top-0 bottom-0 w-[640px] bg-bg-1 border-l border-bg-5 flex flex-col shadow-[-8px_0_32px_rgba(0,0,0,0.4)]">
        {/* Header */}
        <div className="flex items-start justify-between gap-4 px-8 py-6 border-b border-bg-5 shrink-0">
          <div className="flex-1 min-w-0">
            <div className="font-display text-[10px] tracking-[0.14em] uppercase text-bg-8 mb-2">
              <span className="text-bg-7 mr-2">06</span>ДЕТАЛИ ОБЪЯВЛЕНИЯ · ИСТОРИЯ
            </div>
            <h2 className="font-display text-[20px] font-medium tracking-tight text-bg-11 m-0 mb-1.5 leading-snug truncate">
              {adName}
            </h2>
            <div className="font-display text-[11px] text-bg-9 tracking-wide" title={fbAdId}>
              {fbAdId}
            </div>
            {campaignName || adsetName ? (
              <div className="font-display text-[11px] text-bg-9 tracking-wide mt-0.5 truncate">
                {campaignName}
                {campaignName && adsetName ? <span className="text-bg-7 mx-1.5">›</span> : null}
                {adsetName}
              </div>
            ) : null}
            <div className="flex gap-2 mt-3 flex-wrap">
              {cachedAd ? (
                <Badge variant={alertStateToBadge(cachedAd.alert_state)}>
                  {ALERT_STATE_LABELS[cachedAd.alert_state as keyof typeof ALERT_STATE_LABELS] ??
                    cachedAd.alert_state}
                </Badge>
              ) : null}
              {offerCode ? (
                <span className="inline-flex items-center px-2 py-0.5 bg-bg-3 border border-bg-6 text-bg-10 font-display text-[10.5px] tracking-[0.04em] uppercase">
                  {offerCode}
                </span>
              ) : null}
              {cachedAd?.snoozed_until ? (
                <span className="inline-flex items-center px-2 py-0.5 bg-bg-3 border border-bg-6 text-bg-9 font-display text-[10.5px] tracking-wide">
                  отложено до {formatDateTime(cachedAd.snoozed_until)} UTC
                </span>
              ) : null}
            </div>
          </div>
          <button
            type="button"
            onClick={handleClose}
            aria-label="Закрыть"
            className="size-8 shrink-0 border border-bg-6 flex items-center justify-center text-bg-10 hover:bg-bg-2 hover:text-bg-11 transition-colors"
          >
            <X size={14} aria-hidden="true" />
          </button>
        </div>

        {/* Body — scrollable */}
        <div className="flex-1 overflow-y-auto px-8 py-6">
          {/* Секция 1: Snapshot метрик */}
          <section className="mb-8">
            <h3 className="font-display text-[10px] tracking-[0.14em] uppercase text-bg-8 mb-3">
              <span className="text-bg-7 mr-2">01</span>
              Snapshot
              {cachedAd?.last_seen_at ? (
                <span className="text-bg-7 ml-2 normal-case tracking-normal">
                  · посл. скан {formatRelativeTime(cachedAd.last_seen_at)}
                </span>
              ) : null}
            </h3>

            {snapshot ? (
              <div className="grid grid-cols-2 gap-x-6 gap-y-3 py-4 border-t border-b border-bg-3">
                {snapshot.map((f) => (
                  <KvField key={f.label} label={f.label} value={f.value} title={f.title} />
                ))}
              </div>
            ) : timelineQuery.isLoading ? (
              <Skeleton height={120} className="w-full" />
            ) : (
              <div className="py-4 border-t border-b border-bg-3 text-bg-8 font-display text-[12px]">
                Нет данных метрик за период.
              </div>
            )}
          </section>

          {/* Секция 2: Timeline алертов */}
          <section className="mb-8">
            <h3 className="font-display text-[10px] tracking-[0.14em] uppercase text-bg-8 mb-3">
              <span className="text-bg-7 mr-2">02</span>Алерты · период
            </h3>

            {timelineQuery.isLoading ? (
              <div className="flex flex-col gap-2">
                {Array.from({ length: 3 }).map((_, i) => (
                  <Skeleton key={i} height={48} />
                ))}
              </div>
            ) : timelineQuery.isError ? (
              <ErrorState error={timelineQuery.error} onRetry={() => timelineQuery.refetch()} />
            ) : (tl?.alerts ?? []).length === 0 ? (
              <EmptyState title="Нет алертов" className="py-6" />
            ) : (
              <div className="border-t border-bg-3">
                {(tl?.alerts ?? []).map((event) => (
                  <AlertEventRow key={event.id} event={event} />
                ))}
              </div>
            )}
          </section>

          {/* Секция 3: Задачи */}
          <section>
            <h3 className="font-display text-[10px] tracking-[0.14em] uppercase text-bg-8 mb-3">
              <span className="text-bg-7 mr-2">03</span>Задачи
            </h3>

            {timelineQuery.isLoading ? (
              <div className="flex flex-col gap-2">
                {Array.from({ length: 2 }).map((_, i) => (
                  <Skeleton key={i} height={44} />
                ))}
              </div>
            ) : timelineQuery.isError ? (
              <ErrorState error={timelineQuery.error} onRetry={() => timelineQuery.refetch()} />
            ) : (tl?.tasks ?? []).length === 0 ? (
              <EmptyState title="Нет задач" className="py-6" />
            ) : (
              <div>
                {(tl?.tasks ?? []).map((task) => (
                  <TaskQueueRow key={task.id} task={task} />
                ))}
              </div>
            )}
          </section>
        </div>

        {/* Footer */}
        <div className="px-8 py-4 border-t border-bg-5 bg-bg-1 flex items-center justify-end gap-3 shrink-0">
          <Button
            variant="danger"
            size="sm"
            loading={disableMutation.isPending}
            onClick={() => setConfirmOpen(true)}
          >
            <X size={14} aria-hidden="true" />
            Отключить вручную
          </Button>
        </div>
      </aside>

      <ConfirmDialog
        open={confirmOpen}
        onOpenChange={setConfirmOpen}
        title="Отключить объявление?"
        description={`«${adName}» будет отключено через очередь задач — открутка остановится. Отменить автоматически нельзя.`}
        confirmLabel="Отключить"
        onConfirm={handleDisable}
      />
    </div>
  );
}

/** Поля snapshot-сетки: полные из кэша списка, иначе базовые из timeline. */
function buildSnapshotFields(
  cachedAd: AdSnapshot | null,
  tl: AdTimeline | undefined,
): Array<{ label: string; value: string; title?: string }> | null {
  if (cachedAd?.metrics) {
    const m = cachedAd.metrics;
    return [
      { label: "Траты", value: formatSpend(m.spend) },
      { label: "CPL", value: formatSpend(m.cost_per_lead), title: "Стоимость лида" },
      { label: "Лиды", value: m.leads != null ? String(m.leads) : "—" },
      {
        label: "Частота",
        value: m.frequency != null ? parseFloat(String(m.frequency)).toFixed(1) : "—",
        title: "Показов на уникального пользователя",
      },
      {
        label: "CTR",
        value: m.ctr != null ? `${parseFloat(String(m.ctr)).toFixed(2)}%` : "—",
        title: "Кликабельность",
      },
      {
        label: "Показы",
        value: m.impressions != null ? m.impressions.toLocaleString("en-US") : "—",
      },
    ];
  }
  const last = tl?.metrics?.[tl.metrics.length - 1];
  if (last) {
    return [
      { label: "Траты", value: formatSpend(last.spend) },
      { label: "Лиды", value: last.leads != null ? String(last.leads) : "—" },
      {
        label: "Показы",
        value: last.impressions != null ? last.impressions.toLocaleString("en-US") : "—",
      },
      { label: "Клики", value: last.clicks != null ? String(last.clicks) : "—" },
      { label: "Депозиты", value: last.deposits != null ? String(last.deposits) : "—" },
    ];
  }
  return null;
}

/** KV-поле в snapshot-сетке. */
function KvField({
  label,
  value,
  title,
  className,
}: {
  label: string;
  value: string;
  title?: string;
  className?: string;
}) {
  return (
    <div className={cn("flex flex-col gap-1", className)}>
      <span
        title={title}
        className={cn(
          "font-display text-[10px] tracking-[0.1em] uppercase text-bg-8",
          title && "cursor-help decoration-dotted underline underline-offset-2",
        )}
      >
        {label}
      </span>
      <span className="font-display text-[18px] font-medium tabular-nums text-bg-11 leading-tight">
        {value}
      </span>
    </div>
  );
}

/**
 * Ищет AdSnapshot в кэше TanStack Query.
 * useAds кэширует { items, total } под ключом ["ads", params]; перебираем все
 * совпадения по префиксу ["ads"] и ищем нужный fb_ad_id в items.
 */
function findCachedAd(qc: ReturnType<typeof useQueryClient>, fbAdId: string): AdSnapshot | null {
  const cache = qc.getQueriesData({ queryKey: ["ads"] });
  for (const [, data] of cache) {
    const items = (data as { items?: AdSnapshot[] } | undefined)?.items;
    if (!Array.isArray(items)) continue;
    const found = items.find((a) => a.fb_ad_id === fbAdId);
    if (found) return found;
  }
  return null;
}
