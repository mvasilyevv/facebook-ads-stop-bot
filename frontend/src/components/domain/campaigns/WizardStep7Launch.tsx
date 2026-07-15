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
  ChevronDown,
  Copy,
  Check,
  ShieldCheck,
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
        <div className="font-display text-[10px] tracking-[0.14em] uppercase text-bg-8 mb-1">
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
          retryError={
            launchMut.isError
              ? launchMut.error instanceof Error
                ? launchMut.error.message
                : "Не удалось повторить залив"
              : null
          }
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
  retryError: string | null;
  onFinish: () => void;
}

function RunProgress({
  runId,
  onCleanup,
  cleaningUp,
  cleanupResult,
  onRetry,
  retrying,
  retryError,
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
    <div className="space-y-5 min-w-0">
      {/* Статус-шкала */}
      <ProgressBar status={status} stepIdx={stepIdx} />

      {succeeded && (
        <SuccessSummary ids={run.created_meta_ids ?? {}} onFinish={onFinish} />
      )}

      {!succeeded && (
        <div
          className={cn(
            "flex items-start gap-3 px-4 py-3.5 rounded-[var(--radius-2)] border text-[13px]",
            failed
              ? "bg-danger/10 border-danger/30 text-danger"
              : cancelled
                ? "bg-bg-3 border-[var(--hairline)] text-bg-9"
                : "bg-accent-bg border-accent/30 text-accent",
          )}
          role={failed || cancelled ? "alert" : "status"}
        >
          {failed || cancelled ? (
            <XCircle size={16} className="mt-0.5 shrink-0" />
          ) : (
            <Loader2 size={16} className="mt-0.5 shrink-0 animate-spin" />
          )}
          <div className="min-w-0">
            <div className="font-medium">{RUN_STATUS_LABELS[status]}</div>
            {run.error && (
              <div className="mt-0.5 text-[12px] font-normal opacity-80 break-words">
                {run.error}
              </div>
            )}
          </div>
        </div>
      )}

      {/* Повтор после ошибки — тот же конфиг, без пересоздания (концепты переиспользуются) */}
      {failed && (
        <div className="space-y-2">
          <Button
            variant="primary"
            size="md"
            leftIcon={<RefreshCw size={14} />}
            onClick={onRetry}
            loading={retrying}
          >
            Повторить залив
          </Button>
          {retryError && (
            <div role="alert" className="text-[12px] text-danger break-words">
              {retryError}
            </div>
          )}
        </div>
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

      <TechnicalDetails
        runId={runId}
        progress={succeeded ? null : run.progress}
        ids={run.created_meta_ids ?? {}}
      />
    </div>
  );
}

// ─── ProgressBar ─────────────────────────────────────────────────────────────

function ProgressBar({ status, stepIdx }: { status: RunStatus; stepIdx: number }) {
  const failed = status === "failed";
  const succeeded = status === "succeeded";

  return (
    <div
      className="grid grid-cols-5 gap-1.5"
      aria-label={`Прогресс залива: ${RUN_STATUS_LABELS[status]}`}
    >
      {PROGRESS_STEPS.map((s, i) => {
        const done = !failed && (i < stepIdx || (succeeded && i === stepIdx));
        const current = !failed && !succeeded && i === stepIdx;
        const future = !failed && i > stepIdx;
        const state = done ? "done" : current ? "current" : failed ? "failed" : "future";

        return (
          <div key={s} className="min-w-0 flex flex-col items-center gap-1.5" data-state={state}>
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
                "font-display text-[8px] sm:text-[9px] tracking-[0.08em] sm:tracking-wider uppercase text-center truncate w-full",
                done ? "text-success" : current ? "text-accent" : "text-bg-8",
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

// ─── Success summary ──────────────────────────────────────────────────────────

const META_GROUPS = [
  { key: "campaigns", label: "Кампании" },
  { key: "adsets", label: "Адсеты" },
  { key: "ads", label: "Объявления" },
  { key: "creatives", label: "Креативы" },
] as const;

function normalizeMetaIds(value: unknown): string[] {
  if (Array.isArray(value)) return value.flatMap(normalizeMetaIds);
  if (typeof value === "string") {
    return value
      .split(",")
      .map((id) => id.trim())
      .filter(Boolean);
  }
  if (typeof value === "number" || typeof value === "bigint") return [String(value)];
  if (value && typeof value === "object") return Object.values(value).flatMap(normalizeMetaIds);
  return [];
}

function SuccessSummary({ ids, onFinish }: { ids: Record<string, unknown>; onFinish: () => void }) {
  const groups = META_GROUPS.map((group) => ({
    ...group,
    ids: normalizeMetaIds(ids[group.key]),
  }));
  const campaignIds = groups.find((group) => group.key === "campaigns")?.ids ?? [];
  const adsManagerHref = `https://www.facebook.com/adsmanager/manage/campaigns?ids=${encodeURIComponent(campaignIds.join(","))}`;

  return (
    <section className="relative overflow-hidden border border-success/35 bg-[linear-gradient(135deg,rgba(44,194,139,0.13),rgba(44,194,139,0.035)_62%,transparent)] rounded-[var(--radius-3)]">
      <div className="absolute -right-16 -top-20 size-52 rounded-full bg-success/10 blur-3xl pointer-events-none" />
      <div className="relative p-5 sm:p-6">
        <div className="flex items-start gap-3.5">
          <div className="size-10 shrink-0 rounded-full border border-success/35 bg-success/10 flex items-center justify-center text-success">
            <CheckCircle size={20} />
          </div>
          <div className="min-w-0 flex-1">
            <div className="flex flex-wrap items-center gap-2">
              <h3 className="font-display text-[17px] font-medium text-bg-11 m-0">
                Залив завершён
              </h3>
              <span className="inline-flex items-center gap-1 rounded-full border border-success/25 bg-success/10 px-2 py-0.5 font-display text-[9px] tracking-wider uppercase text-success">
                <ShieldCheck size={10} />
                PAUSED · без спенда
              </span>
            </div>
            <p className="mt-1 text-[12.5px] leading-relaxed text-bg-9">
              Объекты созданы в Meta. Проверьте кампанию в Ads Manager перед включением.
            </p>
          </div>
        </div>

        <div className="mt-5 grid grid-cols-2 sm:grid-cols-4 border-y border-success/15 divide-x divide-y sm:divide-y-0 divide-success/15">
          {groups.map((group) => (
            <div key={group.key} className="px-3 py-3 first:pl-0 sm:first:pl-0 last:pr-0">
              <div className="font-mono text-[19px] leading-none tabular-nums text-bg-11">
                {group.ids.length}
              </div>
              <div className="mt-1 font-display text-[9px] tracking-wider uppercase text-bg-8">
                {group.label}
              </div>
            </div>
          ))}
        </div>

        <div className="mt-5 flex flex-col-reverse sm:flex-row sm:items-center gap-2.5">
          <Button
            variant="primary"
            size="lg"
            leftIcon={<CheckCircle size={15} />}
            onClick={onFinish}
            className="w-full sm:w-auto"
          >
            Создать новый залив
          </Button>
          {campaignIds.length > 0 && (
            <a
              href={adsManagerHref}
              target="_blank"
              rel="noopener noreferrer"
              className="h-10 w-full sm:w-auto px-4 inline-flex items-center justify-center gap-2 rounded-[var(--radius-2)] border border-[var(--hairline-strong)] bg-bg-2 text-[13.5px] font-medium text-bg-11 hover:bg-bg-3 hover:border-bg-7 transition-colors focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-accent"
            >
              <ExternalLink size={14} />
              Открыть в Ads Manager
            </a>
          )}
        </div>
      </div>
    </section>
  );
}

// ─── Technical details ────────────────────────────────────────────────────────

function CopyButton({ value, label }: { value: string; label: string }) {
  const [copied, setCopied] = useState(false);

  const handleCopy = async () => {
    if (!navigator.clipboard) return;
    await navigator.clipboard.writeText(value);
    setCopied(true);
    window.setTimeout(() => setCopied(false), 1500);
  };

  return (
    <button
      type="button"
      onClick={() => void handleCopy()}
      aria-label={`Скопировать ${label}`}
      className="size-7 shrink-0 rounded-[var(--radius-1)] inline-flex items-center justify-center text-bg-8 hover:text-bg-11 hover:bg-bg-3 transition-colors focus-visible:outline-2 focus-visible:outline-accent"
    >
      {copied ? <Check size={12} className="text-success" /> : <Copy size={12} />}
    </button>
  );
}

function TechnicalDetails({
  runId,
  progress,
  ids,
}: {
  runId: string;
  progress: Record<string, unknown> | null;
  ids: Record<string, unknown>;
}) {
  const groups = META_GROUPS.map((group) => ({
    ...group,
    ids: normalizeMetaIds(ids[group.key]),
  })).filter((group) => group.ids.length > 0);
  const hasProgress = progress && Object.keys(progress).length > 0;

  return (
    <details className="group border-t border-[var(--hairline)] pt-1">
      <summary className="list-none cursor-pointer py-2.5 flex items-center gap-2 text-[11px] text-bg-8 hover:text-bg-10 transition-colors focus-visible:outline-2 focus-visible:outline-accent rounded-[var(--radius-1)]">
        <ChevronDown
          size={13}
          className="transition-transform duration-150 group-open:rotate-180"
        />
        Технические детали
        <span className="ml-auto font-mono text-[10px] text-bg-8">run {runId.slice(0, 8)}</span>
      </summary>

      <div className="pb-2 pt-1 space-y-3">
        <div className="flex items-center gap-2 rounded-[var(--radius-2)] bg-bg-1 px-3 py-2">
          <span className="font-display text-[9px] tracking-wider uppercase text-bg-8">Run ID</span>
          <code className="min-w-0 flex-1 truncate font-mono text-[11px] text-bg-10" title={runId}>
            {runId}
          </code>
          <CopyButton value={runId} label="Run ID" />
        </div>

        {groups.map((group) => {
          const value = group.ids.join(", ");
          return (
            <div key={group.key} className="rounded-[var(--radius-2)] bg-bg-1 px-3 py-2.5">
              <div className="flex items-center gap-2">
                <span className="font-display text-[9px] tracking-wider uppercase text-bg-8">
                  {group.label}
                </span>
                <span className="text-[10px] text-bg-8">{group.ids.length}</span>
                <CopyButton value={value} label={`ID: ${group.label}`} />
              </div>
              <code className="mt-1.5 block font-mono text-[10.5px] leading-relaxed text-bg-9 break-all">
                {value}
              </code>
            </div>
          );
        })}

        {hasProgress && (
          <div className="rounded-[var(--radius-2)] bg-bg-1 divide-y divide-[var(--hairline)]">
            {Object.entries(progress).map(([key, value]) => (
              <div key={key} className="grid grid-cols-[auto,minmax(0,1fr)] gap-3 px-3 py-2">
                <span className="font-mono text-[10.5px] text-bg-8">{key}</span>
                <span className="min-w-0 text-right font-mono text-[10.5px] text-bg-9 break-all">
                  {String(value)}
                </span>
              </div>
            ))}
          </div>
        )}
      </div>
    </details>
  );
}
