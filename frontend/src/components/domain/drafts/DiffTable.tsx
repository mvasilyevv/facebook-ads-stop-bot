/**
 * DiffTable — 2-колоночная таблица diff «ключ → текущее → цель».
 * Эталон: templates.jsx DiffRow — gridTemplateColumns: '160px 1fr'.
 *
 * Изменённая строка (changed=true):
 *   - 2px accent left border + accent-bg заливка
 *   - value ячейка: current (серый) → chevron → target (accent)
 * Неизменённая строка: только key + value (без стрелки).
 */

import type { DiffRow } from "@fb/shared";
import { ChevronRight } from "lucide-react";
import { cn } from "@/lib/utils/cn";

interface DiffTableProps {
  rows: DiffRow[];
  className?: string;
}

export function DiffTable({ rows, className }: DiffTableProps) {
  return (
    <div className={cn("overflow-hidden font-display text-[12px]", className)}>
      {/* Заголовки колонок — только для screen readers и тестов */}
      <div className="sr-only" aria-hidden="false">
        <span>Field</span>
        <span>Current</span>
        <span>Target</span>
      </div>

      {rows.map((row, idx) => (
        <div
          key={`${row.field}-${idx}`}
          style={{
            display: "grid",
            gridTemplateColumns: "160px 1fr",
            gap: 12,
            padding: "7px 12px",
            borderLeft: row.changed
              ? "2px solid var(--accent)"
              : "2px solid transparent",
            background: row.changed ? "var(--accent-bg)" : "transparent",
          }}
          data-changed={row.changed}
        >
          {/* Ключ */}
          <span className="text-bg-9 truncate">{row.field}</span>

          {/* Значение: current → target для изменённых, «— same —» для неизменённых */}
          <span className="flex items-center gap-2 flex-wrap text-bg-11">
            {row.target != null ? (
              row.changed ? (
                <>
                  <span className="text-bg-9">{row.current || <span className="text-bg-7">—</span>}</span>
                  <ChevronRight size={12} className="text-bg-7 shrink-0" aria-hidden="true" />
                  <span style={{ color: "var(--accent)" }}>{row.target}</span>
                </>
              ) : (
                <span className="text-bg-8">— same —</span>
              )
            ) : (
              row.current || <span className="text-bg-7">—</span>
            )}
          </span>
        </div>
      ))}

      {/* Пустое состояние */}
      {rows.length === 0 && (
        <div className="px-3 py-4 text-[12px] text-bg-8 tracking-wider">
          Нет данных для сравнения
        </div>
      )}
    </div>
  );
}
