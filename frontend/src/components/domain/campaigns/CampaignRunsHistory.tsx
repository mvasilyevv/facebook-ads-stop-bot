/**
 * История запусков — таблица с RunSummaryOut.
 *
 * Фильтрация по статусу, клон запуска, cleanup при ошибке.
 * Переход к деталям (inline раскрытие или отдельная ссылка).
 */

import { type FC, useState } from "react";
import { Copy, Trash2, RefreshCw, ChevronDown, ChevronRight } from "lucide-react";
import { cn } from "@/lib/utils/cn";
import { Button } from "@/components/ui/Button";
import { Skeleton } from "@/components/ui/Skeleton";
import { EmptyState } from "@/components/ui/EmptyState";
import { ErrorState } from "@/components/ui/ErrorState";
import { Select } from "@/components/ui/Select";
import { toast } from "@/components/ui/Toast";
import {
  useRuns,
  useRunDetail,
  useCloneRun,
  useCancelRun,
  useCleanupRun,
  RUN_STATUS_LABELS,
  CANCELLABLE_RUN_STATUSES,
  type RunSummaryOut,
  type RunStatus,
} from "@/lib/api/campaigns";
import { useQueryClient } from "@tanstack/react-query";

// ─── Цвета статуса ────────────────────────────────────────────────────────────

function statusColor(status: RunStatus): string {
  switch (status) {
    case "succeeded":
      return "text-success";
    case "failed":
      return "text-danger";
    case "cancelled":
      return "text-bg-8";
    case "creating":
    case "uploading":
    case "uniquifying":
      return "text-accent";
    default:
      return "text-bg-8";
  }
}

// ─── Компонент ────────────────────────────────────────────────────────────────

interface CampaignRunsHistoryProps {
  /** Callback при «клон» — открывает визард с clone_run_id */
  onClone?: (runId: string) => void;
}

const STATUS_FILTER_OPTIONS = [
  { value: "", label: "Все статусы" },
  { value: "queued", label: RUN_STATUS_LABELS.queued },
  { value: "uniquifying", label: RUN_STATUS_LABELS.uniquifying },
  { value: "uploading", label: RUN_STATUS_LABELS.uploading },
  { value: "creating", label: RUN_STATUS_LABELS.creating },
  { value: "succeeded", label: RUN_STATUS_LABELS.succeeded },
  { value: "failed", label: RUN_STATUS_LABELS.failed },
  { value: "cancelled", label: RUN_STATUS_LABELS.cancelled },
];

export const CampaignRunsHistory: FC<CampaignRunsHistoryProps> = ({ onClone }) => {
  const [statusFilter, setStatusFilter] = useState("");
  const qc = useQueryClient();

  const { data, isLoading, isError, error, refetch } = useRuns({
    status: statusFilter || undefined,
    limit: 50,
  });

  const runs = data?.data ?? [];
  const total = data?.total ?? 0;

  const handleRefresh = () => void refetch();

  if (isLoading) {
    return (
      <div className="space-y-2">
        {[1, 2, 3].map((i) => (
          <Skeleton key={i} className="h-12 w-full" />
        ))}
      </div>
    );
  }

  if (isError) {
    return <ErrorState error={error} onRetry={() => void refetch()} />;
  }

  return (
    <div className="space-y-4">
      {/* Toolbar */}
      <div className="flex items-center justify-between gap-3">
        <div className="flex items-center gap-2">
          <div style={{ width: 200 }}>
            <Select
              options={STATUS_FILTER_OPTIONS}
              value={statusFilter}
              onChange={(e) => setStatusFilter(e.target.value)}
            />
          </div>
          <span className="text-[12px] text-bg-8">
            {total > 0 ? `${total} запусков` : "нет запусков"}
          </span>
        </div>
        <Button
          variant="secondary"
          size="sm"
          leftIcon={<RefreshCw size={13} />}
          onClick={handleRefresh}
          loading={isLoading}
        >
          Обновить
        </Button>
      </div>

      {/* Список */}
      {runs.length === 0 ? (
        <EmptyState
          icon={<RefreshCw size={28} />}
          title="Запусков нет"
          description="Создайте первую кампанию через визард."
        />
      ) : (
        <div className="border border-[var(--hairline)] rounded-[var(--radius-3)] overflow-hidden">
          {/* Шапка */}
          <div className="grid grid-cols-[1fr_120px_140px_100px] gap-3 px-4 py-2.5 bg-bg-2 border-b border-[var(--hairline)]">
            {["Оффер / ID", "Статус", "Создан", "Действия"].map((h) => (
              <div
                key={h}
                className="font-display text-[10px] tracking-[0.12em] uppercase text-bg-8"
              >
                {h}
              </div>
            ))}
          </div>

          {/* Строки */}
          <div className="divide-y divide-[var(--hairline)]">
            {runs.map((run) => (
              <RunRow
                key={run.id}
                run={run}
                onClone={() => {
                  onClone?.(run.id);
                }}
                onRefresh={() => qc.invalidateQueries({ queryKey: ["campaigns", "runs"] })}
              />
            ))}
          </div>
        </div>
      )}
    </div>
  );
};

// ─── RunRow ───────────────────────────────────────────────────────────────────

interface RunRowProps {
  run: RunSummaryOut;
  onClone: () => void;
  onRefresh: () => void;
}

const RunRow: FC<RunRowProps> = ({ run, onClone, onRefresh }) => {
  const [expanded, setExpanded] = useState(false);
  const cloneMut = useCloneRun();
  const cancelMut = useCancelRun();
  const cleanupMut = useCleanupRun();

  const canCancel = CANCELLABLE_RUN_STATUSES.includes(run.status as RunStatus);
  const failed = run.status === "failed";
  const createdAt = new Date(run.created_at).toLocaleString("ru-RU", {
    month: "short",
    day: "numeric",
    hour: "2-digit",
    minute: "2-digit",
  });

  const handleClone = async () => {
    try {
      const result = await cloneMut.mutateAsync(run.id);
      toast.success(`Клон создан: run_id=${result.run_id}`);
      onClone();
      onRefresh();
    } catch (e) {
      toast.error("Ошибка клонирования", e instanceof Error ? e.message : String(e));
    }
  };

  const handleCancel = async () => {
    try {
      await cancelMut.mutateAsync(run.id);
      toast.success("Запуск отменён");
      onRefresh();
    } catch (e) {
      toast.error("Ошибка отмены", e instanceof Error ? e.message : String(e));
    }
  };

  const handleCleanup = async () => {
    try {
      const result = await cleanupMut.mutateAsync(run.id);
      toast.info(result.detail);
    } catch (e) {
      toast.error("Ошибка cleanup", e instanceof Error ? e.message : String(e));
    }
  };

  return (
    <>
      <div className="grid grid-cols-[1fr_120px_140px_100px] gap-3 px-4 py-3 items-center hover:bg-bg-2 transition-colors">
        {/* Оффер + id */}
        <div className="min-w-0">
          <div className="flex items-center gap-1.5">
            <button
              type="button"
              onClick={() => setExpanded((v) => !v)}
              className="text-bg-8 hover:text-bg-11 transition-colors"
              aria-expanded={expanded}
              aria-label="Развернуть детали"
            >
              {expanded ? <ChevronDown size={13} /> : <ChevronRight size={13} />}
            </button>
            <span className="font-medium text-[13px] text-bg-11 truncate">
              {run.offer_code ?? "—"}
            </span>
          </div>
          <div className="text-[10px] font-mono text-bg-8 ml-5 truncate" title={run.id}>
            {run.id}
          </div>
          {run.error && (
            <div
              className="text-[11px] text-danger ml-5 truncate"
              title={run.error}
              role="alert"
            >
              {run.error}
            </div>
          )}
        </div>

        {/* Статус */}
        <div
          className={cn("font-display text-[11px] tracking-wider uppercase", statusColor(run.status as RunStatus))}
        >
          {RUN_STATUS_LABELS[run.status as RunStatus] ?? run.status}
        </div>

        {/* Дата */}
        <div className="text-[12px] text-bg-8">{createdAt}</div>

        {/* Действия */}
        <div className="flex items-center gap-1">
          <button
            type="button"
            onClick={() => void handleClone()}
            disabled={cloneMut.isPending}
            title="Клонировать"
            className="size-7 flex items-center justify-center rounded-[var(--radius-1)] text-bg-8 hover:text-bg-11 hover:bg-bg-3 transition-colors disabled:opacity-40"
          >
            <Copy size={13} />
          </button>
          {canCancel && (
            <button
              type="button"
              onClick={() => void handleCancel()}
              disabled={cancelMut.isPending}
              title="Отменить"
              className="size-7 flex items-center justify-center rounded-[var(--radius-1)] text-bg-8 hover:text-danger hover:bg-danger/10 transition-colors disabled:opacity-40"
            >
              <Trash2 size={13} />
            </button>
          )}
          {failed && (
            <button
              type="button"
              onClick={() => void handleCleanup()}
              disabled={cleanupMut.isPending}
              title="Cleanup Meta-объектов"
              className="size-7 flex items-center justify-center rounded-[var(--radius-1)] text-bg-8 hover:text-warning hover:bg-warning/10 transition-colors disabled:opacity-40"
            >
              <RefreshCw size={13} />
            </button>
          )}
        </div>
      </div>

      {/* Expandable: детали run */}
      {expanded && <RunExpandedDetails runId={run.id} />}
    </>
  );
};

// ─── RunExpandedDetails ───────────────────────────────────────────────────────

function RunExpandedDetails({ runId }: { runId: string }) {
  const { data: run, isLoading } = useRunDetail(runId);

  if (isLoading) return <Skeleton className="h-16 mx-4 mb-3 mt-1" />;
  if (!run) return null;

  const hasMetaIds = Object.keys(run.created_meta_ids ?? {}).length > 0;

  return (
    <div className="mx-4 mb-3 mt-0 border border-[var(--hairline)] rounded-[var(--radius-2)] bg-bg-2 p-3 text-[12px] text-bg-8 space-y-2">
      {/* Прогресс */}
      {run.progress && Object.keys(run.progress).length > 0 && (
        <div>
          <span className="font-display text-[9px] uppercase tracking-wider text-bg-8 block mb-1">
            Прогресс
          </span>
          {Object.entries(run.progress).map(([k, v]) => (
            <div key={k} className="font-mono text-[11px] flex gap-2">
              <span className="text-bg-8">{k}:</span>
              <span className="text-bg-10">{String(v)}</span>
            </div>
          ))}
        </div>
      )}
      {/* Meta IDs */}
      {hasMetaIds && (
        <div>
          <span className="font-display text-[9px] uppercase tracking-wider text-bg-8 block mb-1">
            Meta IDs
          </span>
          {Object.entries(run.created_meta_ids).map(([k, v]) => (
            <div key={k} className="font-mono text-[11px] flex gap-2">
              <span className="text-bg-8">{k}:</span>
              <span className="text-bg-10">{String(v)}</span>
            </div>
          ))}
        </div>
      )}
      {/* Idempotency key */}
      {run.idempotency_key && (
        <div className="font-mono text-[10px] text-bg-6">ikey: {run.idempotency_key}</div>
      )}
    </div>
  );
}
