/**
 * Шаг 7 — Запуск и прогресс.
 *
 * - Кнопка «Залить кампанию» → POST /tools/campaigns/launch → run_id
 * - Progress updates arrive through the PostgreSQL-authoritative operator stream
 * - Прогресс-шкала по статусу: queued → uniquifying → uploading → creating → succeeded
 * - При succeeded: список созданных Meta-ID
 * - При failed: ошибка + created IDs для ручной сверки
 */

import { type FC, useState } from "react";
import { campaignRunFailurePresentation, campaignRunRequiresManualReview } from "@fb/shared";
import {
  Rocket,
  CheckCircle,
  XCircle,
  Loader2,
  ExternalLink,
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
  RUN_STATUS_LABELS,
  type CampaignConfig,
  type RunStatus,
} from "@/lib/api/campaigns";
import { CampaignRunManualReview } from "./CampaignRunManualReview";

// ─── Props ────────────────────────────────────────────────────────────────────

interface WizardStep7LaunchProps {
  config: CampaignConfig;
  presetId?: string | null;
  draftRevision: number | null;
  draftSyncState: "loading" | "idle" | "saving" | "saved" | "error" | "conflict";
  runId: string | null;
  onRunId: (id: string) => void;
  onDraftCleared: () => void;
  /** Завершить визард (сброс к шагу 1). Кнопка «Готово» на успешном заливе. */
  onFinish: () => void;
}

// ─── Шаги прогресса ──────────────────────────────────────────────────────────

const PROGRESS_STEPS: RunStatus[] = ["queued", "uniquifying", "uploading", "creating", "succeeded"];

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
  draftRevision,
  draftSyncState,
  runId,
  onRunId,
  onDraftCleared,
  onFinish,
}) => {
  const launchMut = useLaunchCampaign();

  const handleLaunch = () => {
    launchMut.mutate(
      { config, preset_id: presetId ?? null, draft_revision: draftRevision },
      {
        onSuccess: (out) => {
          if (out.draft_cleared) onDraftCleared();
          onRunId(out.run_id);
        },
      },
    );
  };

  const immutableDraftReady = draftRevision !== null && draftSyncState === "saved";

  return (
    <div className="space-y-6">
      {/* Заголовок */}
      <div>
        <div className="font-display text-[12px] tracking-[0.14em] uppercase text-bg-8 mb-1">
          ШАГ 7 · ЗАПУСК
        </div>
        <h2 className="font-display text-[20px] font-medium text-bg-11 leading-tight m-0">
          Запуск залива
        </h2>
        <p className="text-[13px] text-bg-9 mt-1">
          Кампания, ad set и ad создаются PAUSED — спенда не будет до ручного review.
        </p>
      </div>

      {/* Кнопка запуска (до run_id) */}
      {!runId && (
        <div className="border border-[var(--color-hairline)] rounded-[var(--radius-3)] p-6 bg-bg-1 text-center space-y-4">
          <div className="size-14 mx-auto rounded-full bg-accent/10 flex items-center justify-center">
            <Rocket size={24} className="text-accent" />
          </div>
          <div>
            <div className="font-display text-[15px] font-medium text-bg-11">
              Поставить подтверждённый план в очередь?
            </div>
            <div className="text-[12px] text-bg-8 mt-1">
              Оффер: <b>{config.offer_code}</b> · Дата:{" "}
              <b>{config.start_date || "следующий день кабинета"}</b> · Кампаний:{" "}
              <b>{config.campaigns.length}</b>
            </div>
          </div>
          {launchMut.isError && (
            <div
              role="alert"
              className="text-[12px] text-danger bg-danger/10 border border-danger/30 rounded-[var(--radius-2)] px-3 py-2"
            >
              Не удалось поставить запуск в очередь. Проверьте соединение и повторите попытку.
            </div>
          )}
          {!immutableDraftReady ? (
            <div role="status" className="text-[12px] text-warning">
              Ждём сохранения точной версии серверного черновика.
            </div>
          ) : null}
          <Button
            variant="primary"
            size="lg"
            leftIcon={<Rocket size={16} />}
            onClick={handleLaunch}
            loading={launchMut.isPending}
            disabled={!immutableDraftReady}
          >
            Поставить в очередь
          </Button>
        </div>
      )}

      {/* Прогресс (после run_id) */}
      {runId && <RunProgress runId={runId} onFinish={onFinish} />}
    </div>
  );
};

// ─── RunProgress ──────────────────────────────────────────────────────────────

interface RunProgressProps {
  runId: string;
  onFinish: () => void;
}

function RunProgress({ runId, onFinish }: RunProgressProps) {
  const { data: run, isLoading } = useRunDetail(runId);

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
  const manualReviewRequired = campaignRunRequiresManualReview(run);
  const failure = campaignRunFailurePresentation(run);

  return (
    <div className="space-y-5 min-w-0">
      {/* Статус-шкала */}
      <ProgressBar status={status} stepIdx={stepIdx} />

      {succeeded && <SuccessSummary ids={run.created_meta_ids ?? {}} onFinish={onFinish} />}

      {!succeeded && (
        <div
          className={cn(
            "flex items-start gap-3 px-4 py-3.5 rounded-[var(--radius-2)] border text-[13px]",
            failed
              ? "bg-danger/10 border-danger/30 text-danger"
              : cancelled
                ? "bg-bg-3 border-[var(--color-hairline)] text-bg-9"
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
            {failure ? (
              <div className="mt-0.5 text-[12px] font-normal opacity-80 break-words">
                <strong>{failure.title}.</strong> {failure.reason} Следующий шаг:{" "}
                {failure.action.label}.
              </div>
            ) : cancelled ? (
              <div className="mt-0.5 text-[12px] font-normal opacity-80 break-words">
                Запуск отменён. Перед новым запуском обновите данные и проверьте созданные объекты.
              </div>
            ) : null}
          </div>
        </div>
      )}

      {manualReviewRequired ? (
        <CampaignRunManualReview createdMetaIds={run.created_meta_ids ?? {}} />
      ) : null}

      <TechnicalDetails ids={run.created_meta_ids ?? {}} />
    </div>
  );
}

// ─── ProgressBar ─────────────────────────────────────────────────────────────

function ProgressBar({ status, stepIdx }: { status: RunStatus; stepIdx: number }) {
  const failed = status === "failed";
  const succeeded = status === "succeeded";

  return (
    <div
      role="group"
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
                "font-display text-[12px] sm:text-[12px] tracking-[0.08em] sm:tracking-wider uppercase text-center truncate w-full",
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
              <span className="inline-flex items-center gap-1 rounded-full border border-success/25 bg-success/10 px-2 py-0.5 font-display text-[12px] tracking-wider uppercase text-success">
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
              <div className="mt-1 font-display text-[12px] tracking-wider uppercase text-bg-8">
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
              className="h-11 w-full sm:w-auto px-4 inline-flex items-center justify-center gap-2 rounded-[var(--radius-2)] border border-[var(--color-hairline-strong)] bg-bg-2 text-[13.5px] font-medium text-bg-11 hover:bg-bg-3 hover:border-bg-7 transition-colors focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-accent"
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
      className="size-11 shrink-0 rounded-[var(--radius-2)] inline-flex items-center justify-center text-bg-8 hover:text-bg-11 hover:bg-bg-3 transition-colors focus-visible:outline-2 focus-visible:outline-accent"
    >
      {copied ? <Check size={12} className="text-success" /> : <Copy size={12} />}
    </button>
  );
}

function TechnicalDetails({ ids }: { ids: Record<string, unknown> }) {
  const groups = META_GROUPS.map((group) => ({
    ...group,
    ids: normalizeMetaIds(ids[group.key]),
  })).filter((group) => group.ids.length > 0);

  return (
    <details className="group border-t border-[var(--color-hairline)] pt-1">
      <summary className="list-none cursor-pointer py-2.5 flex items-center gap-2 text-[12px] text-bg-8 hover:text-bg-10 transition-colors focus-visible:outline-2 focus-visible:outline-accent rounded-[var(--radius-1)]">
        <ChevronDown
          size={13}
          className="transition-transform duration-150 group-open:rotate-180"
        />
        Технические детали
      </summary>

      <div className="pb-2 pt-1 space-y-3">
        {groups.map((group) => {
          const value = group.ids.join(", ");
          return (
            <div key={group.key} className="rounded-[var(--radius-2)] bg-bg-1 px-3 py-2.5">
              <div className="flex items-center gap-2">
                <span className="font-display text-[12px] tracking-wider uppercase text-bg-8">
                  {group.label}
                </span>
                <span className="text-[12px] text-bg-8">{group.ids.length}</span>
                <CopyButton value={value} label={`ID: ${group.label}`} />
              </div>
              <code className="mt-1.5 block font-mono text-[12px] leading-relaxed text-bg-9 break-all">
                {value}
              </code>
            </div>
          );
        })}
      </div>
    </details>
  );
}
