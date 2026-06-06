/**
 * DiffTable — 3-колоночная таблица сравнения «было → станет» для подтверждения черновика.
 * Принимает DiffRow[] из buildDraftDiff(@fb/shared).
 *
 * Колонки: Field (1.2fr) / Current (1fr) / Target (1fr).
 * Изменённая строка (changed=true): bg-bg-2 + inset accent-бордер слева 3px.
 * Неизменённые поля: плейсхолдер "— same —" серым цветом.
 */

import type { DiffRow } from "@fb/shared";
import { cn } from "@/lib/utils/cn";

interface DiffTableProps {
  rows: DiffRow[];
  className?: string;
}

export function DiffTable({ rows, className }: DiffTableProps) {
  return (
    <div
      className={cn(
        "border border-bg-5 overflow-hidden font-display text-[12.5px]",
        className,
      )}
    >
      {/* Заголовок таблицы */}
      <div
        className={cn(
          "grid gap-0 bg-bg-2 border-b border-bg-5",
          "px-3 py-2",
          "text-[10px] tracking-[0.12em] uppercase text-bg-8",
        )}
        style={{ gridTemplateColumns: "1.2fr 1fr 1fr" }}
      >
        <span>Field</span>
        <span>Current</span>
        <span>Target</span>
      </div>

      {/* Строки diff */}
      {rows.map((row, idx) => (
        <div
          key={`${row.field}-${idx}`}
          className={cn(
            "grid items-center px-3 py-[10px]",
            "border-b border-bg-3 last:border-b-0",
            // Изменённая строка: тёмный фон + акцентная инсет-полоса
            row.changed
              ? "bg-bg-2 shadow-[inset_3px_0_0_var(--color-accent)]"
              : "",
          )}
          style={{ gridTemplateColumns: "1.2fr 1fr 1fr" }}
          data-changed={row.changed}
        >
          {/* Field */}
          <span className="text-[11px] tracking-[0.04em] text-bg-9 font-display">
            {row.field}
          </span>

          {/* Current */}
          <span className="text-bg-10 tabular-nums">
            {row.changed && row.current === row.target ? (
              <span className="text-bg-7">— same —</span>
            ) : (
              row.current || <span className="text-bg-7">—</span>
            )}
          </span>

          {/* Target */}
          <span
            className={cn(
              "font-medium tabular-nums",
              row.changed ? "text-bg-11" : "text-bg-10",
            )}
          >
            {/* Неизменённые строки: плейсхолдер зависит от семантики */}
            {!row.changed ? (
              <span className="text-bg-7">— same —</span>
            ) : (
              row.target || <span className="text-bg-7">—</span>
            )}
          </span>
        </div>
      ))}

      {/* Пустое состояние */}
      {rows.length === 0 && (
        <div className="px-3 py-4 text-[12px] text-bg-8 font-display tracking-wider">
          Нет данных для сравнения
        </div>
      )}
    </div>
  );
}
