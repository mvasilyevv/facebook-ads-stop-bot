/**
 * RunsHistory — история запусков кампаний.
 * Список run-карточек с клоном, отменой, cleanup (для partial-fail).
 * Обновляется каждые 15 секунд.
 */
import { RefreshCw, Copy, X, Trash2 } from "lucide-react";
import { Badge, Button, Skeleton, EmptyState } from "@/components/ui";
import { Eyebrow } from "@/components/data";
import { haptic, tgConfirm } from "@/lib/tg";
import {
  useCampaignRuns,
  useCloneRun,
  useCancelRun,
  useCleanupRun,
} from "@/lib/api";
import type { CampaignRunSummary } from "@/lib/campaignTypes";
import { RUN_STATUS_LABEL } from "@/lib/campaignTypes";
import { useWizardStore } from "./-wizardStore";
import { cn } from "@/lib/cn";

/** Badge-вариант по статусу run. */
function runBadgeVariant(status: string): "done" | "warning" | "failed" | "neutral" {
  switch (status) {
    case "succeeded":  return "done";
    case "failed":
    case "cancelled":  return "failed";
    case "queued":
    case "uniquifying":
    case "uploading":
    case "creating":   return "warning";
    default:           return "neutral";
  }
}

function formatDateShort(iso: string): string {
  try {
    return new Date(iso).toLocaleString("ru-RU", {
      day: "2-digit",
      month: "2-digit",
      hour: "2-digit",
      minute: "2-digit",
    });
  } catch {
    return iso;
  }
}

interface RunCardProps {
  run: CampaignRunSummary;
  onClone: (run: CampaignRunSummary) => void;
  onCancel: (run: CampaignRunSummary) => void;
  onCleanup: (run: CampaignRunSummary) => void;
}

function RunCard({ run, onClone, onCancel, onCleanup }: RunCardProps) {
  const isActive = !["succeeded", "failed", "cancelled"].includes(run.status);
  const isFailed = run.status === "failed";
  const statusLabel = RUN_STATUS_LABEL[run.status] ?? run.status;

  return (
    <div className="border border-[var(--hairline)] bg-bg-1 rounded-[var(--radius-3)] overflow-hidden">
      {/* Основная строка */}
      <div className="flex items-start justify-between gap-3 px-3.5 py-3">
        <div className="flex-1 min-w-0">
          <div className="flex items-center gap-2 mb-1">
            <Badge variant={runBadgeVariant(run.status)}>{statusLabel}</Badge>
            {run.offer_code && (
              <span className="font-display text-[11px] text-bg-8 truncate">{run.offer_code}</span>
            )}
          </div>
          <p className="font-mono text-[11px] text-bg-7 truncate">{run.id}</p>
          <p className="text-[11px] text-bg-8 mt-1">{formatDateShort(run.created_at)}</p>
        </div>

        {/* Действие: клон */}
        <button
          type="button"
          aria-label="Клонировать запуск"
          onClick={() => onClone(run)}
          className="shrink-0 inline-flex items-center justify-center w-9 h-9 text-bg-8 active:text-accent border border-[var(--hairline)] rounded-[var(--radius-2)]"
        >
          <Copy size={14} strokeWidth={1.8} aria-hidden />
        </button>
      </div>

      {/* Ошибка */}
      {run.error && (
        <div className="px-3.5 pb-2.5">
          <p className="text-[11px] text-[var(--color-danger)] leading-snug line-clamp-2">
            {run.error}
          </p>
        </div>
      )}

      {/* Действия отмены / cleanup */}
      {(isActive || isFailed) && (
        <div className={cn(
          "flex gap-2 px-3.5 pb-3",
          "border-t border-[var(--hairline)] pt-3",
        )}>
          {isActive && (
            <Button
              size="sm"
              variant="secondary"
              onClick={() => onCancel(run)}
              className="flex items-center gap-1"
            >
              <X size={13} strokeWidth={2} aria-hidden />
              Отмена
            </Button>
          )}
          {isFailed && (
            <Button
              size="sm"
              variant="danger"
              onClick={() => onCleanup(run)}
              className="flex items-center gap-1"
            >
              <Trash2 size={13} strokeWidth={1.8} aria-hidden />
              Cleanup
            </Button>
          )}
        </div>
      )}
    </div>
  );
}

export function RunsHistory() {
  const { data: runs, isLoading, isError, refetch } = useCampaignRuns();
  const clone = useCloneRun();
  const cancel = useCancelRun();
  const cleanup = useCleanupRun();
  const { setStep, reset } = useWizardStore();

  async function handleClone(run: CampaignRunSummary) {
    haptic.impact("medium");
    try {
      await clone.mutateAsync({ id: run.id });
      // После клона переходим на визард (шаг identity — конфиг уже заполнен клоном)
      reset();
      setStep("identity");
      haptic.notify("success");
    } catch (err) {
      haptic.notify("error");
      console.error("Clone error", err);
    }
  }

  async function handleCancel(run: CampaignRunSummary) {
    const ok = await tgConfirm(`Отменить запуск ${run.id.slice(0, 8)}…?`);
    if (!ok) return;
    haptic.impact("medium");
    cancel.mutate({ id: run.id });
  }

  async function handleCleanup(run: CampaignRunSummary) {
    const ok = await tgConfirm(`Cleanup Meta-объектов для ${run.id.slice(0, 8)}…?`);
    if (!ok) return;
    haptic.impact("medium");
    cleanup.mutate({ id: run.id });
  }

  const runList = runs ?? [];

  return (
    <div className="flex flex-col gap-3 p-4 pb-8">
      <div className="flex items-center justify-between">
        <Eyebrow>ИСТОРИЯ ЗАПУСКОВ</Eyebrow>
        <button
          type="button"
          aria-label="Обновить историю"
          onClick={() => { haptic.selection(); void refetch(); }}
          disabled={isLoading}
          className="inline-flex items-center justify-center w-9 h-9 text-bg-8 active:text-bg-11 disabled:opacity-40"
        >
          <RefreshCw size={16} strokeWidth={1.8} aria-hidden />
        </button>
      </div>

      {isLoading && (
        <div className="flex flex-col gap-3">
          {Array.from({ length: 3 }, (_, i) => (
            <Skeleton key={i} className="h-[80px]" />
          ))}
        </div>
      )}

      {isError && !isLoading && (
        <EmptyState
          title="Не удалось загрузить историю"
          description="Проверьте соединение и повторите"
        />
      )}

      {!isLoading && !isError && runList.length === 0 && (
        <EmptyState
          title="Запусков ещё нет"
          description="Создайте первую кампанию через визард"
        />
      )}

      {!isLoading && !isError && runList.length > 0 && (
        <div className="flex flex-col gap-3">
          {runList.map((run) => (
            <RunCard
              key={run.id}
              run={run}
              onClone={() => void handleClone(run)}
              onCancel={() => void handleCancel(run)}
              onCleanup={() => void handleCleanup(run)}
            />
          ))}
        </div>
      )}
    </div>
  );
}
