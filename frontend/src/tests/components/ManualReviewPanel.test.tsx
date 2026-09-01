import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it, vi } from "vitest";

import { ManualReviewPanel } from "@fb/operator-ui";
import type { OperatorActionManualReview } from "@fb/shared/operator/contracts";

/**
 * Панель ручной сверки терминального неизвестного исхода (#360).
 *
 * Проверяется ровно то, ради чего она заведена: закрытие требует названного
 * наблюдения, «всё ещё активен» вопрос не закрывает, а интерфейс нигде не
 * называет исход подтверждённым.
 */

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

describe("ManualReviewPanel", () => {
  it("не даёт закрыть вопрос, пока наблюдение не выбрано", async () => {
    const onSubmit = vi.fn();
    render(<ManualReviewPanel review={null} available onSubmit={onSubmit} />);

    const submit = screen.getByRole("button", { name: /Записать наблюдение/ });
    expect(submit).toBeDisabled();

    await userEvent.click(submit);
    expect(onSubmit).not.toHaveBeenCalled();
  });

  it("отправляет выбранное наблюдение, а не абстрактное «ок»", async () => {
    const onSubmit = vi.fn();
    render(<ManualReviewPanel review={null} available onSubmit={onSubmit} />);

    await userEvent.click(screen.getByRole("radio", { name: /Объект остановлен/ }));
    await userEvent.click(screen.getByRole("button", { name: /Записать наблюдение/ }));

    expect(onSubmit).toHaveBeenCalledWith("stopped");
  });

  it("говорит, почему автоматика больше не пытается", () => {
    render(
      <ManualReviewPanel
        review={null}
        available
        automationStoppedReason="Автоматическая сверка исчерпала попытки"
        onSubmit={vi.fn()}
      />,
    );

    expect(screen.getByText(/исчерпала попытки/)).toBeInTheDocument();
  });

  it("после закрытия оставляет след, а не пустое место", () => {
    render(
      <ManualReviewPanel
        review={review()}
        available
        reviewedAtLabel="31 авг, 15:00"
        onSubmit={vi.fn()}
      />,
    );

    expect(screen.getByText(/Объект остановлен/)).toBeInTheDocument();
    expect(screen.getByText(/operator:web/)).toBeInTheDocument();
    expect(screen.getByText(/31 авг, 15:00/)).toBeInTheDocument();
    // Закрытая сверка убирает форму, но не прячет запись.
    expect(screen.queryByRole("radio", { name: /Объект остановлен/ })).toBeNull();
  });

  it("«всё ещё активен» вопрос не закрывает и требует команды", () => {
    render(
      <ManualReviewPanel
        review={review({ observation: "active", question_closed: false })}
        available
        onSubmit={vi.fn()}
      />,
    );

    expect(screen.getByText(/Вопрос не закрыт/)).toBeInTheDocument();
    // Форма остаётся открытой: вопрос ещё живой.
    expect(screen.getByRole("radio", { name: /Объект остановлен/ })).toBeInTheDocument();
  });

  it("никогда не называет исход подтверждённым", () => {
    const { container } = render(
      <ManualReviewPanel review={null} available onSubmit={vi.fn()} />,
    );

    expect(container.textContent).toContain("останется неизвестным");
    expect(container.textContent).not.toMatch(/подтверждён(о|а)?\b/);
  });

  it("молчит, пока сверка недоступна и её ещё не было", () => {
    const { container } = render(
      <ManualReviewPanel review={null} available={false} onSubmit={vi.fn()} />,
    );

    expect(container).toBeEmptyDOMElement();
  });
});
