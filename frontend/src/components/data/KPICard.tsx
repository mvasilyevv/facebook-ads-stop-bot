/**
 * KPICard — большое число + лейбл + опциональный trend delta.
 * Композит для KPI-strip на DashboardPage.
 */

import { type ReactNode } from "react";
import { cn } from "@/lib/utils/cn";

type Variant = "muted" | "warning" | "danger" | "success" | "info";

interface KPICardProps {
  label: string;
  value: ReactNode;
  /** "today scan" / "now" — мелким шрифтом под значением. */
  hint?: ReactNode;
  /** Delta: "+3" / "-1". Цвет зависит от direction. */
  trend?: { value: string; direction: "up" | "down" };
  variant?: Variant;
}

const DOT_CLASS: Record<Variant, string> = {
  muted: "bg-bg-8",
  warning: "bg-warning",
  danger: "bg-danger",
  success: "bg-success",
  info: "bg-info",
};

export function KPICard({ label, value, hint, trend, variant = "muted" }: KPICardProps) {
  return (
    <div className="p-6 pb-7 relative">
      <div className="flex items-center gap-2 mb-4">
        <span aria-hidden="true" className={cn("size-1.5 rounded-full", DOT_CLASS[variant])} />
        <span className="font-display text-[10px] uppercase tracking-[0.14em] text-bg-8">
          {label}
        </span>
      </div>
      <div className="font-display text-[48px] font-medium leading-none tracking-tight text-bg-11 mb-3 tabular-nums">
        {value}
      </div>
      <div className="flex items-baseline justify-between text-[11px] font-display tracking-wider text-bg-9">
        <span>{hint}</span>
        {trend ? (
          <span
            className={cn(
              "font-medium",
              trend.direction === "up" ? "text-warning" : "text-success",
            )}
          >
            {trend.value}
          </span>
        ) : null}
      </div>
    </div>
  );
}

/** Strip — горизонтальный grid из 4 KPI с разделителями. */
export function KPIStrip({ children }: { children: ReactNode }) {
  return (
    <div className="grid grid-cols-4 border border-bg-5 bg-bg-1 divide-x divide-bg-5">
      {children}
    </div>
  );
}
