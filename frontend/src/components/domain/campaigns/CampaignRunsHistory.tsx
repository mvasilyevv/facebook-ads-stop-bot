/**
 * История запусков: mobile cards + desktop table с RunSummaryOut.
 *
 * Фильтрация по статусу и отмена до необратимого create.
 * Переход к деталям (inline раскрытие или отдельная ссылка).
 */

import { type FC, useState } from "react";
import {
  campaignMetaIdGroups,
  campaignRunCommandLifecycle,
  campaignRunControlReason,
  campaignRunFailurePresentation,
  campaignRunRequiresManualReview,
  campaignRunTaskLifecycle,
  completeOperatorCommandIntent,
  getOrCreateOperatorCommandIntent,
  isOperatorCommandIntentStorageError,
  type CampaignRunControlAction,
  type CampaignRunTaskState,
  type OperatorCommandKind,
} from "@fb/shared";
import { safeApiProblemMessage } from "@fb/operator-api";
import {
  Ban,
  CheckCircle2,
  ChevronDown,
  ChevronRight,
  CircleHelp,
  CircleX,
  Clock3,
  LoaderCircle,
  RefreshCw,
  RotateCcw,
} from "lucide-react";
import { cn } from "@/lib/utils/cn";
import { Badge, type BadgeVariant } from "@/components/ui/Badge";
import { Button } from "@/components/ui/Button";
import { Skeleton } from "@/components/ui/Skeleton";
import { EmptyState } from "@/components/ui/EmptyState";
import { OperatorUnavailableState } from "@/components/layout/OperatorPageBoundary";
import { Select } from "@/components/ui/Select";
import { toast } from "@/components/ui/Toast";
import {
  useRuns,
  useRunDetail,
  useAbortCampaignRun,
  useResumeCampaignRun,
  RUN_STATUS_LABELS,
  type RunSummaryOut,
  type RunStatus,
} from "@/lib/api/campaigns";
import { CampaignRunManualReview } from "./CampaignRunManualReview";
import { formatRussianCount } from "@/lib/utils/russianCount";

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

export const CampaignRunsHistory: FC = () => {
  const [statusFilter, setStatusFilter] = useState("");

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
    return (
      <OperatorUnavailableState
        title="История запусков недоступна"
        resource="историю запусков"
        details={error instanceof Error ? error.message : undefined}
        onRetry={() => void refetch()}
      />
    );
  }

  return (
    <div className="space-y-4">
      {/* Toolbar */}
      <div className="flex flex-col gap-3 sm:flex-row sm:items-center sm:justify-between">
        <div className="flex min-w-0 items-center gap-2">
          <div className="min-w-0 flex-1 sm:w-[200px] sm:flex-none">
            <Select
              options={STATUS_FILTER_OPTIONS.map((option) => ({
                ...option,
                label: option.label ?? option.value,
              }))}
              value={statusFilter}
              onChange={(e) => setStatusFilter(e.target.value)}
            />
          </div>
          <span className="shrink-0 text-[12px] text-bg-8">
            {total > 0
              ? formatRussianCount(total, "запуск", "запуска", "запусков")
              : "нет запусков"}
          </span>
        </div>
        <Button
          variant="secondary"
          size="sm"
          leftIcon={<RefreshCw size={13} />}
          onClick={handleRefresh}
          loading={isLoading}
          className="w-full sm:w-auto"
        >
          Обновить
        </Button>
      </div>

      {/* Список */}
      {runs.length === 0 ? (
        <EmptyState
          icon={<RefreshCw size={28} />}
          title="Запусков нет"
          description="Создайте первую кампанию — её ход появится в журнале."
        />
      ) : (
        <div className="border border-[var(--color-hairline)] rounded-[var(--radius-3)] overflow-hidden">
          {/* Шапка */}
          <div
            data-testid="campaign-runs-desktop-header"
            className="hidden gap-3 border-b border-[var(--color-hairline)] bg-bg-2 px-4 py-2.5 md:grid md:grid-cols-[minmax(0,1fr)_120px_140px]"
          >
            {["Оффер", "Статус", "Создан"].map((h) => (
              <div
                key={h}
                className="font-display text-[12px] tracking-[0.12em] uppercase text-bg-8"
              >
                {h}
              </div>
            ))}
          </div>

          {/* Строки */}
          <div className="divide-y divide-[var(--color-hairline)]">
            {runs.map((run) => (
              <RunRow
                key={run.id}
                run={run}
                onRefresh={async () => {
                  await refetch({ throwOnError: true });
                }}
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
  onRefresh: () => Promise<unknown>;
}

const RunRow: FC<RunRowProps> = ({ run, onRefresh }) => {
  const [expanded, setExpanded] = useState(false);

  const createdAt = new Date(run.created_at).toLocaleString("ru-RU", {
    month: "short",
    day: "numeric",
    hour: "2-digit",
    minute: "2-digit",
  });

  return (
    <>
      <div
        data-testid="campaign-run-card"
        className="flex flex-col gap-3 px-4 py-3 transition-colors hover:bg-bg-2 md:grid md:grid-cols-[minmax(0,1fr)_120px_140px] md:items-center"
      >
        {/* Оффер */}
        <div className="min-w-0">
          <div className="flex items-center gap-1.5">
            <button
              type="button"
              onClick={() => setExpanded((v) => !v)}
              className="inline-flex size-11 shrink-0 items-center justify-center rounded-[var(--radius-2)] text-bg-8 transition-colors hover:bg-bg-3 hover:text-bg-11 focus-visible:outline-2 focus-visible:outline-accent"
              aria-expanded={expanded}
              aria-label="Развернуть детали"
            >
              {expanded ? <ChevronDown size={13} /> : <ChevronRight size={13} />}
            </button>
            <span className="font-medium text-[13px] text-bg-11 truncate">
              {run.offer_code ?? "—"}
            </span>
            {run.account_id ? (
              <span className="font-numeric text-[12px] text-bg-8">act_{run.account_id}</span>
            ) : null}
          </div>
          {run.status === "failed" ? (
            <div className="ml-[50px] line-clamp-2 text-[12px] text-danger" role="alert">
              Запуск завершился ошибкой. Откройте детали для безопасных действий.
            </div>
          ) : null}
          <div className="ml-[50px] mt-2 flex flex-wrap items-center gap-x-3 gap-y-1 md:hidden">
            <span
              className={cn(
                "font-display text-[12px] uppercase tracking-wider",
                statusColor(run.status as RunStatus),
              )}
            >
              {RUN_STATUS_LABELS[run.status as RunStatus] ?? "Статус не подтверждён"}
            </span>
            <span className="text-[12px] text-bg-8">{createdAt}</span>
          </div>
        </div>

        {/* Статус */}
        <div
          className={cn(
            "hidden font-display text-[12px] tracking-wider uppercase md:block",
            statusColor(run.status as RunStatus),
          )}
        >
          {RUN_STATUS_LABELS[run.status as RunStatus] ?? "Статус не подтверждён"}
        </div>

        {/* Дата */}
        <div className="hidden text-[12px] text-bg-8 md:block">{createdAt}</div>
      </div>

      {/* Expandable: детали run */}
      {expanded && <RunExpandedDetails runId={run.id} onListRefresh={onRefresh} />}
    </>
  );
};

// ─── RunExpandedDetails ───────────────────────────────────────────────────────

function RunExpandedDetails({
  runId,
  onListRefresh,
}: {
  runId: string;
  onListRefresh: () => Promise<unknown>;
}) {
  const query = useRunDetail(runId);
  const run = query.data;
  const abortMutation = useAbortCampaignRun();
  const resumeMutation = useResumeCampaignRun();
  const [commandStatus, setCommandStatus] = useState<{
    action: CampaignRunControlAction;
    state: CampaignRunTaskState;
    replayed: boolean;
  } | null>(null);
  const [commandError, setCommandError] = useState<string | null>(null);

  if (query.isLoading) return <Skeleton className="h-16 mx-4 mb-3 mt-1" />;
  if (query.isError) {
    return (
      <div className="mx-4 mb-3 mt-1">
        <OperatorUnavailableState
          title="Детали запуска недоступны"
          resource="детали запуска"
          details={query.error instanceof Error ? query.error.message : undefined}
          onRetry={() => void query.refetch()}
        />
      </div>
    );
  }
  if (!run) return null;

  const metaIdGroups = campaignMetaIdGroups(run.created_meta_ids).filter(
    (group) => group.ids.length > 0,
  );
  const manualReviewRequired = campaignRunRequiresManualReview(run);
  const failure = campaignRunFailurePresentation(run);
  const commandBusy = abortMutation.isPending || resumeMutation.isPending;

  async function executeCommand(action: CampaignRunControlAction) {
    const mutation = action === "abort" ? abortMutation : resumeMutation;
    const actionKind: OperatorCommandKind =
      action === "abort" ? "abort_campaign_run" : "resume_campaign_run";
    const actionLabel = action === "abort" ? "Остановка" : "Повтор";
    setCommandError(null);
    let idempotencyKey: string;
    try {
      idempotencyKey = getOrCreateOperatorCommandIntent(actionKind, runId);
      const receipt = await mutation.mutateAsync({
        params: {
          path: { run_id: runId },
          header: { "Idempotency-Key": idempotencyKey },
        },
      });
      let cleanupWarning: string | null = null;
      try {
        completeOperatorCommandIntent(actionKind, runId, idempotencyKey);
      } catch (error) {
        if (!isOperatorCommandIntentStorageError(error)) throw error;
        cleanupWarning = error.userMessage;
      }

      setCommandStatus({
        action,
        state: receipt.state,
        replayed: !receipt.created,
      });
      const lifecycle = campaignRunCommandLifecycle(action, receipt.state);
      if (receipt.state === "confirmed") {
        toast.success(lifecycle.description);
      } else if (receipt.state === "queued" || receipt.state === "running") {
        toast.info(lifecycle.description);
      } else if (receipt.state === "unknown") {
        toast.warning(lifecycle.description);
      } else {
        toast.error(lifecycle.description);
      }
      if (cleanupWarning) {
        toast.error(
          `${actionLabel}: ключ защиты не очищен`,
          `Команда уже принята — не повторяйте её. ${cleanupWarning}`,
        );
      }

      const reconciled = await Promise.allSettled([
        query.refetch({ throwOnError: true }),
        onListRefresh(),
      ]);
      if (reconciled.some((result) => result.status === "rejected")) {
        toast.warning(
          `${actionLabel} принята, но данные не обновились`,
          "Не повторяйте команду. Обновите историю вручную.",
        );
      }
    } catch (error) {
      const message = isOperatorCommandIntentStorageError(error)
        ? error.userMessage
        : safeApiProblemMessage(error, `${actionLabel} недоступна`);
      setCommandError(message);
      toast.error(`${actionLabel} не отправлена`, message);
    }
  }

  return (
    <div className="mx-4 mb-3 mt-0 space-y-4 rounded-[var(--radius-2)] border border-[var(--color-hairline)] bg-bg-2 p-4 text-[12px] text-bg-8">
      <CampaignRunTaskLifecycle task={run.task} />
      {failure ? <CampaignRunFailureGuidance failure={failure} /> : null}
      <CampaignRunControls
        runId={runId}
        controls={run.controls}
        commandBusy={commandBusy}
        abortBusy={abortMutation.isPending}
        resumeBusy={resumeMutation.isPending}
        onCommand={executeCommand}
      />
      {commandStatus ? (
        <CommandStatusNotice
          action={commandStatus.action}
          state={commandStatus.state}
          replayed={commandStatus.replayed}
        />
      ) : null}
      {commandError ? (
        <div
          role="alert"
          className="rounded-[var(--radius-2)] border border-danger/35 bg-danger-bg p-3 text-[13px] leading-5 text-danger"
        >
          {commandError}
        </div>
      ) : null}
      {manualReviewRequired ? (
        <CampaignRunManualReview createdMetaIds={run.created_meta_ids ?? {}} />
      ) : null}
      {/* Созданные IDs успешного запуска остаются доступны для сверки результата. */}
      {!manualReviewRequired && metaIdGroups.length > 0 ? (
        <div>
          <span className="font-display text-[12px] uppercase tracking-wider text-bg-8 block mb-1">
            Созданные объекты
          </span>
          {metaIdGroups.map((group) => (
            <div key={group.key} className="flex min-w-0 gap-2 font-numeric text-[12px]">
              <span className="shrink-0 text-bg-8">
                {group.label} · {group.ids.length}:
              </span>
              <span className="min-w-0 break-all text-bg-10">{group.ids.join(", ")}</span>
            </div>
          ))}
        </div>
      ) : null}
    </div>
  );
}

type RunTask = NonNullable<import("@/lib/api/campaigns").RunDetailOut["task"]>;

function CampaignRunFailureGuidance({
  failure,
}: {
  failure: NonNullable<ReturnType<typeof campaignRunFailurePresentation>>;
}) {
  return (
    <section
      role="status"
      data-failure-class={failure.category}
      className="rounded-[var(--radius-2)] border border-warning/35 bg-warning-bg p-3"
    >
      <p className="font-medium text-[13px] text-bg-11">{failure.title}</p>
      <p className="mt-1 text-[12px] leading-5 text-bg-9">{failure.reason}</p>
      <p className="mt-2 text-[12px] font-medium text-bg-10">
        Следующий шаг: {failure.action.label}.
      </p>
    </section>
  );
}

const TASK_BADGE_VARIANT: Record<CampaignRunTaskState, BadgeVariant> = {
  queued: "warning",
  running: "running",
  confirmed: "done",
  failed: "failed",
  cancelled: "cancelled",
  unknown: "neutral",
};

const TASK_ICON = {
  queued: Clock3,
  running: LoaderCircle,
  confirmed: CheckCircle2,
  failed: CircleX,
  cancelled: Ban,
  unknown: CircleHelp,
} satisfies Record<CampaignRunTaskState, typeof Clock3>;

function CampaignRunTaskLifecycle({ task }: { task: RunTask | null }) {
  if (!task) {
    return (
      <section aria-label="Состояние задачи">
        <div className="flex items-center gap-2 text-bg-10">
          <CircleHelp size={16} aria-hidden="true" />
          <span className="font-display text-[12px] uppercase tracking-wider">
            Задача не подтверждена
          </span>
        </div>
        <p className="mt-2 text-[13px] leading-5 text-bg-9">
          Связанная задача не найдена. Управление запуском заблокировано до сверки.
        </p>
      </section>
    );
  }
  const lifecycle = campaignRunTaskLifecycle(task.state);
  const Icon = TASK_ICON[task.state];
  return (
    <section aria-label="Состояние задачи">
      <div className="flex flex-wrap items-center gap-2">
        <Badge
          variant={TASK_BADGE_VARIANT[task.state]}
          withDot={false}
          data-task-state={task.state}
        >
          <Icon
            size={13}
            aria-hidden="true"
            className={cn(task.state === "running" && "animate-spin motion-reduce:animate-none")}
          />
          {lifecycle.label}
        </Badge>
        <span className="text-[12px] text-bg-8">
          Попыток: {task.attempt_count} из {task.max_attempts}
        </span>
      </div>
      <p
        className={cn(
          "mt-2 text-[13px] leading-5",
          task.state === "unknown" ? "text-bg-10" : "text-bg-9",
        )}
      >
        {lifecycle.description}
      </p>
    </section>
  );
}

type RunControls = import("@/lib/api/campaigns").RunDetailOut["controls"];

function CampaignRunControls({
  runId,
  controls,
  commandBusy,
  abortBusy,
  resumeBusy,
  onCommand,
}: {
  runId: string;
  controls: RunControls;
  commandBusy: boolean;
  abortBusy: boolean;
  resumeBusy: boolean;
  onCommand: (action: CampaignRunControlAction) => Promise<void>;
}) {
  const abortDescriptionId = `campaign-abort-${runId}`;
  const resumeDescriptionId = `campaign-resume-${runId}`;
  return (
    <section
      aria-label="Управление запуском"
      className="rounded-[var(--radius-2)] border border-[var(--color-hairline)] bg-bg-1 p-3"
    >
      <div className="mb-3">
        <span className="font-display text-[12px] uppercase tracking-wider text-bg-8">
          Управление
        </span>
        <p className="mt-1 text-[12px] leading-5 text-bg-8">
          Кнопка сразу отправляет идемпотентную команду. Принятие в очередь ещё не означает
          завершение.
        </p>
      </div>
      <div className="grid gap-3 sm:grid-cols-2">
        <div className="min-w-0">
          {controls.abort.available ? (
            <Button
              type="button"
              variant="danger"
              size="sm"
              fullWidth
              loading={abortBusy}
              disabled={commandBusy}
              aria-describedby={abortDescriptionId}
              leftIcon={<Ban aria-hidden="true" />}
              onClick={() => void onCommand("abort")}
            >
              Запросить остановку
            </Button>
          ) : (
            <p className="font-medium text-[13px] text-bg-10">Остановка недоступна</p>
          )}
          <p id={abortDescriptionId} className="mt-1.5 text-[12px] leading-5 text-bg-8">
            {campaignRunControlReason("abort", controls.abort.reason, controls.abort.available)}
          </p>
        </div>
        <div className="min-w-0">
          {controls.resume.available ? (
            <Button
              type="button"
              variant="secondary"
              size="sm"
              fullWidth
              loading={resumeBusy}
              disabled={commandBusy}
              aria-describedby={resumeDescriptionId}
              leftIcon={<RotateCcw aria-hidden="true" />}
              onClick={() => void onCommand("resume")}
            >
              Безопасно повторить
            </Button>
          ) : (
            <p className="font-medium text-[13px] text-bg-10">Повтор недоступен</p>
          )}
          <p id={resumeDescriptionId} className="mt-1.5 text-[12px] leading-5 text-bg-8">
            {campaignRunControlReason("resume", controls.resume.reason, controls.resume.available)}
          </p>
        </div>
      </div>
    </section>
  );
}

function CommandStatusNotice({
  action,
  state,
  replayed,
}: {
  action: CampaignRunControlAction;
  state: CampaignRunTaskState;
  replayed: boolean;
}) {
  const lifecycle = campaignRunCommandLifecycle(action, state);
  const Icon = TASK_ICON[state];
  return (
    <div
      role="status"
      aria-live="polite"
      data-command-state={state}
      className="rounded-[var(--radius-2)] border border-[var(--color-hairline-strong)] bg-bg-1 p-3"
    >
      <div className="flex items-center gap-2 text-bg-10">
        <Icon
          size={15}
          aria-hidden="true"
          className={cn(state === "running" && "animate-spin motion-reduce:animate-none")}
        />
        <span className="font-medium text-[13px]">{lifecycle.description}</span>
      </div>
      {replayed ? (
        <p className="mt-1 text-[12px] leading-5 text-bg-8">
          Показано сохранённое состояние уже принятой команды.
        </p>
      ) : null}
    </div>
  );
}
