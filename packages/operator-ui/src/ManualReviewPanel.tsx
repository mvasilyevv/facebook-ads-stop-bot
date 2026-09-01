import { useId, useState } from "react";

import type { OperatorActionManualReview } from "@fb/shared/operator/contracts";
import {
  MANUAL_REVIEW_HONESTY_NOTE,
  MANUAL_REVIEW_OPTIONS,
  MANUAL_REVIEW_PROMPT,
  MANUAL_REVIEW_REOPEN_LABEL,
  MANUAL_REVIEW_STILL_ACTIVE_NOTICE,
  MANUAL_REVIEW_SUBMIT_LABEL,
  MANUAL_REVIEW_TITLE,
  manualReviewRecordedSummary,
  type ManualReviewObservation,
} from "@fb/shared/operator/manualReview";

export interface ManualReviewPanelProps {
  /** Зафиксированный факт сверки, если он уже есть. */
  review: OperatorActionManualReview | null | undefined;
  /** Разрешает ли сервер записывать сверку по этой задаче. */
  available: boolean;
  /** Почему автоматика больше не пытается. null — причина не записана. */
  automationStoppedReason?: string | null;
  /** Время сверки, уже приведённое к часовому поясу кабинета. */
  reviewedAtLabel?: string | null;
  busy?: boolean;
  errorMessage?: string | null;
  onSubmit: (observation: ManualReviewObservation) => void;
  compact?: boolean;
}

/**
 * Панель ручной сверки терминального неизвестного исхода.
 *
 * Уровень трения соразмерен: это не команда в Meta, но и не «ок» одним
 * кликом — без выбранного наблюдения кнопка не активна. Записанная сверка не
 * прячет историю: она остаётся видимой строкой, а не исчезнувшим баннером.
 */
export function ManualReviewPanel({
  review,
  available,
  automationStoppedReason,
  reviewedAtLabel,
  busy = false,
  errorMessage,
  onSubmit,
  compact = false,
}: ManualReviewPanelProps) {
  const groupId = useId();
  const [choice, setChoice] = useState<ManualReviewObservation | null>(null);
  const [reopened, setReopened] = useState(false);

  const questionClosed = review?.question_closed === true;
  const recorded = manualReviewRecordedSummary(review ?? null, reviewedAtLabel ?? null);
  const formOpen = available && (!review || !questionClosed || reopened);

  if (!available && !review) return null;

  return (
    <section
      aria-label={MANUAL_REVIEW_TITLE}
      className={`rounded-[var(--radius-3)] border border-[var(--color-hairline-strong)] bg-bg-1 ${
        compact ? "p-4" : "p-5"
      }`}
    >
      <h3 className="m-0 font-display text-[16px] font-medium text-bg-11">{MANUAL_REVIEW_TITLE}</h3>

      {automationStoppedReason ? (
        <p className="mt-2 text-[13px] leading-5 text-warning">
          Автоматика остановилась: {automationStoppedReason}.
        </p>
      ) : null}

      {recorded ? (
        <p
          className={`mt-3 rounded-[var(--radius-2)] px-3 py-2 text-[13px] leading-5 ${
            questionClosed ? "bg-bg-2 text-bg-10" : "bg-warning-bg text-warning"
          }`}
        >
          {recorded}
        </p>
      ) : null}

      {review && !questionClosed ? (
        <p role="status" className="mt-2 text-[13px] leading-5 text-bg-10">
          {MANUAL_REVIEW_STILL_ACTIVE_NOTICE}
        </p>
      ) : null}

      {formOpen ? (
        <>
          <p className="mt-3 text-[13px] leading-5 text-bg-9">{MANUAL_REVIEW_PROMPT}</p>
          <fieldset className="mt-3 border-0 p-0">
            <legend className="sr-only">Что видно в кабинете</legend>
            <div className="grid gap-2">
              {MANUAL_REVIEW_OPTIONS.map((option) => (
                <label
                  key={option.value}
                  className={`flex min-h-11 cursor-pointer items-start gap-3 rounded-[var(--radius-2)] border p-3 text-[13px] leading-5 ${
                    choice === option.value
                      ? "border-accent bg-accent-bg text-bg-11"
                      : "border-[var(--color-hairline)] text-bg-10 hover:bg-bg-2"
                  }`}
                >
                  <input
                    type="radio"
                    name={groupId}
                    value={option.value}
                    checked={choice === option.value}
                    disabled={busy}
                    onChange={() => setChoice(option.value)}
                    className="mt-0.5"
                  />
                  <span className="min-w-0">
                    <span className="block font-semibold text-bg-11">{option.label}</span>
                    <span className="mt-0.5 block text-bg-9">{option.hint}</span>
                  </span>
                </label>
              ))}
            </div>
          </fieldset>
          <p className="mt-3 text-[12px] leading-5 text-bg-8">{MANUAL_REVIEW_HONESTY_NOTE}</p>
          {errorMessage ? (
            <p role="alert" className="mt-2 text-[13px] leading-5 text-danger">
              {errorMessage}
            </p>
          ) : null}
          <button
            type="button"
            disabled={busy || choice === null}
            onClick={() => {
              if (choice !== null) onSubmit(choice);
            }}
            className="mt-3 inline-flex min-h-11 w-full items-center justify-center rounded-[var(--radius-2)] bg-bg-3 px-4 text-[14px] font-semibold text-bg-11 hover:bg-bg-4 disabled:cursor-not-allowed disabled:opacity-50 focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-accent sm:w-auto"
          >
            {busy ? "Записываю…" : MANUAL_REVIEW_SUBMIT_LABEL}
          </button>
        </>
      ) : available ? (
        <button
          type="button"
          onClick={() => setReopened(true)}
          className="mt-3 inline-flex min-h-11 items-center rounded-[var(--radius-2)] px-3 text-[14px] font-semibold text-bg-10 hover:bg-bg-2 focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-accent"
        >
          {MANUAL_REVIEW_REOPEN_LABEL}
        </button>
      ) : null}
    </section>
  );
}
