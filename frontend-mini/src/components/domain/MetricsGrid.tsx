/**
 * MetricsGrid — компактная 2-колоночная сетка KPI для AdDetail.
 * Локальный компонент (не в ui/), не трогает ui/ kit.
 * Числа: JetBrains Mono (font-mono), tabular.
 */
import { cn } from "@/lib/cn";

export interface MetricCell {
  label: string;
  value: string | number | null | undefined;
  /** Подсветить значение (например, spend=warning). */
  variant?: "default" | "warn" | "stop";
}

const VALUE_COLOR: Record<string, string> = {
  default: "text-[var(--color-bg-11)]",
  warn:    "text-[var(--color-warning)]",
  stop:    "text-[var(--color-danger)]",
};

interface MetricsGridProps {
  cells: MetricCell[];
  className?: string;
}

export function MetricsGrid({ cells, className }: MetricsGridProps) {
  return (
    <div
      className={cn(
        "grid grid-cols-2 gap-px border border-[var(--color-bg-5)] bg-[var(--color-bg-5)]",
        className,
      )}
    >
      {cells.map((cell, i) => (
        <div
          key={i}
          className="bg-[var(--color-bg-1)] p-3 flex flex-col gap-1"
        >
          <p className="text-[10px] uppercase tracking-[0.08em] text-[var(--color-bg-9)] font-mono leading-none">
            {cell.label}
          </p>
          <p
            className={cn(
              "text-[22px] font-mono font-semibold leading-none tabular-nums",
              VALUE_COLOR[cell.variant ?? "default"],
            )}
          >
            {cell.value ?? "—"}
          </p>
        </div>
      ))}
    </div>
  );
}
