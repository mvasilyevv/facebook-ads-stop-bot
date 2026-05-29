/**
 * /ads/$fbAdId — drawer-детальный вид объявления.
 *
 * Отображает:
 *   - Snapshot метрик (KV-сетка из 6 полей)
 *   - Лента алертов (AlertEventRow)
 *   - Лента задач (TaskQueueRow)
 *
 * Источники данных:
 *   - useAdTimeline(fb_ad_id) — метрики + alerts + tasks.
 *   - Данные snapshot берём из кэша useAds через queryClient.
 *
 * Открывается как overlay поверх /ads/ через Drawer-компонент.
 */

import { useNavigate } from "@tanstack/react-router";
import { createFileRoute } from "@tanstack/react-router";
import { useQueryClient } from "@tanstack/react-query";
import { ExternalLink, X } from "lucide-react";

import { Badge, alertStateToBadge } from "@/components/ui/Badge";
import { Button } from "@/components/ui/Button";
import { Skeleton } from "@/components/ui/Skeleton";
import { ErrorState } from "@/components/ui/ErrorState";
import { EmptyState } from "@/components/ui/EmptyState";
import { AlertEventRow } from "@/components/domain/AlertEventRow";
import { TaskQueueRow } from "@/components/domain/TaskQueueRow";
import { useAdTimeline, useCreateDisableTask } from "@/lib/api/ads";
import { formatSpend, formatRelativeTime, formatDateTime } from "@/lib/utils/format";
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

  // Берём snapshot из кэша ads, если он там есть — для шапки drawer'а
  const cachedAd = findCachedAd(qc, fbAdId);

  const handleClose = () => {
    navigate({ to: "/ads" });
  };

  const handleDisable = () => {
    disableMutation.mutate(fbAdId, {
      onSuccess: () =>
        toast.success("Disable запущен", `Задача для ${fbAdId} добавлена в очередь.`),
      onError: (err) =>
        toast.error(
          "Не удалось запустить disable",
          err instanceof Error ? err.message : String(err),
        ),
    });
  };

  return (
    // Оверлей поверх страницы — имитируем drawer-overlay + panel
    <div
      className="fixed inset-0 z-[200]"
      role="dialog"
      aria-modal="true"
      aria-label={`Детали объявления ${cachedAd?.ad_name ?? fbAdId}`}
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
              {cachedAd?.ad_name ?? fbAdId}
            </h2>
            <div className="font-display text-[11px] text-bg-9 tracking-wide">
              {fbAdId}
              {cachedAd?.campaign_name ? (
                <>
                  <span className="text-bg-7 mx-1.5">·</span>
                  {cachedAd.campaign_name}
                </>
              ) : null}
            </div>
            {cachedAd && (
              <div className="flex gap-2 mt-3 flex-wrap">
                <Badge variant={alertStateToBadge(cachedAd.alert_state)}>
                  {cachedAd.alert_state.replace("_sent", "")}
                </Badge>
                {cachedAd.offer_code && (
                  <span className="inline-flex items-center px-2 py-0.5 bg-bg-3 border border-bg-6 text-bg-10 font-display text-[10.5px] tracking-[0.04em] uppercase">
                    {cachedAd.offer_code}
                  </span>
                )}
                {cachedAd.snoozed_until && (
                  <span className="inline-flex items-center px-2 py-0.5 bg-bg-3 border border-bg-6 text-bg-9 font-display text-[10.5px] tracking-wide">
                    отложено до {formatDateTime(cachedAd.snoozed_until)}
                  </span>
                )}
              </div>
            )}
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

            {cachedAd?.metrics ? (
              <div className="grid grid-cols-2 gap-x-6 gap-y-3 py-4 border-t border-b border-bg-3">
                <KvField label="Spend" value={formatSpend(cachedAd.metrics.spend)} />
                <KvField label="CPL" value={formatSpend(cachedAd.metrics.cost_per_lead)} />
                <KvField
                  label="Leads"
                  value={cachedAd.metrics.leads != null ? String(cachedAd.metrics.leads) : "—"}
                />
                <KvField
                  label="Frequency"
                  value={
                    cachedAd.metrics.frequency != null
                      ? parseFloat(String(cachedAd.metrics.frequency)).toFixed(1)
                      : "—"
                  }
                />
                <KvField
                  label="CTR"
                  value={
                    cachedAd.metrics.ctr != null
                      ? `${parseFloat(String(cachedAd.metrics.ctr)).toFixed(2)}%`
                      : "—"
                  }
                />
                <KvField
                  label="Impressions"
                  value={
                    cachedAd.metrics.impressions != null
                      ? cachedAd.metrics.impressions.toLocaleString("en-US")
                      : "—"
                  }
                />
              </div>
            ) : (
              <div className="py-4 border-t border-b border-bg-3 text-bg-8 font-display text-[12px]">
                Нет данных метрик
              </div>
            )}
          </section>

          {/* Секция 2: Timeline алертов */}
          <section className="mb-8">
            <h3 className="font-display text-[10px] tracking-[0.14em] uppercase text-bg-8 mb-3">
              <span className="text-bg-7 mr-2">02</span>Алерты · последние 24ч
            </h3>

            {timelineQuery.isLoading ? (
              <div className="flex flex-col gap-2">
                {Array.from({ length: 3 }).map((_, i) => (
                  <Skeleton key={i} height={48} />
                ))}
              </div>
            ) : timelineQuery.isError ? (
              <ErrorState
                error={timelineQuery.error}
                onRetry={() => timelineQuery.refetch()}
              />
            ) : (timelineQuery.data?.alerts ?? []).length === 0 ? (
              <EmptyState title="Нет алертов" className="py-6" />
            ) : (
              <div className="border-t border-bg-3">
                {(timelineQuery.data?.alerts ?? []).map((event) => (
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
            ) : (timelineQuery.data?.tasks ?? []).length === 0 ? (
              <EmptyState title="Нет задач" className="py-6" />
            ) : (
              <div>
                {(timelineQuery.data?.tasks ?? []).map((task) => (
                  <TaskQueueRow key={task.id} task={task} />
                ))}
              </div>
            )}
          </section>
        </div>

        {/* Footer */}
        <div className="px-8 py-4 border-t border-bg-5 bg-bg-1 flex items-center justify-between gap-3 shrink-0">
          <Button
            variant="ghost"
            size="sm"
            rightIcon={<ExternalLink size={12} aria-hidden="true" />}
            onClick={() => {
              // Ads Manager deep-link (может не работать без авторизации Meta)
              window.open(
                `https://www.facebook.com/adsmanager/manage/ads?act=&selected_ad_ids=${fbAdId}`,
                "_blank",
                "noopener,noreferrer",
              );
            }}
          >
            Открыть в Ads Manager
          </Button>
          <div className="flex gap-2">
            <Button variant="secondary" size="sm">
              Отложить на 1ч
            </Button>
            <Button
              variant="danger"
              size="sm"
              loading={disableMutation.isPending}
              onClick={handleDisable}
            >
              <X size={14} aria-hidden="true" />
              Отключить вручную
            </Button>
          </div>
        </div>
      </aside>
    </div>
  );
}

/** KV-поле в snapshot-сетке. */
function KvField({ label, value, className }: { label: string; value: string; className?: string }) {
  return (
    <div className={cn("flex flex-col gap-1", className)}>
      <span className="font-display text-[10px] tracking-[0.1em] uppercase text-bg-8">
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
 * Перебираем все кэши с ключом ["ads", ...] и ищем совпадение по fb_ad_id.
 */
function findCachedAd(qc: ReturnType<typeof useQueryClient>, fbAdId: string): AdSnapshot | null {
  const cache = qc.getQueriesData<AdSnapshot[]>({ queryKey: ["ads"] });
  for (const [, data] of cache) {
    if (!Array.isArray(data)) continue;
    const found = data.find((a) => a.fb_ad_id === fbAdId);
    if (found) return found;
  }
  return null;
}
