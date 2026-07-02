/**
 * Тест SpendChart: <2 точек → «Нет данных», null-точки (нет данных за бакет)
 * рисуются РАЗРЫВОМ линии/области, а не просадкой в ноль (аудит 02.07, LOW F2).
 */
import { describe, it, expect } from "vitest";
import { render, screen } from "@testing-library/react";
import { SpendChart } from "@/components/data/SpendChart";

describe("SpendChart", () => {
  // Меньше 2 точек — заглушка «Нет данных»
  it("показывает «Нет данных о тратах за период» при менее чем 2 точках", () => {
    render(<SpendChart data={[10]} animate={false} />);
    expect(screen.getByText("Нет данных о тратах за период")).toBeInTheDocument();
  });

  // Пустой массив
  it("показывает заглушку при пустом массиве", () => {
    render(<SpendChart data={[]} animate={false} />);
    expect(screen.getByText("Нет данных о тратах за период")).toBeInTheDocument();
  });

  // Все точки null (нет данных вообще) — тоже заглушка, не пустой график
  it("показывает заглушку когда все точки null", () => {
    render(<SpendChart data={[null, null, null]} animate={false} />);
    expect(screen.getByText("Нет данных о тратах за период")).toBeInTheDocument();
  });

  // 2+ валидные точки без разрывов — один непрерывный сегмент (один polyline)
  it("рендерит один polyline без разрывов", () => {
    const { container } = render(
      <SpendChart data={[10, 20, 15, 30]} animate={false} />,
    );
    const lines = container.querySelectorAll("polyline");
    expect(lines).toHaveLength(1);
    expect(screen.queryByText("Нет данных о тратах за период")).not.toBeInTheDocument();
  });

  // null посередине ряда — РАЗРЫВ: два отдельных сегмента линии/области,
  // а не один непрерывный (что было бы, если null тихо стал 0).
  it("null-точка посередине рисует разрыв (два сегмента, не один)", () => {
    const { container } = render(
      <SpendChart data={[10, 20, null, 25, 30]} animate={false} />,
    );
    const lines = container.querySelectorAll("polyline");
    const areas = container.querySelectorAll("path");
    expect(lines).toHaveLength(2);
    expect(areas).toHaveLength(2);
  });

  // Несколько null-разрывов подряд/по краям — сегментов ровно по числу
  // непрерывных участков валидных данных.
  it("несколько разрывов — по сегменту на каждый непрерывный участок", () => {
    const { container } = render(
      <SpendChart data={[null, 5, 10, null, null, 8, 12, 6]} animate={false} />,
    );
    const lines = container.querySelectorAll("polyline");
    expect(lines).toHaveLength(2);
  });
});
