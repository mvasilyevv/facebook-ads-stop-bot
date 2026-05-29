/**
 * ChartWrapper — Recharts с предустановленными tokens.
 * Series colors: 1 → accent, 2 → accent+accent-muted, 3+ → accent + semantic.
 * Tooltips — кастомные.
 */

import { type ReactNode } from "react";

export const CHART_COLORS = {
  primary: "#F5F1E8",
  secondary: "#BDB8AB",
  info: "#7AA0B4",
  success: "#7EB47A",
  warning: "#D4A858",
  danger: "#C7625C",
  grid: "#1C1C21",
  axis: "#5C5C66",
  axisText: "#7C7C86",
} as const;

interface ChartWrapperProps {
  /** Высота в пикселях. */
  height?: number;
  children: ReactNode;
}

export function ChartWrapper({ height = 280, children }: ChartWrapperProps) {
  return <div style={{ height, width: "100%" }}>{children}</div>;
}

/** Custom tooltip для Recharts — bg-3 + tabular-nums. */
export function CustomTooltipContent({
  active,
  payload,
  label,
}: {
  active?: boolean;
  payload?: Array<{ name?: string; value?: number | string; color?: string }>;
  label?: string;
}) {
  if (!active || !payload?.length) return null;
  return (
    <div className="bg-bg-3 border border-bg-6 px-3 py-2 text-[12px]">
      {label ? (
        <div className="font-display text-bg-9 mb-1 text-[10px] uppercase tracking-wider">
          {label}
        </div>
      ) : null}
      {payload.map((p, i) => (
        <div key={i} className="flex items-center gap-2 text-bg-11 font-numeric">
          <span
            aria-hidden="true"
            className="size-2 rounded-full inline-block"
            style={{ background: p.color }}
          />
          <span className="text-bg-9 mr-2">{p.name}:</span>
          <span className="font-medium">{p.value}</span>
        </div>
      ))}
    </div>
  );
}
