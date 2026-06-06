/**
 * Тесты DataTable — сортировка, checkbox-select, виртуализация, row-variants.
 */

import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, it, expect, vi } from "vitest";
import { useState } from "react";
import { createColumnHelper, type SortingState } from "@tanstack/react-table";
import { DataTable, type RowVariant } from "@/components/data/table/DataTable";

// ─── Фиктивные данные ──────────────────────────────────────────────────────

interface TestRow {
  id: string;
  name: string;
  value: number;
  variant?: RowVariant;
}

const ROWS: TestRow[] = [
  { id: "a", name: "Alpha", value: 30, variant: "normal" },
  { id: "b", name: "Beta", value: 10, variant: "warning" },
  { id: "c", name: "Gamma", value: 20, variant: "stop" },
];

const helper = createColumnHelper<TestRow>();

const columns = [
  helper.accessor("name", {
    header: "Название",
    enableSorting: true,
  }),
  helper.accessor("value", {
    header: "Значение",
    enableSorting: true,
    meta: { align: "right" },
  }),
];

// ─── Обёртка с управляемым состоянием сортировки ──────────────────────────

function SortableWrapper({
  onSortChange,
}: {
  onSortChange?: (s: SortingState) => void;
}) {
  const [sorting, setSorting] = useState<SortingState>([]);

  function handleSortingChange(next: SortingState) {
    setSorting(next);
    onSortChange?.(next);
  }

  return (
    <DataTable
      data={ROWS}
      columns={columns}
      sorting={sorting}
      onSortingChange={handleSortingChange}
      getRowId={(r) => r.id}
    />
  );
}

// ─── Обёртка для checkbox-select ──────────────────────────────────────────

const checkboxHelper = createColumnHelper<TestRow>();

const selectColumns = [
  checkboxHelper.display({
    id: "select",
    header: ({ table }) => (
      <input
        type="checkbox"
        aria-label="select-all"
        checked={table.getIsAllRowsSelected()}
        ref={(el) => {
          if (el) el.indeterminate = table.getIsSomeRowsSelected();
        }}
        onChange={table.getToggleAllRowsSelectedHandler()}
      />
    ),
    cell: ({ row }) => (
      <input
        type="checkbox"
        aria-label={`select-${row.id}`}
        checked={row.getIsSelected()}
        onChange={row.getToggleSelectedHandler()}
      />
    ),
  }),
  checkboxHelper.accessor("name", { header: "Название" }),
];

function CheckboxWrapper({
  onSelectionChange,
}: {
  onSelectionChange?: (ids: string[]) => void;
}) {
  const [selection, setSelection] = useState<Record<string, boolean>>({});

  return (
    <DataTable
      data={ROWS}
      columns={selectColumns}
      rowSelection={selection}
      onRowSelectionChange={(next) => {
        setSelection(next);
        onSelectionChange?.(Object.keys(next).filter((k) => next[k]));
      }}
      getRowId={(r) => r.id}
    />
  );
}

// ─── Тесты ────────────────────────────────────────────────────────────────

describe("DataTable", () => {
  // Рендерит строки из data
  it("рендерит все строки таблицы", () => {
    render(
      <DataTable
        data={ROWS}
        columns={columns}
        getRowId={(r) => r.id}
      />,
    );
    expect(screen.getByText("Alpha")).toBeInTheDocument();
    expect(screen.getByText("Beta")).toBeInTheDocument();
    expect(screen.getByText("Gamma")).toBeInTheDocument();
  });

  // Сортировка по колонке: первый клик → desc, второй → asc, третий → сброс
  it("клик по заголовку переключает сортировку desc → asc", async () => {
    const user = userEvent.setup();
    const onChange = vi.fn();
    render(<SortableWrapper onSortChange={onChange} />);

    const header = screen.getByRole("button", { name: /Значение/i });

    // Первый клик → desc
    await user.click(header);
    expect(onChange).toHaveBeenLastCalledWith([{ id: "value", desc: true }]);

    // Второй клик → asc
    await user.click(header);
    expect(onChange).toHaveBeenLastCalledWith([{ id: "value", desc: false }]);

    // Третий клик → сброс
    await user.click(header);
    expect(onChange).toHaveBeenLastCalledWith([]);
  });

  // Сортировка по другой колонке меняет активную
  it("клик по другой колонке меняет sorting.id", async () => {
    const user = userEvent.setup();
    const onChange = vi.fn();
    render(<SortableWrapper onSortChange={onChange} />);

    await user.click(screen.getByRole("button", { name: /Значение/i }));
    await user.click(screen.getByRole("button", { name: /Название/i }));

    const lastCall = onChange.mock.lastCall?.[0] as SortingState | undefined;
    expect(lastCall?.[0]?.id).toBe("name");
  });

  // select-all → все строки выбраны
  it("select-all checkbox выбирает все строки", async () => {
    const user = userEvent.setup();
    const onChange = vi.fn();
    render(<CheckboxWrapper onSelectionChange={onChange} />);

    const selectAll = screen.getByLabelText("select-all");
    await user.click(selectAll);

    const lastCall = onChange.mock.lastCall?.[0] as string[];
    expect(lastCall).toContain("a");
    expect(lastCall).toContain("b");
    expect(lastCall).toContain("c");
  });

  // Выбрать только одну строку → indeterminate у select-all
  it("частичный выбор строк → select-all indeterminate", async () => {
    const user = userEvent.setup();
    render(<CheckboxWrapper />);

    // Выбираем только первую строку
    await user.click(screen.getByLabelText("select-a"));

    const selectAll = screen.getByLabelText("select-all") as HTMLInputElement;
    expect(selectAll.indeterminate).toBe(true);
    expect(selectAll.checked).toBe(false);
  });

  // select-all снимает выбор если все уже выбраны
  it("select-all снимает выбор если все выбраны", async () => {
    const user = userEvent.setup();
    const onChange = vi.fn();
    render(<CheckboxWrapper onSelectionChange={onChange} />);

    const selectAll = screen.getByLabelText("select-all");
    // Выбрать всё
    await user.click(selectAll);
    // Снять всё
    await user.click(selectAll);

    const lastCall = onChange.mock.lastCall?.[0] as string[];
    expect(lastCall).toHaveLength(0);
  });

  // Empty state при пустых данных
  it("показывает empty state при пустом data", () => {
    render(
      <DataTable
        data={[]}
        columns={columns}
        emptyState={<span>Нет данных</span>}
        getRowId={(r) => r.id}
      />,
    );
    expect(screen.getByText("Нет данных")).toBeInTheDocument();
  });

  // Скелетон при loading
  it("показывает скелетон при loading=true и не рендерит строки", () => {
    render(
      <DataTable
        data={ROWS}
        columns={columns}
        loading={true}
        skeletonRows={3}
        getRowId={(r) => r.id}
      />,
    );
    // Данные не должны отображаться
    expect(screen.queryByText("Alpha")).not.toBeInTheDocument();
  });

  // Row-variant классы: stop → data-variant="stop"
  it("stop-строка получает data-variant=stop", () => {
    render(
      <DataTable
        data={ROWS}
        columns={columns}
        getRowId={(r) => r.id}
        getRowVariant={(r) => r.variant ?? "normal"}
      />,
    );
    // Находим строку с текстом "Gamma" (variant=stop)
    const gammaCell = screen.getByText("Gamma");
    const row = gammaCell.closest("tr");
    expect(row).toHaveAttribute("data-variant", "stop");
  });

  // Row-variant warning
  it("warning-строка получает data-variant=warning", () => {
    render(
      <DataTable
        data={ROWS}
        columns={columns}
        getRowId={(r) => r.id}
        getRowVariant={(r) => r.variant ?? "normal"}
      />,
    );
    const betaCell = screen.getByText("Beta");
    const row = betaCell.closest("tr");
    expect(row).toHaveAttribute("data-variant", "warning");
  });

  // onRowClick вызывается при клике на строку
  it("onRowClick вызывается с оригинальной строкой", async () => {
    const user = userEvent.setup();
    const onClick = vi.fn();
    render(
      <DataTable
        data={ROWS}
        columns={columns}
        getRowId={(r) => r.id}
        onRowClick={onClick}
      />,
    );
    await user.click(screen.getByText("Alpha"));
    expect(onClick).toHaveBeenCalledWith(ROWS[0]);
  });

  // Виртуализация: при 1000 строках DOM содержит subset
  it("виртуализация рендерит подмножество при 1000 строках", () => {
    // Генерируем 1000 строк
    const bigData: TestRow[] = Array.from({ length: 1000 }, (_, i) => ({
      id: `row-${i}`,
      name: `Row ${i}`,
      value: i,
    }));

    render(
      <DataTable
        data={bigData}
        columns={columns}
        getRowId={(r) => r.id}
        containerHeight={400}
      />,
    );

    // DOM должен содержать меньше 1000 строк (виртуализация + spacer)
    const rows = screen.getAllByRole("row");
    // thead row (1) + spacer (1) + visible rows (< 1000) → значительно меньше 1001
    expect(rows.length).toBeLessThan(100);
  });
});
