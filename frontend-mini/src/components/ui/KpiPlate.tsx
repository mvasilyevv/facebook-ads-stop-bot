/**
 * KpiPlate — числовая плитка KPI.
 * Число: JetBrains Mono 28px weight 500 tabular-nums (+ count-up для чисел).
 * Eyebrow 10px tracking 0.12em. Вариант красит число по семантике.
 */
import { cn } from "@/lib/cn";
import { useCountUp } from "@/lib/hooks/useCountUp";

export type KpiVariant = "default" | "ok" | "warn" | "stop" | "info";

const VARIANT_NUM_COLOR: Record<KpiVariant, string> = {
  default: "text-bg-11",
  ok:      "text-success",
  warn:    "text-warning",
  stop:    "text-danger",
  info:    "text-info",
};

/** Внутренний счётчик с count-up для числовых значений. */
function KpiNumber({ value }: { value: number }) {
  const animated = useCountUp(value);
  return <>{animated.toLocaleString("en-US")}</>;
}

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
  const isNum = typeof value === "number" && Number.isFinite(value);

  return (
    <div
      className={cn(
        "bg-bg-1 border border-bg-5 p-3",
        "flex flex-col gap-1",
        className,
      )}
    >
      {eyebrow && (
        <p className="font-display text-[10px] uppercase tracking-[0.12em] text-bg-9 leading-none">
          {eyebrow}
        </p>
      )}
      <p
        className={cn(
          "text-[28px] font-display font-medium leading-none tabular-nums",
          VARIANT_NUM_COLOR[variant],
        )}
      >
        {isNum ? <KpiNumber value={value} /> : value == null ? "—" : String(value)}
      </p>
      <p className="text-[12px] text-bg-9 leading-none">{label}</p>
    </div>
  );
}
