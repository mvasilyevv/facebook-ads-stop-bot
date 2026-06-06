/**
 * Тесты DiffTable — 3-колоночная таблица сравнения.
 */

import { render, screen } from "@testing-library/react";
import { describe, it, expect } from "vitest";
import { DiffTable } from "@/components/domain/drafts/DiffTable";
import { buildDraftDiff } from "@fb/shared";
import type { DiffRow } from "@fb/shared";

// Вспомогательная фабрика строки
function makeRow(overrides: Partial<DiffRow>): DiffRow {
  return {
    field: "Тестовое поле",
    current: "старое",
    target: "новое",
    changed: true,
    ...overrides,
  };
}

describe("DiffTable", () => {
  // Проверяем, что заголовок таблицы показывает 3 колонки
  it("рендерит заголовки Field / Current / Target", () => {
    render(<DiffTable rows={[makeRow({})]} />);
    expect(screen.getByText("Field")).toBeInTheDocument();
    expect(screen.getByText("Current")).toBeInTheDocument();
    expect(screen.getByText("Target")).toBeInTheDocument();
  });

  // changed=true → строка получает data-changed="true" и accent bg
  it("строка с changed=true получает data-changed=true", () => {
    const { container } = render(
      <DiffTable rows={[makeRow({ changed: true })]} />,
    );
    const changedRows = container.querySelectorAll('[data-changed="true"]');
    expect(changedRows.length).toBe(1);
  });

  // changed=false → строка НЕ получает data-changed="true"
  it("строка с changed=false НЕ получает data-changed=true", () => {
    const { container } = render(
      <DiffTable rows={[makeRow({ changed: false })]} />,
    );
    const changedRows = container.querySelectorAll('[data-changed="true"]');
    expect(changedRows.length).toBe(0);
  });

  // Неизменённая строка показывает плейсхолдер "— same —"
  it("строка с changed=false показывает плейсхолдер same в Target", () => {
    render(
      <DiffTable rows={[makeRow({ changed: false, current: "ACTIVE", target: "ACTIVE" })]} />,
    );
    // Должен быть минимум один "— same —"
    const sames = screen.getAllByText("— same —");
    expect(sames.length).toBeGreaterThan(0);
  });

  // changed=true → target-текст отображается
  it("изменённая строка показывает target-значение", () => {
    render(
      <DiffTable rows={[makeRow({ changed: true, target: "$350.00" })]} />,
    );
    expect(screen.getByText("$350.00")).toBeInTheDocument();
  });

  // Несколько строк — правильное количество data-changed
  it("корректно рендерит смесь changed=true и changed=false", () => {
    const rows: DiffRow[] = [
      makeRow({ field: "Бюджет", changed: true }),
      makeRow({ field: "Ad ID", changed: false }),
      makeRow({ field: "Статус", changed: true }),
    ];
    const { container } = render(<DiffTable rows={rows} />);
    const changedRows = container.querySelectorAll('[data-changed="true"]');
    expect(changedRows.length).toBe(2);
  });

  // Пустой массив → сообщение "Нет данных"
  it("при пустом rows показывает fallback-текст", () => {
    render(<DiffTable rows={[]} />);
    expect(screen.getByText("Нет данных для сравнения")).toBeInTheDocument();
  });

  // Интеграция с buildDraftDiff: pause_ad
  it("buildDraftDiff(pause_ad) генерирует строку changed=true", () => {
    const rows = buildDraftDiff(
      "pause_ad",
      { fb_ad_id: "120211984573_8761" },
      { status: "ACTIVE" },
    );
    const { container } = render(<DiffTable rows={rows} />);
    // Статус изменится → минимум одна changed-строка
    const changed = container.querySelectorAll('[data-changed="true"]');
    expect(changed.length).toBeGreaterThan(0);
  });

  // Интеграция с buildDraftDiff: set_adset_budget
  it("buildDraftDiff(set_adset_budget) показывает бюджет в Target", () => {
    const rows = buildDraftDiff(
      "set_adset_budget",
      { budget_cents: 35000, budget_type: "daily" },
      { daily_budget_cents: 20000 },
    );
    render(<DiffTable rows={rows} />);
    // Должен показать $350.00
    expect(screen.getByText("$350.00")).toBeInTheDocument();
    // Current $200.00
    expect(screen.getByText("$200.00")).toBeInTheDocument();
  });
});
