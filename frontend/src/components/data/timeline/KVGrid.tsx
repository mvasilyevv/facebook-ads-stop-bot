/**
 * KVGrid — 2-колоночная сетка "label / value" для drawer/панелей деталей.
 *
 * Спека из макета (ads.html .kv-grid):
 *   - 2 колонки, gap 12px×24px, border-top/bottom bg-3, padding 16px 0.
 *   - .kv__label  — font-display 10px tracking 0.1em uppercase bg-8.
 *   - .kv__value  — font-numeric 18px tabular-nums bg-11; warn → warning, bad → danger.
 *   - .kv__sub    — font-display 10px bg-9 (опциональная sub-линия).
 */

import { type ReactNode } from "react";
import { cn } from "@/lib/utils/cn";

// ─── Типы ─────────────────────────────────────────────────────────────────────

/** Состояние значения: влияет на цвет. */
export type KVValueState = "default" | "warn" | "bad";

export interface KVItem {
  /** Лейбл (короткий, uppercase). */
  label: string;
  /** Отформатированное значение. */
  value: ReactNode;
  /** Состояние визуального акцента. */
  state?: KVValueState;
  /** Опциональная sub-строка (threshold / delta). */
  sub?: ReactNode;
  /** Tooltip на ячейке. */
  title?: string;
}

// ─── Стили значения по state ──────────────────────────────────────────────────

const VALUE_STATE_CLASSES: Record<KVValueState, string> = {
  default: "text-bg-11",
  warn: "text-warning",
  bad: "text-danger",
};

// ─── Одна KV-ячейка ───────────────────────────────────────────────────────────

interface KVCellProps {
  item: KVItem;
}

function KVCell({ item }: KVCellProps) {
  const { label, value, state = "default", sub, title } = item;

  return (
    <div className="flex flex-col gap-1" title={title}>
      {/* Лейбл: font-display uppercase tracking 0.1em text-bg-8 */}
      <span className="font-display text-[10px] tracking-[0.1em] uppercase text-bg-8">
        {label}
      </span>

      {/* Значение: font-numeric 18px tabular-nums */}
      <span
        className={cn(
          "font-display text-[18px] font-medium tabular-nums leading-tight",
          VALUE_STATE_CLASSES[state],
        )}
        style={{ fontVariantNumeric: "tabular-nums" }}
      >
        {value}
      </span>

      {/* Sub-строка (необязательно) */}
      {sub ? (
        <span className="font-display text-[10px] text-bg-9 leading-tight">{sub}</span>
      ) : null}
    </div>
  );
}

// ─── KVGrid ───────────────────────────────────────────────────────────────────

interface KVGridProps {
  items: KVItem[];
  /** Дополнительные CSS-классы на контейнер. */
  className?: string;
}

/**
 * KVGrid — 2-колоночная label/value сетка.
 *
 * Принимает массив KVItem. Нечётное число элементов — последняя ячейка занимает полную ширину.
 *
 * @example
 * <KVGrid items={[
 *   { label: "Spend",     value: "$891.23", state: "bad",  sub: "+ $42 last hour" },
 *   { label: "CPL",       value: "$42.10",  state: "bad",  sub: "threshold $20 — over" },
 *   { label: "Leads",     value: "21",      state: "default", sub: "12 today" },
 *   { label: "Frequency", value: "4.8",     state: "warn", sub: "over threshold 4.0" },
 * ]} />
 */
export function KVGrid({ items, className }: KVGridProps) {
  return (
    <div
      className={cn(
        "grid grid-cols-2 gap-y-3 gap-x-6",
        "py-4 border-t border-bg-3 border-b",
        className,
      )}
    >
      {items.map((item, idx) => (
        <KVCell key={idx} item={item} />
      ))}
    </div>
  );
}
