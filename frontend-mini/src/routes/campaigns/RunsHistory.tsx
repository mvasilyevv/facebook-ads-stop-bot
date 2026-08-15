import { useState } from "react";
import {
  adsManagerCampaignUrl,
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
  AlertTriangle,
  Ban,
  CheckCircle2,
  ChevronDown,
  CircleHelp,
  CircleX,
  Clock3,
  ExternalLink,
  LoaderCircle,
  RefreshCw,
  RotateCcw,
  X,
} from "lucide-react";

import { Eyebrow } from "@/components/data";
import {
  Badge,
  type BadgeVariant,
  Button,
  EmptyState,
  ErrorState,
  Skeleton,
} from "@/components/ui";
import { cn } from "@/lib/cn";
import {
  campaignRunStatusLabel,
  type CampaignRunDetail,
  type CampaignRunSummary,
} from "@/lib/campaignRuns";
import {
  operatorProblemMessage,
  useAbortCampaignRun,
  useCampaignRun,
  useCampaignRuns,
  useResumeCampaignRun,
} from "@/lib/operatorApi";
import { haptic, openLink } from "@/lib/tg";

function runBadgeVariant(
  status: string,
): "done" | "warning" | "failed" | "neutral" {
  if (status === "succeeded") return "done";
  if (status === "failed" || status === "cancelled") return "failed";
  if (["queued", "uniquifying", "uploading", "creating"].includes(status)) {
    return "warning";
  }
  return "neutral";
}

function formatDate(iso: string): string {
  const date = new Date(iso);
  return Number.isNaN(date.getTime())
    ? "дата не подтверждена"
    : date.toLocaleString("ru-RU", {
        day: "2-digit",
        month: "2-digit",
        hour: "2-digit",
        minute: "2-digit",
      });
}

function resultCounts(run: CampaignRunDetail): Array<[string, number]> {
  return campaignMetaIdGroups(run.created_meta_ids)
    .map((group) => [group.label, group.ids.length] as [string, number])
    .filter(([, count]) => count > 0);
}

function progressFacts(
  progress: CampaignRunDetail["progress"],
): Array<[string, string]> {
  const facts: Array<[string, string]> = [];
  const stageLabels: Record<string, string> = {
    queued: "В очереди",
    uniquifying: "Уникализация",
    uploading: "Загрузка",
    creating: "Создание",
    succeeded: "Готово",
    failed: "Ошибка",
    cancelled: "Отменён",
  };
  const stageLabel = stageLabels[progress.stage];
  if (stageLabel) {
    facts.push(["Этап", stageLabel]);
  }
  for (const [key, label] of [
    ["completed", "Готово"],
    ["total", "Всего"],
  ] as const) {
    const value = progress[key];
    if (
      typeof value === "number" &&
      Number.isSafeInteger(value) &&
      value >= 0
    ) {
      facts.push([label, String(value)]);
    }
  }
  return facts;
}

function ManualReviewRequired({ run }: { run: CampaignRunDetail }) {
  const groups = campaignMetaIdGroups(run.created_meta_ids).filter(
    (group) => group.ids.length > 0,
  );
  const adsManagerUrl = adsManagerCampaignUrl(run.created_meta_ids);

  return (
    <section
      role="alert"
      aria-label="Требуется ручная сверка"
      className="rounded-[var(--radius-3)] border border-warning/45 bg-warning/10 p-4"
    >
      <div className="flex items-start gap-3">
        <AlertTriangle
          size={18}
          className="mt-0.5 shrink-0 text-warning"
          aria-hidden
        />
        <div className="min-w-0">
          <h3 className="text-[14px] font-semibold text-bg-11">
            Требуется ручная сверка
          </h3>
          <p className="mt-1 text-[13px] leading-5 text-bg-9">
            Не повторяйте запуск и не удаляйте объекты вслепую. Сначала
            проверьте фактическое состояние в Ads Manager.
          </p>
        </div>
      </div>

      {groups.length > 0 ? (
        <div className="mt-4 space-y-3">
          {groups.map((group) => (
            <div key={group.key}>
              <p className="text-[12px] font-semibold uppercase tracking-[.06em] text-bg-8">
                {group.label} · {group.ids.length}
              </p>
              <p className="mt-1 break-all font-numeric text-[12px] leading-5 text-bg-11">
                {group.ids.join(", ")}
              </p>
            </div>
          ))}
        </div>
      ) : (
        <p className="mt-3 rounded-[var(--radius-2)] bg-bg-2 px-3 py-2 text-[12px] leading-5 text-bg-9">
          Подтверждённых Meta ID нет. Ответ мог потеряться уже после создания
          объекта.
        </p>
      )}

      {adsManagerUrl ? (
        <Button
          type="button"
          variant="secondary"
          fullWidth
          className="mt-4"
          onClick={() => {
            haptic.selection();
            openLink(adsManagerUrl);
          }}
        >
          Открыть Ads Manager
          <ExternalLink size={15} aria-hidden />
        </Button>
      ) : null}
    </section>
  );
}

function RunDetail({
  runId,
  onClose,
  onListRefresh,
}: {
  runId: string;
  onClose: () => void;
  onListRefresh: () => Promise<unknown>;
}) {
  const query = useCampaignRun(runId);
  const abortMutation = useAbortCampaignRun();
  const resumeMutation = useResumeCampaignRun();
  const [commandStatus, setCommandStatus] = useState<{
    action: CampaignRunControlAction;
    state: CampaignRunTaskState;
    replayed: boolean;
  } | null>(null);
  const [commandError, setCommandError] = useState<string | null>(null);
  const run = query.data;
  const progress = run ? progressFacts(run.progress) : [];
  const manualReviewRequired = run
    ? campaignRunRequiresManualReview(run)
    : false;
  const failure = run ? campaignRunFailurePresentation(run) : null;
  const commandBusy = abortMutation.isPending || resumeMutation.isPending;

  async function executeCommand(action: CampaignRunControlAction) {
    const mutation = action === "abort" ? abortMutation : resumeMutation;
    const actionKind: OperatorCommandKind =
      action === "abort" ? "abort_campaign_run" : "resume_campaign_run";
    const actionLabel = action === "abort" ? "Остановка" : "Повтор";
    setCommandError(null);
    haptic.impact(action === "abort" ? "heavy" : "medium");
    try {
      const idempotencyKey = getOrCreateOperatorCommandIntent(
        actionKind,
        runId,
      );
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
      if (receipt.state === "confirmed") {
        haptic.notify("success");
      } else if (
        receipt.state === "queued" ||
        receipt.state === "running" ||
        receipt.state === "unknown"
      ) {
        haptic.notify("warning");
      } else {
        haptic.notify("error");
      }
      if (cleanupWarning) {
        setCommandError(
          `Команда уже принята — не повторяйте её. ${cleanupWarning}`,
        );
        haptic.notify("warning");
      }
      const reconciled = await Promise.allSettled([
        query.refetch({ throwOnError: true }),
        onListRefresh(),
      ]);
      if (reconciled.some((result) => result.status === "rejected")) {
        setCommandError(
          `${actionLabel} принята, но данные не обновились. Не повторяйте команду; обновите историю вручную.`,
        );
      }
    } catch (error) {
      setCommandError(
        isOperatorCommandIntentStorageError(error)
          ? error.userMessage
          : safeApiProblemMessage(error, `${actionLabel} недоступна`),
      );
      haptic.notify("error");
    }
  }

  return (
    <section
      aria-label="Детали запуска"
      className="rounded-[var(--radius-3)] border border-accent/30 bg-bg-1 p-4"
    >
      <div className="flex min-h-11 items-center justify-between gap-3">
        <div>
          <Eyebrow>ХОД ВЫПОЛНЕНИЯ</Eyebrow>
          <p className="mt-1 text-[12px] text-bg-9">
            {run ? formatDate(run.created_at) : "Запуск кампании"}
          </p>
        </div>
        <button
          type="button"
          aria-label="Закрыть детали запуска"
          onClick={onClose}
          className="inline-flex size-11 items-center justify-center rounded-[var(--radius-2)] text-bg-9 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-accent"
        >
          <X size={18} aria-hidden />
        </button>
      </div>

      {query.isLoading ? <Skeleton className="mt-3 h-28" /> : null}
      {query.isError ? (
        <ErrorState
          message={operatorProblemMessage(query.error)}
          onRetry={() => void query.refetch()}
        />
      ) : null}
      {run ? (
        <div className="mt-3 space-y-4">
          <div className="flex flex-wrap items-center gap-2">
            <Badge variant={runBadgeVariant(run.status)}>
              {campaignRunStatusLabel(run.status)}
            </Badge>
            <span className="text-[13px] text-bg-9">
              Обновлено {formatDate(run.updated_at)}
            </span>
          </div>

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
              className="rounded-[var(--radius-2)] border border-danger/40 bg-danger-bg p-3 text-[13px] leading-5 text-danger"
            >
              {commandError}
            </div>
          ) : null}

          {progress.length > 0 ? (
            <dl className="grid grid-cols-2 gap-px overflow-hidden rounded-[var(--radius-2)] border border-[var(--color-hairline)] bg-[var(--color-hairline)]">
              {progress.map(([label, value], index) => (
                <div
                  key={label}
                  className={cn(
                    "min-w-0 bg-bg-0 p-3",
                    progress.length % 2 === 1 &&
                      index === progress.length - 1 &&
                      "col-span-2",
                  )}
                >
                  <dt className="text-[12px] text-bg-8">{label}</dt>
                  <dd className="mt-1 break-words font-numeric text-[13px] text-bg-11">
                    {value}
                  </dd>
                </div>
              ))}
            </dl>
          ) : null}

          {manualReviewRequired ? <ManualReviewRequired run={run} /> : null}

          {!manualReviewRequired && resultCounts(run).length > 0 ? (
            <div>
              <p className="text-[12px] font-semibold uppercase tracking-[.06em] text-bg-8">
                Создано в Meta
              </p>
              <div className="mt-2 flex flex-wrap gap-2">
                {resultCounts(run).map(([label, count]) => (
                  <span
                    key={label}
                    className="rounded-full bg-bg-3 px-3 py-1.5 text-[13px] text-bg-10"
                  >
                    {label}: {count}
                  </span>
                ))}
              </div>
            </div>
          ) : null}
        </div>
      ) : null}
    </section>
  );
}

type RunTask = NonNullable<CampaignRunDetail["task"]>;

function CampaignRunFailureGuidance({
  failure,
}: {
  failure: NonNullable<ReturnType<typeof campaignRunFailurePresentation>>;
}) {
  return (
    <section
      role="status"
      data-failure-class={failure.category}
      className="rounded-[var(--radius-2)] border border-warning/40 bg-warning/10 p-3"
    >
      <p className="text-[13px] font-semibold text-bg-11">{failure.title}</p>
      <p className="mt-1 text-[13px] leading-5 text-bg-9">{failure.reason}</p>
      <p className="mt-2 text-[12px] font-semibold text-bg-10">
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
      <section
        aria-label="Состояние задачи"
        className="rounded-[var(--radius-2)] border border-[var(--color-hairline)] bg-bg-0 p-3"
      >
        <div className="flex items-center gap-2 text-bg-10">
          <CircleHelp size={16} aria-hidden />
          <span className="text-[13px] font-semibold">
            Задача не подтверждена
          </span>
        </div>
        <p className="mt-2 text-[13px] leading-5 text-bg-9">
          Связанная задача не найдена. Управление запуском заблокировано до
          сверки.
        </p>
      </section>
    );
  }
  const lifecycle = campaignRunTaskLifecycle(task.state);
  const Icon = TASK_ICON[task.state];
  return (
    <section
      aria-label="Состояние задачи"
      data-task-state={task.state}
      className="rounded-[var(--radius-2)] border border-[var(--color-hairline)] bg-bg-0 p-3"
    >
      <div className="flex flex-wrap items-center gap-2">
        <Badge variant={TASK_BADGE_VARIANT[task.state]} withDot={false}>
          <Icon
            size={13}
            aria-hidden
            className={cn(
              task.state === "running" &&
                "animate-spin motion-reduce:animate-none",
            )}
          />
          {lifecycle.label}
        </Badge>
      </div>
      <p className="mt-2 text-[13px] leading-5 text-bg-9">
        {lifecycle.description}
      </p>
      <p className="mt-1 text-[12px] text-bg-8">
        Попыток: {task.attempt_count} из {task.max_attempts}
      </p>
    </section>
  );
}

type RunControls = CampaignRunDetail["controls"];

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
  const abortDescriptionId = `tma-campaign-abort-${runId}`;
  const resumeDescriptionId = `tma-campaign-resume-${runId}`;
  return (
    <section
      aria-label="Управление запуском"
      className="rounded-[var(--radius-2)] border border-[var(--color-hairline)] bg-bg-0 p-3"
    >
      <p className="text-[12px] font-semibold uppercase tracking-[.06em] text-bg-8">
        Управление
      </p>
      <p className="mt-1 text-[12px] leading-5 text-bg-8">
        Кнопка сразу отправляет идемпотентную команду. Принятие в очередь ещё не
        означает завершение.
      </p>
      <div className="mt-3 space-y-4">
        <div>
          {controls.abort.available ? (
            <Button
              type="button"
              variant="danger"
              fullWidth
              loading={abortBusy}
              disabled={commandBusy}
              aria-describedby={abortDescriptionId}
              onClick={() => void onCommand("abort")}
            >
              <Ban size={16} aria-hidden />
              Запросить остановку
            </Button>
          ) : (
            <p className="text-[13px] font-semibold text-bg-10">
              Остановка недоступна
            </p>
          )}
          <p
            id={abortDescriptionId}
            className="mt-1.5 text-[12px] leading-5 text-bg-8"
          >
            {campaignRunControlReason(
              "abort",
              controls.abort.reason,
              controls.abort.available,
            )}
          </p>
        </div>
        <div>
          {controls.resume.available ? (
            <Button
              type="button"
              variant="secondary"
              fullWidth
              loading={resumeBusy}
              disabled={commandBusy}
              aria-describedby={resumeDescriptionId}
              onClick={() => void onCommand("resume")}
            >
              <RotateCcw size={16} aria-hidden />
              Безопасно повторить
            </Button>
          ) : (
            <p className="text-[13px] font-semibold text-bg-10">
              Повтор недоступен
            </p>
          )}
          <p
            id={resumeDescriptionId}
            className="mt-1.5 text-[12px] leading-5 text-bg-8"
          >
            {campaignRunControlReason(
              "resume",
              controls.resume.reason,
              controls.resume.available,
            )}
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
      className="rounded-[var(--radius-2)] border border-[var(--color-hairline-strong)] bg-bg-0 p-3"
    >
      <div className="flex items-start gap-2 text-bg-10">
        <Icon
          size={16}
          aria-hidden
          className={cn(
            "mt-0.5 shrink-0",
            state === "running" && "animate-spin motion-reduce:animate-none",
          )}
        />
        <p className="text-[13px] font-semibold leading-5">
          {lifecycle.description}
        </p>
      </div>
      {replayed ? (
        <p className="mt-1 pl-6 text-[12px] leading-5 text-bg-8">
          Показано сохранённое состояние уже принятой команды.
        </p>
      ) : null}
    </div>
  );
}

function RunCard({
  run,
  selected,
  onOpen,
}: {
  run: CampaignRunSummary;
  selected: boolean;
  onOpen: () => void;
}) {
  return (
    <article
      className={cn(
        "overflow-hidden rounded-[var(--radius-3)] border bg-bg-1",
        selected ? "border-accent/50" : "border-[var(--color-hairline)]",
      )}
    >
      <button
        type="button"
        aria-expanded={selected}
        aria-label={`Открыть запуск ${run.offer_code ?? formatDate(run.created_at)}`}
        onClick={onOpen}
        className="flex min-h-16 w-full items-center justify-between gap-3 px-4 py-3 text-left focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-inset focus-visible:ring-accent"
      >
        <span className="min-w-0">
          <span className="flex flex-wrap items-center gap-2">
            <Badge variant={runBadgeVariant(run.status)}>
              {campaignRunStatusLabel(run.status)}
            </Badge>
            {run.offer_code ? (
              <span className="truncate text-[13px] font-semibold text-bg-11">
                {run.offer_code}
              </span>
            ) : null}
          </span>
          <span className="mt-1 block text-[12px] text-bg-8">
            {run.account_id ? `act_${run.account_id} · ` : ""}
            {formatDate(run.created_at)}
          </span>
        </span>
        <ChevronDown
          className={cn(
            "shrink-0 text-bg-8 transition-transform",
            selected && "rotate-180",
          )}
          size={18}
          aria-hidden
        />
      </button>

      {run.status === "failed" ? (
        <p className="line-clamp-2 px-4 pb-3 text-[13px] leading-5 text-danger">
          Запуск завершился ошибкой. Откройте детали для безопасных действий.
        </p>
      ) : null}
    </article>
  );
}

export function RunsHistory() {
  const query = useCampaignRuns();
  const [selectedRunId, setSelectedRunId] = useState<string | null>(null);

  const runs = query.data ?? [];
  return (
    <div className="flex flex-col gap-4 p-4 pb-8">
      <div className="rounded-[var(--radius-3)] border border-accent/25 bg-accent/5 p-4">
        <p className="text-[14px] font-semibold text-bg-11">Ход выполнения</p>
        <p className="mt-1 text-[13px] leading-5 text-bg-9">
          Здесь — ход выполнения, подтверждённый результат и серверные команды
          безопасной остановки или повтора.
        </p>
      </div>

      <div className="flex min-h-11 items-center justify-between gap-3">
        <Eyebrow>ЗАПУСКИ</Eyebrow>
        <button
          type="button"
          aria-label="Обновить запуски"
          onClick={() => {
            haptic.selection();
            void query.refetch();
          }}
          disabled={query.isFetching}
          className="inline-flex size-11 items-center justify-center rounded-[var(--radius-2)] text-bg-9 disabled:opacity-40 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-accent"
        >
          <RefreshCw
            className={cn(
              query.isFetching && "animate-spin motion-reduce:animate-none",
            )}
            size={18}
            aria-hidden
          />
        </button>
      </div>

      {query.isLoading ? (
        <div className="space-y-3">
          <Skeleton className="h-24" />
          <Skeleton className="h-24" />
        </div>
      ) : null}
      {query.isError ? (
        <ErrorState
          message={operatorProblemMessage(query.error)}
          onRetry={() => void query.refetch()}
        />
      ) : null}
      {!query.isLoading && !query.isError && runs.length === 0 ? (
        <EmptyState
          title="Запусков пока нет"
          description="Создайте кампанию здесь — её ход появится в журнале."
        />
      ) : null}

      {runs.map((run) => (
        <RunCard
          key={run.id}
          run={run}
          selected={selectedRunId === run.id}
          onOpen={() =>
            setSelectedRunId((current) => (current === run.id ? null : run.id))
          }
        />
      ))}

      {selectedRunId ? (
        <RunDetail
          runId={selectedRunId}
          onClose={() => setSelectedRunId(null)}
          onListRefresh={async () => {
            await query.refetch({ throwOnError: true });
          }}
        />
      ) : null}
    </div>
  );
}
