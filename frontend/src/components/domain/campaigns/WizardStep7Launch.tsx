/**
 * Шаг 7 — Запуск и прогресс.
 *
 * - Кнопка «Залить кампанию» → POST /tools/campaigns/launch → run_id
 * - Поллинг GET /runs/{run_id} каждые 3 сек до терминального статуса
 * - Прогресс-шкала по статусу: queued → uniquifying → uploading → creating → succeeded
 * - При succeeded: список созданных Meta-ID
 * - При failed: ошибка + кнопка cleanup
 */

import { type FC, useState, useEffect } from "react";
import {
  Rocket,
  CheckCircle,
  XCircle,
  Loader2,
  ExternalLink,
  Trash2,
  RefreshCw,
} from "lucide-react";
import { cn } from "@/lib/utils/cn";
import { Button } from "@/components/ui/Button";
import { Skeleton } from "@/components/ui/Skeleton";
import {
  useLaunchCampaign,
  useRunDetail,
  useCleanupRun,
  RUN_STATUS_LABELS,
  TERMINAL_RUN_STATUSES,
  type CampaignConfig,
  type RunStatus,
} from "@/lib/api/campaigns";

// ─── Props ────────────────────────────────────────────────────────────────────

interface WizardStep7LaunchProps {
  config: CampaignConfig;
  presetId?: string | null;
  runId: string | null;
  onRunId: (id: string) => void;
  /** Завершить визард (сброс к шагу 1). Кнопка «Готово» на успешном заливе. */
  onFinish: () => void;
}

// ─── Шаги прогресса ──────────────────────────────────────────────────────────

const PROGRESS_STEPS: RunStatus[] = [
  "queued",
  "uniquifying",
  "uploading",
  "creating",
  "succeeded",
];

const STATUS_STEP_INDEX: Partial<Record<RunStatus, number>> = {
  queued: 0,
  uniquifying: 1,
  uploading: 2,
  creating: 3,
  succeeded: 4,
};

// ─── Компонент ────────────────────────────────────────────────────────────────

export const WizardStep7Launch: FC<WizardStep7LaunchProps> = ({
  config,
  presetId,
  runId,
  onRunId,
  onFinish,
}) => {
  const launchMut = useLaunchCampaign();
  const cleanupMut = useCleanupRun();

  const handleLaunch = () => {
    launchMut.mutate(
      { config, preset_id: presetId ?? null },
      {
        onSuccess: (out) => onRunId(out.run_id),
      },
    );
  };

  // Повтор после ошибки: тот же config, но СВЕЖИЙ idempotency_key (иначе launch вернёт
  // тот же упавший run по ON CONFLICT). Концепты переиспользуются — воркер не чистит
  // upload-папку при ошибке (только при успехе), так что заново загружать не нужно.
  const handleRetry = () => {
    launchMut.mutate(
      { config, preset_id: presetId ?? null, idempotency_key: crypto.randomUUID() },
      {
        onSuccess: (out) => onRunId(out.run_id),
      },
    );
  };

  return (
    <div className="space-y-6">
      {/* Заголовок */}
      <div>
        <div className="font-display text-[10px] tracking-[0.14em] uppercase text-bg-7 mb-1">
          ШАГ 7 · ЗАПУСК
        </div>
        <h2 className="font-display text-[20px] font-medium text-bg-11 leading-tight m-0">
          Запуск залива
        </h2>
        <p className="text-[13px] text-bg-9 mt-1">
          Кампании создаются в статусе PAUSED — спенда не будет. Снимешь паузу в Ads Manager.
        </p>
      </div>

      {/* Кнопка запуска (до run_id) */}
      {!runId && (
        <div className="border border-[var(--hairline)] rounded-[var(--radius-3)] p-6 bg-bg-1 text-center space-y-4">
          <div className="size-14 mx-auto rounded-full bg-accent/10 flex items-center justify-center">
            <Rocket size={24} className="text-accent" />
          </div>
          <div>
            <div className="font-display text-[15px] font-medium text-bg-11">
              Готово к заливу?
            </div>
            <div className="text-[12px] text-bg-8 mt-1">
              Оффер: <b>{config.offer_code}</b> · Дата: <b>{config.start_date}</b> ·
              Кампаний: <b>{config.campaigns.length}</b>
            </div>
          </div>
          {launchMut.isError && (
            <div
              role="alert"
              className="text-[12px] text-danger bg-danger/10 border border-danger/30 rounded-[var(--radius-2)] px-3 py-2"
            >
              {launchMut.error instanceof Error
                ? launchMut.error.message
                : "Ошибка запуска залива"}
            </div>
          )}
          <Button
            variant="primary"
            size="lg"
            leftIcon={<Rocket size={16} />}
            onClick={handleLaunch}
            loading={launchMut.isPending}
          >
            Залить кампанию
          </Button>
        </div>
      )}

      {/* Прогресс (после run_id) */}
      {runId && (
        <RunProgress
          runId={runId}
          onCleanup={() => cleanupMut.mutate(runId)}
          cleaningUp={cleanupMut.isPending}
          cleanupResult={cleanupMut.data}
          onRetry={handleRetry}
          retrying={launchMut.isPending}
          onFinish={onFinish}
        />
      )}
    </div>
  );
};

// ─── RunProgress ──────────────────────────────────────────────────────────────

interface RunProgressProps {
  runId: string;
  onCleanup: () => void;
  cleaningUp: boolean;
  cleanupResult?: { meta_ids: Record<string, unknown>; detail: string };
  onRetry: () => void;
  retrying: boolean;
  onFinish: () => void;
}

function RunProgress({
  runId,
  onCleanup,
  cleaningUp,
  cleanupResult,
  onRetry,
  retrying,
  onFinish,
}: RunProgressProps) {
  // Поллинг каждые 3 сек пока статус не терминальный
  const [interval, setInterval_] = useState<number | false>(3000);

  const { data: run, isLoading } = useRunDetail(runId, {
    refetchInterval: interval,
  });

  // Остановить поллинг при достижении терминального статуса
  useEffect(() => {
    const runStatus = run?.status as RunStatus | undefined;
    if (runStatus && TERMINAL_RUN_STATUSES.includes(runStatus)) {
      setInterval_(false);
    }
  }, [run?.status]);

  if (isLoading && !run) {
    return (
      <div className="space-y-3">
        <Skeleton className="h-6 w-1/2" />
        <Skeleton className="h-2 w-full" />
        <Skeleton className="h-16 w-full" />
      </div>
    );
  }

  if (!run) return null;

  const status = run.status as RunStatus;
  const stepIdx = STATUS_STEP_INDEX[status] ?? (status === "failed" ? -1 : 0);
  const succeeded = status === "succeeded";
  const failed = status === "failed";
  const cancelled = status === "cancelled";

  return (
    <div className="space-y-5">
      {/* run_id badge */}
      <div className="text-[11px] text-bg-7">
        run_id:{" "}
        <span className="font-mono text-bg-9">{runId}</span>
      </div>

      {/* Статус-шкала */}
      <ProgressBar status={status} stepIdx={stepIdx} />

      {/* Статус текущий */}
      <div
        className={cn(
          "flex items-center gap-2 px-4 py-3 rounded-[var(--radius-2)] border text-[13px] font-medium",
          succeeded
            ? "bg-success/10 border-success/30 text-success"
            : failed
              ? "bg-danger/10 border-danger/30 text-danger"
              : cancelled
                ? "bg-bg-3 border-[var(--hairline)] text-bg-8"
                : "bg-accent-bg border-accent/30 text-accent",
        )}
        role={failed || cancelled ? "alert" : undefined}
      >
        {succeeded ? (
          <CheckCircle size={16} />
        ) : failed ? (
          <XCircle size={16} />
        ) : (
          <Loader2 size={16} className="animate-spin" />
        )}
        {RUN_STATUS_LABELS[status]}
        {run.error && <span className="ml-2 text-[12px] font-normal opacity-80">— {run.error}</span>}
      </div>

      {/* Повтор после ошибки — тот же конфиг, без пересоздания (концепты переиспользуются) */}
      {failed && (
        <Button
          variant="primary"
          size="md"
          leftIcon={<RefreshCw size={14} />}
          onClick={onRetry}
          loading={retrying}
        >
          Повторить залив
        </Button>
      )}

      {/* Готово — завершить визард после успеха (сброс к шагу 1, можно начать новый залив) */}
      {succeeded && (
        <Button
          variant="primary"
          size="md"
          leftIcon={<CheckCircle size={14} />}
          onClick={onFinish}
        >
          Готово — начать новый залив
        </Button>
      )}

      {/* Прогресс-детали (jsonb) */}
      {run.progress && Object.keys(run.progress).length > 0 && (
        <ProgressDetails progress={run.progress} />
      )}

      {/* Созданные Meta-ID при успехе */}
      {succeeded && run.created_meta_ids && Object.keys(run.created_meta_ids).length > 0 && (
        <MetaIdsBlock ids={run.created_meta_ids} />
      )}

      {/* Cleanup при ошибке с частичным созданием */}
      {(failed || cancelled) &&
        run.created_meta_ids &&
        Object.keys(run.created_meta_ids).length > 0 &&
        !cleanupResult && (
          <div className="border border-[var(--hairline)] rounded-[var(--radius-2)] p-4 bg-bg-2 space-y-2">
            <div className="text-[12px] text-bg-8">
              Часть объектов была создана в Meta до ошибки. Запросите список для ручного сноса.
            </div>
            <Button
              variant="secondary"
              size="sm"
              leftIcon={<Trash2 size={13} />}
              onClick={onCleanup}
              loading={cleaningUp}
            >
              Показать список для cleanup
            </Button>
          </div>
        )}

      {/* Результат cleanup */}
      {cleanupResult && (
        <div className="border border-[var(--hairline)] rounded-[var(--radius-2)] p-4 bg-bg-2 text-[12px] text-bg-9">
          {cleanupResult.detail}
          {Object.keys(cleanupResult.meta_ids).length > 0 && (
            <pre className="mt-2 font-mono text-[11px] text-bg-8 overflow-auto">
              {JSON.stringify(cleanupResult.meta_ids, null, 2)}
            </pre>
          )}
        </div>
      )}
    </div>
  );
}

// ─── ProgressBar ─────────────────────────────────────────────────────────────

function ProgressBar({ status, stepIdx }: { status: RunStatus; stepIdx: number }) {
  const failed = status === "failed";

  return (
    <div className="flex items-center gap-1">
      {PROGRESS_STEPS.map((s, i) => {
        const done = !failed && i < stepIdx;
        const current = !failed && i === stepIdx;
        const future = !failed && i > stepIdx;

        return (
          <div key={s} className="flex-1 flex flex-col items-center gap-1">
            <div
              className={cn(
                "h-1 w-full rounded-full transition-colors duration-300",
                failed && i <= stepIdx
                  ? "bg-danger"
                  : done
                    ? "bg-success"
                    : current
                      ? "bg-accent"
                      : future
                        ? "bg-bg-4"
                        : "bg-bg-4",
              )}
            />
            <span
              className={cn(
                "font-display text-[9px] tracking-wider uppercase text-center",
                done ? "text-success" : current ? "text-accent" : "text-bg-7",
              )}
            >
              {RUN_STATUS_LABELS[s]}
            </span>
          </div>
        );
      })}
    </div>
  );
}

// ─── ProgressDetails ──────────────────────────────────────────────────────────

function ProgressDetails({ progress }: { progress: Record<string, unknown> }) {
  return (
    <div className="border border-[var(--hairline)] rounded-[var(--radius-2)] divide-y divide-[var(--hairline)]">
      {Object.entries(progress).map(([k, v]) => (
        <div key={k} className="flex items-center px-3 py-1.5 gap-2 text-[12px]">
          <span className="text-bg-7 font-mono">{k}</span>
          <span className="text-bg-10 ml-auto font-mono">{String(v)}</span>
        </div>
      ))}
    </div>
  );
}

// ─── MetaIdsBlock ─────────────────────────────────────────────────────────────

function MetaIdsBlock({ ids }: { ids: Record<string, unknown> }) {
  return (
    <div className="border border-success/30 bg-success/5 rounded-[var(--radius-2)] p-4">
      <div className="font-display text-[10px] tracking-wider uppercase text-success mb-2">
        СОЗДАННЫЕ META-ОБЪЕКТЫ
      </div>
      <div className="space-y-1">
        {Object.entries(ids).map(([k, v]) => (
          <div key={k} className="flex items-center gap-2 text-[12px]">
            <span className="text-bg-7 font-display uppercase text-[10px]">{k}</span>
            <span className="font-mono text-bg-10">{String(v)}</span>
            <a
              href={`https://www.facebook.com/adsmanager/manage/campaigns?ids=${String(v)}`}
              target="_blank"
              rel="noopener noreferrer"
              className="ml-auto text-accent hover:underline flex items-center gap-1 text-[11px]"
            >
              <ExternalLink size={10} />
              Открыть
            </a>
          </div>
        ))}
      </div>
    </div>
  );
}
