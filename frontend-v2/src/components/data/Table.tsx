/**
 * Table — TanStack Table + virtualization.
 * Features: sortable headers, sticky header, density toggle.
 *
 * Минимальное API: передаём columns + data, остальное опционально.
 * Виртуализация — через @tanstack/react-virtual.
 */

import { useRef, useState, type ReactNode } from "react";
import {
  flexRender,
  getCoreRowModel,
  getSortedRowModel,
  useReactTable,
  type ColumnDef,
  type SortingState,
  type Row,
} from "@tanstack/react-table";
import { useVirtualizer } from "@tanstack/react-virtual";
import { ArrowDown, ArrowUp, ArrowUpDown } from "lucide-react";
import { useUiStore, DENSITY_ROW_HEIGHT } from "@/stores/ui";
import { cn } from "@/lib/utils/cn";

interface TableProps<T> {
  data: T[];
  columns: ColumnDef<T, unknown>[];
  /** Виртуализация (default true). Отключи для коротких списков (<50 rows). */
  virtualized?: boolean;
  /** Высота контейнера для виртуализации в px. */
  height?: number;
  /** Empty state, если data пустая. */
  emptyState?: ReactNode;
  /** Skeleton, если loading. */
  loading?: boolean;
  /** Клик по строке (для drawer-open). */
  onRowClick?: (row: T) => void;
  /** Выделенная строка (визуально). */
  selectedRowKey?: string;
  getRowKey?: (row: T) => string;
}

export function Table<T>({
  data,
  columns,
  virtualized = true,
  height = 600,
  emptyState,
  loading,
  onRowClick,
  selectedRowKey,
  getRowKey,
}: TableProps<T>) {
  const [sorting, setSorting] = useState<SortingState>([]);
  const density = useUiStore((s) => s.density);
  const rowHeight = DENSITY_ROW_HEIGHT[density];

  const table = useReactTable({
    data,
    columns,
    state: { sorting },
    onSortingChange: setSorting,
    getCoreRowModel: getCoreRowModel(),
    getSortedRowModel: getSortedRowModel(),
  });

  const containerRef = useRef<HTMLDivElement | null>(null);
  const rows = table.getRowModel().rows;

  const virtualizer = useVirtualizer({
    count: rows.length,
    getScrollElement: () => containerRef.current,
    estimateSize: () => rowHeight,
    overscan: 10,
  });

  const visibleRows = virtualized ? virtualizer.getVirtualItems() : null;

  const showEmpty = !loading && rows.length === 0;

  return (
    <div
      ref={containerRef}
      className="border border-bg-5 bg-bg-1 overflow-auto"
      style={virtualized ? { height } : undefined}
      role="region"
      aria-label="Таблица данных"
    >
      <table className="w-full border-collapse">
        <thead className="sticky top-0 bg-bg-1 z-[1] border-b border-bg-5">
          {table.getHeaderGroups().map((hg) => (
            <tr key={hg.id}>
              {hg.headers.map((h) => {
                const canSort = h.column.getCanSort();
                const sortDir = h.column.getIsSorted();
                return (
                  <th
                    key={h.id}
                    scope="col"
                    style={{ width: h.getSize() !== 150 ? h.getSize() : undefined }}
                    className={cn(
                      "text-left px-3 py-2 border-b border-bg-5",
                      "font-display text-[10px] uppercase tracking-wider text-bg-9",
                    )}
                  >
                    {h.isPlaceholder ? null : canSort ? (
                      <button
                        type="button"
                        onClick={h.column.getToggleSortingHandler()}
                        className="inline-flex items-center gap-1.5 hover:text-bg-11 transition-colors"
                      >
                        {flexRender(h.column.columnDef.header, h.getContext())}
                        {sortDir === "asc" ? (
                          <ArrowUp size={11} aria-hidden="true" />
                        ) : sortDir === "desc" ? (
                          <ArrowDown size={11} aria-hidden="true" />
                        ) : (
                          <ArrowUpDown size={11} aria-hidden="true" className="opacity-50" />
                        )}
                      </button>
                    ) : (
                      flexRender(h.column.columnDef.header, h.getContext())
                    )}
                  </th>
                );
              })}
            </tr>
          ))}
        </thead>
        <tbody>
          {loading ? (
            Array.from({ length: 6 }).map((_, i) => <SkeletonRow key={i} columns={columns.length} />)
          ) : virtualized && visibleRows ? (
            <>
              <tr style={{ height: virtualizer.getTotalSize() }}>
                <td colSpan={columns.length} style={{ padding: 0, border: 0 }} />
              </tr>
              {visibleRows.map((vi) => {
                const row = rows[vi.index];
                if (!row) return null;
                return (
                  <TableRow
                    key={row.id}
                    row={row}
                    style={{
                      transform: `translateY(${vi.start - vi.index * rowHeight}px)`,
                    }}
                    onRowClick={onRowClick}
                    selected={selectedRowKey === (getRowKey ? getRowKey(row.original) : row.id)}
                  />
                );
              })}
            </>
          ) : (
            rows.map((row) => (
              <TableRow
                key={row.id}
                row={row}
                onRowClick={onRowClick}
                selected={selectedRowKey === (getRowKey ? getRowKey(row.original) : row.id)}
              />
            ))
          )}
          {showEmpty ? (
            <tr>
              <td colSpan={columns.length} className="text-center py-12">
                {emptyState ?? (
                  <span className="text-bg-9 text-[13px]">Нет данных для отображения.</span>
                )}
              </td>
            </tr>
          ) : null}
        </tbody>
      </table>
    </div>
  );
}

function TableRow<T>({
  row,
  style,
  onRowClick,
  selected,
}: {
  row: Row<T>;
  style?: React.CSSProperties;
  onRowClick?: (row: T) => void;
  selected?: boolean;
}) {
  return (
    <tr
      style={style}
      onClick={onRowClick ? () => onRowClick(row.original) : undefined}
      data-selected={selected || undefined}
      className={cn(
        "transition-colors border-b border-bg-3 last:border-b-0",
        onRowClick && "cursor-pointer",
        selected ? "bg-accent-bg" : "hover:bg-bg-2",
      )}
    >
      {row.getVisibleCells().map((cell) => (
        <td
          key={cell.id}
          style={{ height: "var(--table-row-height, 32px)" }}
          className="px-3 text-[13px] text-bg-11 align-middle"
        >
          {flexRender(cell.column.columnDef.cell, cell.getContext())}
        </td>
      ))}
    </tr>
  );
}

function SkeletonRow({ columns }: { columns: number }) {
  return (
    <tr style={{ height: "var(--table-row-height, 32px)" }} className="border-b border-bg-3">
      {Array.from({ length: columns }).map((_, i) => (
        <td key={i} className="px-3">
          <div className="h-3 bg-bg-3 animate-pulse rounded-[1px]" style={{ width: "60%" }} />
        </td>
      ))}
    </tr>
  );
}
