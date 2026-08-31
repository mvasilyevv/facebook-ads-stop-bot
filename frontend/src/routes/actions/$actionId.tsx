import { createFileRoute, Link } from "@tanstack/react-router";
import {
  ArrowLeft,
  ArrowRight,
  CheckCircle2,
  CircleHelp,
  Clock3,
  Loader2,
  XCircle,
} from "lucide-react";

import { ACTION_STATE_LABEL, actionForRealtimeState } from "@fb/shared/operator/viewModel";
import {
  OPERATOR_UNKNOWN_RESULT_NOTICE,
  operatorActionKindLabel,
  operatorActionReason,
  operatorActionRecovery,
} from "@fb/shared/operator/actionLabels";
import { formatZonedDateTime, timezoneEvidenceLabel } from "@fb/shared/format/time";
import type { OperatorActionItem } from "@fb/shared/operator/contracts";
import { DataStateBadge, DataStateNotice } from "@fb/operator-ui";
import { useOperatorRealtimeStatus } from "@fb/operator-api";

import {
  OperatorCardSkeleton,
  OperatorPageBoundary,
  OperatorUnavailableState,
} from "@/components/layout/OperatorPageBoundary";
import { operatorProblemMessage, useOperatorAction } from "@/lib/api/operator";

export const Route = createFileRoute("/actions/$actionId")({ component: ActionDetailPage });

function ActionDetailPage() {
  const { actionId } = Route.useParams();
  const realtimeStatus = useOperatorRealtimeStatus();
  const actionQuery = useOperatorAction(actionId);
  const projection = actionQuery.data
    ? actionForRealtimeState(
        actionQuery.data,
        realtimeStatus === "connected" && !actionQuery.isError,
      )
    : null;
  const action = projection?.data;

  if (actionQuery.isError && !projection) {
    return (
      <OperatorPageBoundary
        title="Действие"
        navigation={<ActionBreadcrumb />}
      >
        <OperatorUnavailableState
          title="Действие недоступно"
          resource="действие"
          details={operatorProblemMessage(actionQuery.error)}
          onRetry={() => void actionQuery.refetch()}
        />
      </OperatorPageBoundary>
    );
  }
  // isLoading (не isPending!) — иначе запрос, который так и не стартовал
  // (невалидный actionId → enabled:false) держит скелетон вечно: status
  // остаётся "pending" навсегда, хотя fetchStatus так и не переходит в
  // "fetching". См. #336.
  if (actionQuery.isLoading && !projection) {
    return (
      <OperatorPageBoundary
        title="Действие"
        navigation={<ActionBreadcrumb />}
      >
        <OperatorCardSkeleton label="Загрузка действия" />
      </OperatorPageBoundary>
    );
  }
  if (!action) {
    // Разбираем два разных исхода одним честным состоянием с retry:
    // запрос действительно завершился и действия нет (isFetched) —
    // или ответ не удалось разобрать/запрос не выполнился вовсе.
    const notFound = actionQuery.isFetched && !actionQuery.isError;
    return (
      <OperatorPageBoundary
        title="Действие"
        navigation={<ActionBreadcrumb />}
      >
        <OperatorUnavailableState
          title={notFound ? "Действие не найдено" : "Действие недоступно"}
          resource="действие"
          details={
            notFound
              ? `Задача #${actionId} отсутствует или недоступна.`
              : operatorProblemMessage(actionQuery.error)
          }
          onRetry={() => void actionQuery.refetch()}
        />
      </OperatorPageBoundary>
    );
  }
  const recovery = operatorActionRecovery(action.state, action.target_id);

  return (
    <article className="mx-auto max-w-3xl">
      <Link
        to="/actions"
        className="mb-5 inline-flex min-h-11 items-center gap-2 rounded-[var(--radius-2)] px-2 text-[14px] font-semibold text-bg-9 hover:bg-bg-2 hover:text-bg-11 focus-visible:outline-2 focus-visible:outline-accent"
      >
        <ArrowLeft size={16} aria-hidden="true" /> Все действия
      </Link>
      <header className="rounded-[var(--radius-3)] border border-[var(--color-hairline-strong)] bg-bg-1 p-5 sm:p-6">
        <div className="flex flex-wrap items-center justify-between gap-3">
          <span className="font-display text-[12px] uppercase tracking-[.08em] text-bg-8">
            {action.public_id} · {operatorActionKindLabel(action.kind)}
          </span>
          <div className="flex flex-wrap items-center gap-2">
            {projection ? <DataStateBadge state={projection.state} /> : null}
            <ActionStatus action={action} />
          </div>
        </div>
        <h1 className="m-0 mt-4 font-display text-[clamp(26px,4vw,38px)] font-medium text-bg-11">
          {action.title}
        </h1>
        <p className="mt-2 text-[16px] text-bg-9">{action.target_label ?? "Системная операция"}</p>
        {projection && projection.state !== "ready" ? (
          <div className="mt-5">
            <DataStateNotice state={projection.state} issues={projection.issues} />
          </div>
        ) : null}
        {action.state === "unknown" ? (
          <div
            role="status"
            className="mt-5 rounded-[var(--radius-2)] border border-warning/40 bg-warning-bg p-4 text-[14px] text-bg-10"
          >
            {OPERATOR_UNKNOWN_RESULT_NOTICE}
          </div>
        ) : null}
      </header>

      {action.run_id ? (
        <section className="mt-5 rounded-[var(--radius-3)] border border-[var(--color-hairline-strong)] bg-bg-1 p-5 sm:p-6">
          <h2 className="m-0 font-display text-[20px] text-bg-11">Залив</h2>
          <p className="mt-2 text-[14px] leading-6 text-bg-9">
            Состав залива, созданные объекты и безопасные действия — на экране кампании.
          </p>
          <Link
            to="/campaigns"
            search={{ run: action.run_id }}
            className="mt-4 inline-flex min-h-11 items-center gap-2 rounded-[var(--radius-2)] bg-bg-3 px-4 text-[14px] font-semibold text-bg-11 hover:bg-bg-4 focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-accent"
          >
            Открыть залив
            <ArrowRight size={16} aria-hidden="true" />
          </Link>
        </section>
      ) : null}

      <section className="mt-5 rounded-[var(--radius-3)] border border-[var(--color-hairline)] bg-bg-1 p-5 sm:p-6">
        <h2 className="m-0 font-display text-[20px] text-bg-11">Жизненный цикл</h2>
        <LifecycleRail state={action.state} />
        <dl className="mt-6 grid gap-px overflow-hidden rounded-[var(--radius-2)] border border-[var(--color-hairline)] bg-[var(--color-hairline)] sm:grid-cols-2">
          <Detail
            label="Запрошено"
            value={
              action.cabinet_timezone
                ? formatZonedDateTime(action.requested_at, action.cabinet_timezone)
                : "—"
            }
          />
          <Detail
            label="Обновлено"
            value={
              action.cabinet_timezone
                ? formatZonedDateTime(action.updated_at, action.cabinet_timezone)
                : "—"
            }
          />
          <Detail
            label="Часовой пояс"
            value={timezoneEvidenceLabel(
              action.cabinet_timezone,
              action.cabinet_timezone ? "single" : "unknown",
            )}
          />
          <Detail label="Инициатор" value={action.requested_by ?? "не указан"} />
        </dl>
        <div className="mt-4 rounded-[var(--radius-2)] bg-bg-2 p-4">
          <div className="text-[12px] font-semibold uppercase tracking-[.06em] text-bg-8">
            Причина
          </div>
          {/* Состояние уже названо чипом и дорожкой жизненного цикла выше;
              здесь — причина исхода из события, а не пересказ состояния. */}
          <p className="m-0 mt-2 break-words text-[14px] leading-6 text-bg-10">
            {operatorActionReason(action)}
          </p>
          {recovery?.destination === "target" && action.target_id ? (
            <Link
              to="/ads/$fbAdId"
              params={{ fbAdId: action.target_id }}
              className="mt-3 inline-flex min-h-11 items-center gap-2 rounded-[var(--radius-2)] px-3 text-[14px] font-semibold text-bg-11 hover:bg-bg-3 focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-accent"
            >
              {recovery.label}
              <ArrowRight size={16} aria-hidden="true" />
            </Link>
          ) : recovery?.destination === "sources" ? (
            <Link
              to="/system/sources"
              className="mt-3 inline-flex min-h-11 items-center gap-2 rounded-[var(--radius-2)] px-3 text-[14px] font-semibold text-bg-11 hover:bg-bg-3 focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-accent"
            >
              {recovery.label}
              <ArrowRight size={16} aria-hidden="true" />
            </Link>
          ) : null}
        </div>
      </section>
    </article>
  );
}

function ActionBreadcrumb() {
  return (
    <Link
      to="/actions"
      className="inline-flex min-h-11 items-center gap-2 rounded-[var(--radius-2)] px-2 text-[14px] font-semibold text-bg-9 hover:bg-bg-2 hover:text-bg-11 focus-visible:outline-2 focus-visible:outline-accent"
    >
      <ArrowLeft size={16} aria-hidden="true" /> Все действия
    </Link>
  );
}

function ActionStatus({ action }: { action: OperatorActionItem }) {
  const tone =
    action.state === "confirmed"
      ? "text-success border-success/40 bg-success-bg"
      : action.state === "failed"
        ? "text-danger border-danger/40 bg-danger-bg"
        : action.state === "unknown" || action.state === "queued" || action.state === "running"
          ? "text-warning border-warning/40 bg-warning-bg"
          : "text-bg-9 border-[var(--color-hairline-strong)] bg-bg-2";
  return (
    <span
      className={`inline-flex min-h-8 items-center rounded-full border px-3 text-[12px] font-semibold ${tone}`}
    >
      {ACTION_STATE_LABEL[action.state]}
    </span>
  );
}

function LifecycleRail({ state }: { state: OperatorActionItem["state"] }) {
  const terminal = ["confirmed", "failed", "cancelled", "unknown"].includes(state);
  const steps = [
    { label: "В очереди", done: true, Icon: Clock3 },
    { label: "Выполнение", done: state !== "queued", Icon: Loader2 },
    {
      label: terminal ? ACTION_STATE_LABEL[state] : "Подтверждение",
      done: terminal,
      Icon: state === "confirmed" ? CheckCircle2 : state === "failed" ? XCircle : CircleHelp,
    },
  ];
  return (
    <ol className="mt-5 grid gap-2 sm:grid-cols-3" aria-label="Этапы действия">
      {steps.map(({ label, done, Icon }) => (
        <li
          key={label}
          className={`flex min-h-14 items-center gap-3 rounded-[var(--radius-2)] border p-3 ${done ? "border-[var(--color-hairline-strong)] bg-bg-2 text-bg-11" : "border-[var(--color-hairline)] text-bg-8"}`}
        >
          <Icon size={18} aria-hidden="true" />
          <span className="text-[14px] font-semibold">{label}</span>
        </li>
      ))}
    </ol>
  );
}

function Detail({ label, value }: { label: string; value: string }) {
  return (
    <div className="min-w-0 bg-bg-2 p-4">
      <dt className="text-[12px] font-semibold uppercase tracking-[.05em] text-bg-8">{label}</dt>
      <dd className="m-0 mt-2 break-all text-[14px] text-bg-11">{value}</dd>
    </div>
  );
}
