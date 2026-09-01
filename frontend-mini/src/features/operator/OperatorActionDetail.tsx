import { Link } from "@tanstack/react-router";
import { ArrowRight } from "lucide-react";

import {
  ACTION_STATE_LABEL,
  actionForRealtimeState,
} from "@fb/shared/operator/viewModel";
import {
  OPERATOR_UNKNOWN_RESULT_NOTICE,
  operatorActionKindLabel,
  operatorActionReason,
  operatorActionRecovery,
} from "@fb/shared/operator/actionLabels";
import {
  formatZonedDateTime,
  timezoneEvidenceLabel,
} from "@fb/shared/format/time";
import type { ManualReviewObservation } from "@fb/shared/operator/manualReview";
import {
  DataStateBadge,
  DataStateNotice,
  ManualReviewPanel,
} from "@fb/operator-ui";
import { useOperatorRealtimeStatus } from "@fb/operator-api";

import { ErrorState } from "@/components/ui";
import {
  operatorProblemMessage,
  useOperatorAction,
  useRecordOperatorManualReview,
} from "@/lib/operatorApi";

/**
 * Экран действия: маршрут `/actions/$actionId` и deep-link `/open` открывают
 * один и тот же компонент, поэтому он живёт вне `routes/` (issue #349).
 */
export function MiniActionDetail({ actionId }: { actionId: string }) {
  const realtimeStatus = useOperatorRealtimeStatus();
  const query = useOperatorAction(actionId);
  const manualReview = useRecordOperatorManualReview();
  const projection = query.data
    ? actionForRealtimeState(
        query.data,
        realtimeStatus === "connected" && !query.isError,
      )
    : null;
  const action = projection?.data;

  if (query.isPending && !projection)
    return (
      <div role="status" className="p-4 text-[14px] text-bg-9">
        Загрузка действия…
      </div>
    );
  if (query.isError && !projection)
    return (
      <div className="p-4">
        <ErrorState
          message={operatorProblemMessage(query.error)}
          onRetry={() => void query.refetch()}
        />
      </div>
    );
  if (!action)
    return (
      <div
        role="alert"
        className="m-4 rounded-[var(--radius-2)] bg-danger-bg p-4 text-[14px] text-danger"
      >
        Действие #{actionId} не найдено.
      </div>
    );
  const recovery = operatorActionRecovery(action.state, action.target_id);

  return (
    <article className="px-4 pb-6 pt-4">
      <header className="rounded-[var(--radius-3)] border border-[var(--color-hairline-strong)] bg-bg-1 p-4">
        <div className="font-display text-[12px] uppercase tracking-[.08em] text-bg-8">
          {action.public_id} · {operatorActionKindLabel(action.kind)}
        </div>
        <h1 className="m-0 mt-3 font-display text-[26px] leading-8 text-bg-11">
          {action.title}
        </h1>
        <p className="mt-2 text-[14px] text-bg-9">
          {action.target_label ?? "Системная операция"}
        </p>
        <div className="mt-4">
          <DataStateBadge state={projection.state} compact />
        </div>
        {projection.state !== "ready" ? (
          <div className="mt-3">
            <DataStateNotice
              state={projection.state}
              issues={projection.issues}
              compact
            />
          </div>
        ) : null}
        <div
          className={`mt-3 rounded-[var(--radius-2)] border p-3 text-[14px] font-semibold ${action.state === "confirmed" ? "border-success/40 bg-success-bg text-success" : action.state === "failed" ? "border-danger/40 bg-danger-bg text-danger" : action.state === "cancelled" ? "border-[var(--color-hairline-strong)] bg-bg-2 text-bg-9" : "border-warning/40 bg-warning-bg text-warning"}`}
        >
          {ACTION_STATE_LABEL[action.state]}
        </div>
        {action.state === "unknown" ? (
          <p role="status" className="mt-3 text-[14px] leading-5 text-bg-10">
            {OPERATOR_UNKNOWN_RESULT_NOTICE}
          </p>
        ) : null}
      </header>
      <dl className="mt-4 divide-y divide-[var(--color-hairline)] rounded-[var(--radius-3)] border border-[var(--color-hairline)] bg-bg-1">
        <Row
          label="Запрошено"
          value={
            action.cabinet_timezone
              ? formatZonedDateTime(
                  action.requested_at,
                  action.cabinet_timezone,
                )
              : "—"
          }
        />
        <Row
          label="Обновлено"
          value={
            action.cabinet_timezone
              ? formatZonedDateTime(action.updated_at, action.cabinet_timezone)
              : "—"
          }
        />
        <Row
          label="Часовой пояс"
          value={timezoneEvidenceLabel(
            action.cabinet_timezone,
            action.cabinet_timezone ? "single" : "unknown",
          )}
        />
        <Row label="Инициатор" value={action.requested_by ?? "не указан"} />
        {/* Состояние уже названо чипом выше; здесь — причина исхода из события. */}
        <Row label="Причина" value={operatorActionReason(action)} />
      </dl>
      {/* Способ закрыть терминальный неизвестный исход (#360): иначе строка
          остаётся «требует сверки» навсегда. */}
      <div className="mt-4">
        <ManualReviewPanel
          compact
          review={action.manual_review}
          available={action.manual_review_available === true}
          automationStoppedReason={action.automation_stopped_reason}
          reviewedAtLabel={
            action.manual_review && action.cabinet_timezone
              ? formatZonedDateTime(
                  action.manual_review.at,
                  action.cabinet_timezone,
                )
              : null
          }
          busy={manualReview.isPending}
          errorMessage={
            manualReview.isError
              ? operatorProblemMessage(manualReview.error)
              : null
          }
          onSubmit={(observation: ManualReviewObservation) => {
            manualReview.mutate(
              {
                params: { path: { task_id: Number(action.id) } },
                body: { observation },
              },
              { onSuccess: () => void query.refetch() },
            );
          }}
        />
      </div>
      {recovery?.destination === "target" && action.target_id ? (
        <Link
          to="/ads/$fbAdId"
          params={{ fbAdId: action.target_id }}
          className="mt-4 inline-flex min-h-11 w-full items-center justify-center gap-2 rounded-[var(--radius-2)] border border-[var(--color-hairline-strong)] bg-bg-1 px-4 text-[14px] font-semibold text-bg-11 focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-accent"
        >
          {recovery.label}
          <ArrowRight size={16} aria-hidden="true" />
        </Link>
      ) : recovery?.destination === "sources" ? (
        <Link
          to="/system/sources"
          className="mt-4 inline-flex min-h-11 w-full items-center justify-center gap-2 rounded-[var(--radius-2)] border border-[var(--color-hairline-strong)] bg-bg-1 px-4 text-[14px] font-semibold text-bg-11 focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-accent"
        >
          {recovery.label}
          <ArrowRight size={16} aria-hidden="true" />
        </Link>
      ) : null}
    </article>
  );
}

function Row({ label, value }: { label: string; value: string }) {
  return (
    <div className="p-4">
      <dt className="text-[12px] font-semibold uppercase tracking-[.05em] text-bg-8">
        {label}
      </dt>
      <dd className="m-0 mt-2 break-all text-[14px] leading-5 text-bg-11">
        {value}
      </dd>
    </div>
  );
}
