import type { OperatorActionManualReview } from "./contracts";

/**
 * Ручная сверка неизвестного исхода — один источник текста для обоих фронтов.
 *
 * Смысл, который эти строки обязаны донести: оператор фиксирует НАБЛЮДЕНИЕ, а
 * не подтверждает результат. Исход внешней операции остаётся неизвестным и
 * после сверки — иначе интерфейс соврёт про деньги.
 */

export type ManualReviewObservation = "stopped" | "active" | "missing";

export interface ManualReviewOption {
  value: ManualReviewObservation;
  label: string;
  hint: string;
}

/**
 * Три исхода осмотра и ничего больше. Кнопки «ок» здесь нет по замыслу:
 * закрытие без названного наблюдения — это стирание баннера, а не сверка.
 */
export const MANUAL_REVIEW_OPTIONS: readonly ManualReviewOption[] = [
  {
    value: "stopped",
    label: "Объект остановлен",
    hint: "В кабинете он выключен и бюджет не тратит.",
  },
  {
    value: "active",
    label: "Объект всё ещё активен",
    hint: "Он продолжает работать. Вопрос останется открытым — нужна команда.",
  },
  {
    value: "missing",
    label: "Объекта в кабинете нет",
    hint: "Найти его не удалось: он не был создан или уже удалён.",
  },
] as const;

export const MANUAL_REVIEW_TITLE = "Сверка вручную";

export const MANUAL_REVIEW_PROMPT =
  "Откройте кабинет и отметьте, что видите своими глазами. Система запишет наблюдение с вашим именем и временем.";

/** Почему после сверки исход всё равно не «подтверждено». */
export const MANUAL_REVIEW_HONESTY_NOTE =
  "Исход внешней операции останется неизвестным: сверка — отдельный факт, а не подтверждение результата.";

export const MANUAL_REVIEW_SUBMIT_LABEL = "Записать наблюдение";

export const MANUAL_REVIEW_REOPEN_LABEL = "Сверить заново";

/** Оператор увидел объект живым: закрывать тут нечего. */
export const MANUAL_REVIEW_STILL_ACTIVE_NOTICE =
  "Вопрос не закрыт: объект работает. Отправьте команду отключения и дождитесь её исхода — сверка сама ничего не выключает.";

export const MANUAL_REVIEW_UNAVAILABLE_NOTICE =
  "Система ещё сверяет исход сама. Дождитесь, пока она закончит.";

/** Незнакомое значение с сервера не показывается как есть. */
export function manualReviewObservationLabel(value: unknown): string {
  const option = MANUAL_REVIEW_OPTIONS.find((item) => item.value === value);
  return option ? option.label : "Наблюдение не записано";
}

/**
 * Строка следа: что записано, кем и когда. `at` приходит уже отформатированным
 * — время считает оболочка, у которой есть часовой пояс кабинета.
 */
export function manualReviewRecordedSummary(
  review: OperatorActionManualReview | null | undefined,
  formattedAt: string | null,
): string | null {
  if (!review) return null;
  const parts = [manualReviewObservationLabel(review.observation)];
  if (review.by) parts.push(`сверил ${review.by}`);
  if (formattedAt) parts.push(formattedAt);
  return parts.join(" · ");
}

/**
 * Нужно ли всё ещё звать оператора на сверку.
 *
 * Закрытая сверка баннер снимает, но запись остаётся видимой: след, а не
 * стирание. Наблюдение «активен» сверку не закрывает.
 */
export function manualReviewOutstanding(action: {
  manual_review_available?: boolean | null;
  manual_review?: OperatorActionManualReview | null;
}): boolean {
  if (action.manual_review) return !action.manual_review.question_closed;
  return action.manual_review_available === true;
}
