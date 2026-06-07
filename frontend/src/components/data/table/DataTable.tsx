/**
 * DataTable — generic виртуализованная таблица с сортировкой и чекбоксами.
 *
 * Архитектура:
 *   - @tanstack/react-table для column-defs API, сортировки, row-selection
 *   - @tanstack/react-virtual для виртуализации (1000+ строк без лагов)
 *   - Density-aware: --table-row-height из ui-store
 *   - Row-variants: warning / stop / selected (через rowVariant accessor)
 *   - .num right-align — задаётся через meta.align = "right" в column def
 *
 * Использование:
 *   const cols = useMemo<ColumnDef<MyRow>[]>(() => [...], []);
 *   <DataTable data={rows} columns={cols} />
 */

import { useRef, type ReactNode } from "react";
import {
  flexRender,
  getCoreRowModel,
  getSortedRowModel,
  useReactTable,
  type ColumnDef,
  type SortingState,
  type RowSelectionState,
  type Row,
} from "@tanstack/react-table";
import { useVirtualizer } from "@tanstack/react-virtual";
import { ArrowDown, ArrowUp, ArrowUpDown } from "lucide-react";
import { useUiStore, DENSITY_ROW_HEIGHT } from "@/stores/ui";
import { cn } from "@/lib/utils/cn";

// ─── Публичный API ───────────────────────────────────────────────────────────

/**
 * Мета-расширение column def — позволяет задать right-align (.num),
 * sticky-left позицию, цветовой вариант ячейки.
 */
export interface ColumnMeta {
  /** Выравнивание ячеек колонки. */
  align?: "left" | "right" | "center";
}

/**
 * Вариант строки для подсветки. Определяется коллбэком getRowVariant.
 * selected — приоритет, stop — красный, warning — жёлтый.
 */
export type RowVariant = "normal" | "warning" | "stop" | "selected";

export interface DataTableProps<T> {
  data: T[];
  columns: ColumnDef<T, unknown>[];

  /** Управление сортировкой снаружи (controlled). */
  sorting?: SortingState;
  onSortingChange?: (sorting: SortingState) => void;

  /** Управление выбором строк снаружи (controlled). */
  rowSelection?: RowSelectionState;
  onRowSelectionChange?: (selection: RowSelectionState) => void;

  /** Ключ строки для идентификации (по умолчанию — индекс). */
  getRowId?: (row: T) => string;

  /** Колбэк для определения визуального варианта строки. */
  getRowVariant?: (row: T) => RowVariant;

  /** Клик по строке — открыть drawer и т.п. */
  onRowClick?: (row: T) => void;

  /**
   * Высота контейнера для виртуализации (px).
   * Если не задана — виртуализация отключается (для коротких списков).
   */
  containerHeight?: number;

  /** Empty state при пустых данных. */
  emptyState?: ReactNode;

  /** Показывать скелетон-строки вместо данных. */
  loading?: boolean;

  /** Количество скелетон-строк при loading. */
  skeletonRows?: number;

  /** aria-label для области таблицы. */
  label?: string;
}

// ─── Компонент ───────────────────────────────────────────────────────────────

export function DataTable<T>({
  data,
  columns,
  sorting,
  onSortingChange,
  rowSelection,
  onRowSelectionChange,
  getRowId,
  getRowVariant,
  onRowClick,
  containerHeight,
  emptyState,
  loading = false,
  skeletonRows = 8,
  label = "Таблица данных",
}: DataTableProps<T>) {
  const density = useUiStore((s) => s.density);
  const rowHeight = DENSITY_ROW_HEIGHT[density];

  // ─── TanStack Table ──────────────────────────────────────────────────────
  // eslint-disable-next-line react-hooks/incompatible-library -- TanStack Table v8 несовместим с React Compiler
  const table = useReactTable<T>({
    data,
    columns,
    state: {
      sorting: sorting ?? [],
      rowSelection: rowSelection ?? {},
    },
    // Controlled sorting
    onSortingChange: (updater) => {
      if (!onSortingChange) return;
      const next = typeof updater === "function" ? updater(sorting ?? []) : updater;
      onSortingChange(next);
    },
    // Controlled row selection
    onRowSelectionChange: (updater) => {
      if (!onRowSelectionChange) return;
      const next = typeof updater === "function" ? updater(rowSelection ?? {}) : updater;
      onRowSelectionChange(next);
    },
    getCoreRowModel: getCoreRowModel(),
    getSortedRowModel: getSortedRowModel(),
    getRowId,
    enableRowSelection: !!onRowSelectionChange,
    enableMultiRowSelection: true,
    // Сортировка управляется снаружи — отключаем auto
    manualSorting: false,
  });

  // ─── Virtualization ──────────────────────────────────────────────────────
  const containerRef = useRef<HTMLDivElement | null>(null);
  const rows = table.getRowModel().rows;
  const virtualized = containerHeight !== undefined;

  const virtualizer = useVirtualizer({
    count: rows.length,
    getScrollElement: () => (virtualized ? containerRef.current : null),
    estimateSize: () => rowHeight,
    overscan: 12,
    enabled: virtualized,
  });

  const virtualItems = virtualized ? virtualizer.getVirtualItems() : null;
  const totalHeight = virtualized ? virtualizer.getTotalSize() : 0;

  // ─── Рендер ──────────────────────────────────────────────────────────────
  const colCount = columns.length;
  const showEmpty = !loading && rows.length === 0;

  return (
    <div
      ref={containerRef}
      role="region"
      aria-label={label}
      className="border border-bg-5 bg-bg-1 overflow-auto"
      style={virtualized ? { height: containerHeight } : undefined}
    >
      <table
        className="w-full border-collapse"
        style={{ fontVariantNumeric: "tabular-nums" }}
      >
        {/* Sticky thead */}
        <thead className="sticky top-0 z-[2] bg-bg-1">
          {table.getHeaderGroups().map((hg) => (
            <tr key={hg.id} className="border-b border-bg-5">
              {hg.headers.map((header) => {
                const meta = (header.column.columnDef.meta ?? {}) as ColumnMeta;
                const canSort = header.column.getCanSort();
                const sortDir = header.column.getIsSorted();
                const isRight = meta.align === "right";

                return (
                  <th
                    key={header.id}
                    scope="col"
                    aria-sort={
                      canSort && sortDir
                        ? sortDir === "asc"
                          ? "ascending"
                          : "descending"
                        : undefined
                    }
                    style={{ width: header.getSize() !== 150 ? header.getSize() : undefined }}
                    className={cn(
                      "px-3.5 py-3 bg-bg-1",
                      "font-display text-[10px] tracking-[0.12em] uppercase text-bg-8 font-medium",
                      isRight ? "text-right" : "text-left",
                    )}
                  >
                    {header.isPlaceholder ? null : canSort ? (
                      <button
                        type="button"
                        onClick={header.column.getToggleSortingHandler()}
                        className={cn(
                          "inline-flex items-center gap-1 transition-colors hover:text-bg-11",
                          isRight && "flex-row-reverse",
                          sortDir ? "text-accent" : "text-bg-8",
                        )}
                      >
                        {flexRender(header.column.columnDef.header, header.getContext())}
                        <span aria-hidden="true" className="text-[9px] leading-none">
                          {sortDir === "asc" ? (
                            <ArrowUp size={9} />
                          ) : sortDir === "desc" ? (
                            <ArrowDown size={9} />
                          ) : (
                            <ArrowUpDown size={9} className="opacity-40" />
                          )}
                        </span>
                      </button>
                    ) : (
                      flexRender(header.column.columnDef.header, header.getContext())
                    )}
                  </th>
                );
              })}
            </tr>
          ))}
        </thead>

        <tbody>
          {/* Скелетон при загрузке */}
          {loading ? (
            Array.from({ length: skeletonRows }, (_, i) => (
              <SkeletonRow key={i} colCount={colCount} rowHeight={rowHeight} />
            ))
          ) : virtualized && virtualItems ? (
            <>
              {/* Spacer-строка для виртуализации — компенсирует высоту невидимых строк */}
              <tr aria-hidden="true">
                <td
                  colSpan={colCount}
                  style={{ height: totalHeight, padding: 0, border: 0 }}
                />
              </tr>
              {virtualItems.map((vi) => {
                const row = rows[vi.index];
                if (!row) return null;
                return (
                  <DataRow
                    key={row.id}
                    row={row}
                    rowHeight={rowHeight}
                    variant={getRowVariant ? getRowVariant(row.original) : "normal"}
                    onClick={onRowClick ? () => onRowClick(row.original) : undefined}
                    // translateY без offset: spacer уже занял место
                    style={{ transform: `translateY(${vi.start - totalHeight}px)` }}
                  />
                );
              })}
            </>
          ) : (
            // Невиртуализованный режим (короткие списки)
            rows.map((row) => (
              <DataRow
                key={row.id}
                row={row}
                rowHeight={rowHeight}
                variant={getRowVariant ? getRowVariant(row.original) : "normal"}
                onClick={onRowClick ? () => onRowClick(row.original) : undefined}
              />
            ))
          )}

          {/* Empty state */}
          {showEmpty && (
            <tr>
              <td colSpan={colCount} className="py-12 text-center">
                {emptyState ?? (
                  <span className="text-bg-9 text-[13px]">Нет данных для отображения.</span>
                )}
              </td>
            </tr>
          )}
        </tbody>
      </table>
    </div>
  );
}

// ─── Вспомогательные субкомпоненты ───────────────────────────────────────────

/** Строка таблицы с вариантом подсветки. */
function DataRow<T>({
  row,
  rowHeight,
  variant,
  onClick,
  style,
}: {
  row: Row<T>;
  rowHeight: number;
  variant: RowVariant;
  onClick?: () => void;
  style?: React.CSSProperties;
}) {
  // selected-состояние берём из TanStack (row.getIsSelected) как fallback
  const isSelected = variant === "selected" || row.getIsSelected();

  const rowCls = cn(
    "border-b border-bg-3 last:border-b-0 transition-colors",
    onClick && "cursor-pointer",
    isSelected
      ? "bg-accent-bg [box-shadow:inset_2px_0_0_theme(colors.accent)]"
      : variant === "stop"
        ? "bg-[rgba(199,98,92,0.05)] hover:bg-[rgba(199,98,92,0.08)]"
        : variant === "warning"
          ? "bg-[rgba(212,168,88,0.04)] hover:bg-bg-2"
          : "hover:bg-bg-2",
  );

  return (
    <tr
      style={style}
      data-variant={variant}
      data-selected={isSelected || undefined}
      className={rowCls}
      onClick={onClick}
      aria-selected={isSelected || undefined}
    >
      {row.getVisibleCells().map((cell) => {
        const meta = (cell.column.columnDef.meta ?? {}) as ColumnMeta;
        return (
          <td
            key={cell.id}
            style={{ height: rowHeight }}
            className={cn(
              "px-3.5 text-[13px] text-bg-11 align-middle",
              meta.align === "right" && "text-right",
              meta.align === "center" && "text-center",
            )}
          >
            {flexRender(cell.column.columnDef.cell, cell.getContext())}
          </td>
        );
      })}
    </tr>
  );
}

/** Скелетон-строка при загрузке. */
function SkeletonRow({
  colCount,
  rowHeight,
}: {
  colCount: number;
  rowHeight: number;
}) {
  return (
    <tr className="border-b border-bg-3" aria-hidden="true">
      {Array.from({ length: colCount }, (_, i) => (
        <td key={i} style={{ height: rowHeight }} className="px-3.5 align-middle">
          <div
            className="h-3 bg-bg-3 animate-pulse rounded-[1px]"
            style={{ width: i === 0 ? 16 : i === 1 ? "75%" : "55%" }}
          />
        </td>
      ))}
    </tr>
  );
}
