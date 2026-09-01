import { describe, expect, it } from "vitest";

import type { OperatorActionManualReview } from "../contracts";
import {
  MANUAL_REVIEW_OPTIONS,
  manualReviewObservationLabel,
  manualReviewOutstanding,
  manualReviewRecordedSummary,
} from "../manualReview";

function review(
  overrides: Partial<OperatorActionManualReview> = {},
): OperatorActionManualReview {
  return {
    observation: "stopped",
    at: "2026-08-31T12:00:00Z",
    by: "operator:web",
    question_closed: true,
    ...overrides,
  };
}

describe("ручная сверка неизвестного исхода", () => {
  it("предлагает ровно три наблюдения и ни одной кнопки «ок»", () => {
    expect(MANUAL_REVIEW_OPTIONS.map((option) => option.value)).toEqual([
      "stopped",
      "active",
      "missing",
    ]);
    for (const option of MANUAL_REVIEW_OPTIONS) {
      expect(option.hint.length).toBeGreaterThan(0);
    }
  });

  it("не показывает незнакомое значение с сервера как есть", () => {
    expect(manualReviewObservationLabel("ok")).toBe("Наблюдение не записано");
    expect(manualReviewObservationLabel("stopped")).toBe("Объект остановлен");
  });

  it("оставляет след: что увидели, кто и когда", () => {
    const summary = manualReviewRecordedSummary(review(), "31 авг, 15:00");

    expect(summary).toContain("Объект остановлен");
    expect(summary).toContain("operator:web");
    expect(summary).toContain("31 авг, 15:00");
  });

  it("не выдумывает время, если часового пояса кабинета нет", () => {
    expect(manualReviewRecordedSummary(review(), null)).not.toContain("undefined");
  });

  it("закрытая сверка снимает требование разобраться", () => {
    expect(
      manualReviewOutstanding({
        manual_review: review(),
        manual_review_available: true,
      }),
    ).toBe(false);
  });

  it("«всё ещё активен» вопрос не закрывает", () => {
    expect(
      manualReviewOutstanding({
        manual_review: review({ observation: "active", question_closed: false }),
        manual_review_available: true,
      }),
    ).toBe(true);
  });

  it("несверенная терминальная задача остаётся требующей разбора", () => {
    expect(
      manualReviewOutstanding({ manual_review: null, manual_review_available: true }),
    ).toBe(true);
  });

  it("пока автоматика работает, оператора не зовут", () => {
    expect(
      manualReviewOutstanding({ manual_review: null, manual_review_available: false }),
    ).toBe(false);
  });
});
