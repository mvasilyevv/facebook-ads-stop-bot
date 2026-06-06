/**
 * Тесты Pagination — диапазон, prev/next, disabled-состояния.
 */

import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, it, expect, vi } from "vitest";
import { Pagination } from "@/components/data/table/Pagination";

describe("Pagination", () => {
  // Показывает диапазон "1–50 из 247"
  it("отображает диапазон X–Y из total", () => {
    render(
      <Pagination
        offset={0}
        pageSize={50}
        total={247}
        onPrev={vi.fn()}
        onNext={vi.fn()}
      />,
    );
    expect(screen.getByText(/1/)).toBeInTheDocument();
    expect(screen.getByText(/50/)).toBeInTheDocument();
    expect(screen.getByText(/247/)).toBeInTheDocument();
  });

  // Вторая страница: offset=50, показывает 51–100
  it("вторая страница показывает 51–100", () => {
    render(
      <Pagination
        offset={50}
        pageSize={50}
        total={247}
        onPrev={vi.fn()}
        onNext={vi.fn()}
      />,
    );
    expect(screen.getByText(/51/)).toBeInTheDocument();
    expect(screen.getByText(/100/)).toBeInTheDocument();
  });

  // Первая страница: кнопка Назад disabled
  it("на первой странице кнопка Назад disabled", () => {
    render(
      <Pagination
        offset={0}
        pageSize={50}
        total={247}
        onPrev={vi.fn()}
        onNext={vi.fn()}
      />,
    );
    expect(screen.getByLabelText("Предыдущая страница")).toBeDisabled();
  });

  // Последняя страница: кнопка Вперёд disabled
  it("на последней странице кнопка Вперёд disabled", () => {
    render(
      <Pagination
        offset={200}
        pageSize={47}
        total={247}
        onPrev={vi.fn()}
        onNext={vi.fn()}
      />,
    );
    expect(screen.getByLabelText("Следующая страница")).toBeDisabled();
  });

  // Клик Назад вызывает onPrev
  it("клик Назад вызывает onPrev", async () => {
    const user = userEvent.setup();
    const onPrev = vi.fn();
    render(
      <Pagination
        offset={50}
        pageSize={50}
        total={247}
        onPrev={onPrev}
        onNext={vi.fn()}
      />,
    );
    await user.click(screen.getByLabelText("Предыдущая страница"));
    expect(onPrev).toHaveBeenCalledTimes(1);
  });

  // Клик Вперёд вызывает onNext
  it("клик Вперёд вызывает onNext", async () => {
    const user = userEvent.setup();
    const onNext = vi.fn();
    render(
      <Pagination
        offset={0}
        pageSize={50}
        total={247}
        onPrev={vi.fn()}
        onNext={onNext}
      />,
    );
    await user.click(screen.getByLabelText("Следующая страница"));
    expect(onNext).toHaveBeenCalledTimes(1);
  });

  // Без total — не показывает "из N"
  it("без total не показывает 'из N'", () => {
    render(
      <Pagination
        offset={0}
        pageSize={50}
        total={null}
        onPrev={vi.fn()}
        onNext={vi.fn()}
      />,
    );
    expect(screen.queryByText(/из/)).not.toBeInTheDocument();
  });

  // Если страница одна — компонент не рендерится
  it("не рендерится если данных меньше pageSize", () => {
    const { container } = render(
      <Pagination
        offset={0}
        pageSize={10}
        total={5}
        onPrev={vi.fn()}
        onNext={vi.fn()}
      />,
    );
    expect(container.firstChild).toBeNull();
  });
});
