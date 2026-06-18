/**
 * ChartWrapper — div-обёртка с фиксированной px-высотой.
 *
 * Решает проблему Recharts в jsdom и при первом рендере:
 * ResponsiveContainer получает width/height из родительского div,
 * а не из window — что даёт 0×0 в тестах без этой обёртки.
 *
 * Экспортирует также CHART_COLORS и CustomTooltipContent для переиспользования
 * в других chart-компонентах.
 */

import { type ReactNode } from "react";

// ─── Цвета графиков ───────────────────────────────────────────────────────────

export const CHART_COLORS = {
  /** Accent — warm off-white (#F5F1E8) */
  primary: "#F5F1E8",
  secondary: "#BDB8AB",
  info: "#7AA0B4",
  success: "#7EB47A",
  warning: "#D4A858",
  danger: "#C7625C",
  /** bg-3 — фоновая сетка */
  grid: "#1C1C21",
  /** bg-7 — ось и cursor */
  axis: "#4A4A52",
  /** bg-8 — текст осей */
  axisText: "#5C5C66",
} as const;

// ─── Обёртка ─────────────────────────────────────────────────────────────────

interface ChartWrapperProps {
  /** Высота в пикселях. Default 280. */
  height?: number;
  children: ReactNode;
}

export function ChartWrapper({ height = 280, children }: ChartWrapperProps) {
  return (
    <div
      style={{ height, width: "100%" }}
      aria-hidden="false"
      data-testid="chart-wrapper"
    >
      {children}
    </div>
  );
}

// ─── Кастомный tooltip (shared) ───────────────────────────────────────────────

interface TooltipPayloadItem {
  name?: string;
  value?: number | string;
  color?: string;
}

interface CustomTooltipContentProps {
  active?: boolean;
  payload?: TooltipPayloadItem[];
  label?: string;
  /** Маппинг имени серии на человекочитаемый лейбл. */
  nameMap?: Record<string, string>;
  /** Форматтер значения. */
  valueFormatter?: (value: number | string | undefined, name?: string) => string;
}

/** Tooltip в стиле editorial — bg-3, mono, tabular-nums. */
export function CustomTooltipContent({
  active,
  payload,
  label,
  nameMap,
  valueFormatter,
}: CustomTooltipContentProps) {
  if (!active || !payload?.length) return null;
  return (
    <div className="bg-bg-3 border border-[var(--hairline)] rounded-[var(--radius-2)] px-3 py-2 text-[12px]">
      {label ? (
        <div className="font-display text-bg-9 mb-1 text-[10px] uppercase tracking-wider">
          {label}
        </div>
      ) : null}
      {payload.map((p, i) => (
        <div key={i} className="flex items-center gap-2 text-bg-11 font-display">
          <span
            aria-hidden="true"
            className="size-2 rounded-full inline-block"
            style={{ background: p.color }}
          />
          <span className="text-bg-9 mr-2">
            {(p.name && nameMap?.[p.name]) ?? p.name}:
          </span>
          <span className="font-medium tabular-nums">
            {valueFormatter ? valueFormatter(p.value, p.name) : p.value}
          </span>
        </div>
      ))}
    </div>
  );
}
