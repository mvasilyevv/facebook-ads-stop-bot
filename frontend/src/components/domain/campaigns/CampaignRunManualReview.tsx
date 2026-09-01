import type { ReactNode } from "react";
import { AlertTriangle, CheckCircle2, ExternalLink } from "lucide-react";
import { adsManagerCampaignUrl, campaignMetaIdGroups } from "@fb/shared";
import { formatZonedDateTime } from "@fb/shared/format/time";
import type { ManualReviewObservation } from "@fb/shared/operator/manualReview";
import { ManualReviewPanel } from "@fb/operator-ui";

import { buttonStyles } from "@/components/ui/Button";
import { cn } from "@/lib/utils/cn";
import {
  operatorProblemMessage,
  useOperatorAction,
  useRecordOperatorManualReview,
} from "@/lib/api/operator";

interface CampaignRunManualReviewProps {
  createdMetaIds: Record<string, unknown>;
  /**
   * Задача залива. Ручная сверка живёт на ней, а не на инциденте: только так
   * закрытие переживает перезагрузку экрана (#360). Без идентификатора остаётся
   * прежний баннер без способа закрыться — но и запроса тогда не делаем.
   */
  taskId?: number | null;
}

export function CampaignRunManualReview({
  createdMetaIds,
  taskId,
}: CampaignRunManualReviewProps) {
  if (typeof taskId === "number" && Number.isSafeInteger(taskId) && taskId > 0) {
    return <ClosableManualReview createdMetaIds={createdMetaIds} taskId={taskId} />;
  }
  return <ManualReviewFrame createdMetaIds={createdMetaIds} />;
}

/**
 * Баннер, который умеет закрыться: читает факт сверки с задачи и даёт его
 * записать. Исход залива при этом остаётся неизвестным — закрывается вопрос
 * сверки, а не результат создания.
 */
function ClosableManualReview({
  createdMetaIds,
  taskId,
}: {
  createdMetaIds: Record<string, unknown>;
  taskId: number;
}) {
  // Факт сверки хранится на задаче, поэтому читается из ленты действий: ответ
  // залива о нём ничего не знает.
  const actionQuery = useOperatorAction(String(taskId));
  const action = actionQuery.data?.data ?? null;
  const manualReview = useRecordOperatorManualReview();
  const questionClosed = action?.manual_review?.question_closed === true;

  return (
    <ManualReviewFrame createdMetaIds={createdMetaIds} questionClosed={questionClosed}>
      {action ? (
        <div className="mt-4">
          <ManualReviewPanel
            compact
            review={action.manual_review}
            available={action.manual_review_available === true}
            automationStoppedReason={action.automation_stopped_reason}
            reviewedAtLabel={
              action.manual_review && action.cabinet_timezone
                ? formatZonedDateTime(action.manual_review.at, action.cabinet_timezone)
                : null
            }
            busy={manualReview.isPending}
            errorMessage={
              manualReview.isError ? operatorProblemMessage(manualReview.error) : null
            }
            onSubmit={(observation: ManualReviewObservation) => {
              manualReview.mutate(
                {
                  params: { path: { task_id: taskId } },
                  body: { observation },
                },
                { onSuccess: () => void actionQuery.refetch() },
              );
            }}
          />
        </div>
      ) : null}
    </ManualReviewFrame>
  );
}

function ManualReviewFrame({
  createdMetaIds,
  questionClosed = false,
  children,
}: {
  createdMetaIds: Record<string, unknown>;
  questionClosed?: boolean;
  children?: ReactNode;
}) {
  const groups = campaignMetaIdGroups(createdMetaIds);
  const populatedGroups = groups.filter((group) => group.ids.length > 0);
  const adsManagerUrl = adsManagerCampaignUrl(createdMetaIds);

  return (
    <section
      role={questionClosed ? "status" : "alert"}
      aria-label={questionClosed ? "Сверено вручную" : "Требуется ручная сверка"}
      className={cn(
        "rounded-[var(--radius-3)] border p-4",
        questionClosed
          ? "border-[var(--color-hairline-strong)] bg-bg-2 text-bg-10"
          : "border-warning/40 bg-warning/10 text-warning",
      )}
    >
      <div className="flex items-start gap-3">
        {questionClosed ? (
          <CheckCircle2 size={18} className="mt-0.5 shrink-0" aria-hidden="true" />
        ) : (
          <AlertTriangle size={18} className="mt-0.5 shrink-0" aria-hidden="true" />
        )}
        <div className="min-w-0 flex-1">
          <h3 className="m-0 font-display text-[14px] font-medium text-bg-11">
            {questionClosed ? "Сверено вручную" : "Требуется ручная сверка"}
          </h3>
          <p className="mt-1 text-[13px] leading-5 text-bg-9">
            {questionClosed
              ? "Оператор проверил кабинет и записал, что увидел. Исход самого запроса остался неизвестным — не повторяйте запуск."
              : "Результат создания неоднозначен. Не повторяйте запуск и не удаляйте объекты вслепую — сначала проверьте их фактическое состояние в Ads Manager."}
          </p>
        </div>
      </div>

      {populatedGroups.length > 0 ? (
        <div className="mt-4 space-y-3">
          {populatedGroups.map((group) => (
            <div key={group.key}>
              <div className="text-[12px] font-semibold uppercase tracking-wider text-bg-8">
                {group.label} · {group.ids.length}
              </div>
              <div className="mt-1 break-all font-numeric text-[12px] leading-5 text-bg-11">
                {group.ids.join(", ")}
              </div>
            </div>
          ))}
        </div>
      ) : (
        <p className="mt-3 rounded-[var(--radius-2)] bg-bg-2 px-3 py-2 text-[12px] leading-5 text-bg-9">
          Подтверждённых идентификаторов в Meta нет. Это не доказывает, что объект не был создан:
          ответ мог потеряться после внешнего вызова.
        </p>
      )}

      {adsManagerUrl ? (
        <a
          href={adsManagerUrl}
          target="_blank"
          rel="noopener noreferrer"
          className={cn(
            buttonStyles({ variant: "secondary", size: "sm" }),
            "mt-4 w-full sm:w-auto",
          )}
        >
          Открыть Ads Manager
          <ExternalLink size={14} aria-hidden="true" />
        </a>
      ) : null}

      {children}
    </section>
  );
}
