/**
 * ChartCard — обёртка для графика.
 * Структура: Card > header (eyebrow + title + диапазон-табы) > контент > meta-footer.
 *
 * Макет-эталон: docs/frontend_mockups/dashboard.html (.card > .chart-tabs + .chart-meta).
 * Используется в SpendChartCard и других chart-компонентах дашборда.
 */

import { type ReactNode } from "react";
import { Card } from "@/components/ui/Card";
import { cn } from "@/lib/utils/cn";

// ─── RangeTabs ────────────────────────────────────────────────────────────────

export interface RangeTabItem {
  value: string;
  label: string;
}

interface RangeTabsProps {
  items: RangeTabItem[];
  value: string;
  onChange: (value: string) => void;
  "aria-label"?: string;
}

/**
 * Compact chart-tabs — border box без gap (как в mockup).
 * Active: bg-4 + text-accent, inactive: text-bg-9 hover:text-bg-11.
 */
export function RangeTabs({
  items,
  value,
  onChange,
  "aria-label": ariaLabel = "Диапазон графика",
}: RangeTabsProps) {
  return (
    <div
      role="tablist"
      aria-label={ariaLabel}
      className="flex border border-[var(--hairline)] rounded-[var(--radius-2)] overflow-hidden bg-bg-2"
    >
      {items.map((item, idx) => (
        <button
          key={item.value}
          type="button"
          role="tab"
          aria-selected={item.value === value}
          onClick={() => onChange(item.value)}
          className={cn(
            "px-2.5 py-1 font-display text-[11px] tracking-wider transition-colors",
            "focus-visible:outline-2 focus-visible:outline-offset-[-2px] focus-visible:outline-accent",
            // Разделитель между табами
            idx < items.length - 1 ? "border-r border-[var(--hairline)]" : "",
            item.value === value
              ? "bg-bg-4 text-accent"
              : "text-bg-9 hover:text-bg-11",
          )}
        >
          {item.label}
        </button>
      ))}
    </div>
  );
}

// ─── ChartMetaItem ────────────────────────────────────────────────────────────

interface ChartMetaItemProps {
  label: string;
  value: string;
  title?: string;
}

/** Одна пара label/value в meta-footer. */
export function ChartMetaItem({ label, value, title }: ChartMetaItemProps) {
  return (
    <span title={title}>
      <span className="text-bg-8 mr-1.5">{label}</span>
      <span className="text-bg-11 font-medium tabular-nums font-display">{value}</span>
    </span>
  );
}

// ─── ChartCard ────────────────────────────────────────────────────────────────

interface ChartCardProps {
  /** Eyebrow-лейбл: "02 Spend × Hour" */
  eyebrow?: ReactNode;
  /** Заголовок карточки: "Spend rate · last 24h" */
  title?: ReactNode;
  /** Слот для range-табов (RangeTabs или другой контрол). */
  rangeControl?: ReactNode;
  /** График — область с контентом высотой chartHeight. */
  children: ReactNode;
  /** Массив мета-элементов в footer. Если пустой — footer скрыт. */
  metaItems?: ChartMetaItemProps[];
  /** Дополнительные классы на Card. */
  className?: string;
}

/**
 * ChartCard — универсальная обёртка графика.
 *
 * Слот rangeControl рендерится справа в header (как chart-tabs в mockup).
 * Слот children — тело карточки (ChartWrapper + Recharts или EmptyState/Skeleton).
 * metaItems — footer-строка с итоговыми метриками.
 */
export function ChartCard({
  eyebrow,
  title,
  rangeControl,
  children,
  metaItems,
  className,
}: ChartCardProps) {
  const hasFooter = metaItems && metaItems.length > 0;

  return (
    <Card className={className}>
      {/* Header: eyebrow + title слева, range-control справа */}
      {(eyebrow || title || rangeControl) && (
        <div className="flex items-start justify-between mb-5">
          <div>
            {eyebrow ? (
              <div className="font-display text-[10px] tracking-[.14em] uppercase text-bg-8 mb-1.5">
                {eyebrow}
              </div>
            ) : null}
            {title ? (
              <h3 className="font-display text-[13px] font-medium tracking-wider text-bg-11 m-0">
                {title}
              </h3>
            ) : null}
          </div>
          {rangeControl ? <div>{rangeControl}</div> : null}
        </div>
      )}

      {/* Тело — chart + state-компоненты */}
      {children}

      {/* Meta-footer: total / avg / peak / leads / cpl */}
      {hasFooter ? (
        <div className="flex gap-6 pt-3 mt-3 border-t border-[var(--hairline)] font-display text-[11px] tracking-wider text-bg-10 flex-wrap">
          {metaItems.map((item) => (
            <ChartMetaItem
              key={item.label}
              label={item.label}
              value={item.value}
              title={item.title}
            />
          ))}
        </div>
      ) : null}
    </Card>
  );
}
