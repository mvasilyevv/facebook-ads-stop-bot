/**
 * KpiPlate — большая числовая плитка для KPI-сетки.
 * Число: JetBrains Mono, крупный размер. Label: Inter Tight, caption.
 * Вариант: default | ok | warn | stop | info.
 */
import { cn } from "@/lib/cn";

export type KpiVariant = "default" | "ok" | "warn" | "stop" | "info";

const VARIANT_NUM_COLOR: Record<KpiVariant, string> = {
  default: "text-[var(--color-bg-11)]",
  ok:      "text-[var(--color-success)]",
  warn:    "text-[var(--color-warning)]",
  stop:    "text-[var(--color-danger)]",
  info:    "text-[var(--color-info)]",
};

interface KpiPlateProps {
  /** Маленький eyebrow над числом. */
  eyebrow?: string;
  /** Основная метка под числом. */
  label: string;
  /** Числовое значение. null/undefined → "—". */
  value: number | string | null | undefined;
  variant?: KpiVariant;
  className?: string;
}

export function KpiPlate({ eyebrow, label, value, variant = "default", className }: KpiPlateProps) {
  const displayValue = value == null ? "—" : String(value);

  return (
    <div
      className={cn(
        "bg-[var(--color-bg-1)] border border-[var(--color-bg-5)] p-3",
        "flex flex-col gap-1",
        className,
      )}
    >
      {eyebrow && (
        <p className="text-[10px] uppercase tracking-[0.08em] text-[var(--color-bg-9)] font-mono leading-none">
          {eyebrow}
        </p>
      )}
      <p
        className={cn(
          "text-[28px] font-display font-semibold leading-none tabular-nums",
          VARIANT_NUM_COLOR[variant],
        )}
      >
        {displayValue}
      </p>
      <p className="text-[12px] text-[var(--color-bg-9)] font-body leading-none">{label}</p>
    </div>
  );
}
