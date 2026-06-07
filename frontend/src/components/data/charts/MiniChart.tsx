/**
 * MiniChart — компактный area-chart без осей и tooltip.
 * Используется в drawer для отображения spend-динамики (6h окно).
 *
 * Danger-tinted вариант: #C7625C (danger) градиент + линия.
 * Размер: высота 120px по умолчанию (как в mockup ads.html .mini-chart).
 * Без осей, без сетки, без tooltip — только силуэт тренда.
 *
 * Makет-эталон: docs/frontend_mockups/ads.html (#02 Spend rate · 6h window).
 */

import { useId } from "react";
import { AreaChart, Area, ResponsiveContainer } from "recharts";
import { ChartWrapper } from "./ChartWrapper";

// ─── Типы ─────────────────────────────────────────────────────────────────────

export interface MiniChartPoint {
  /** Метка (не показывается — только для ключа). */
  label: string;
  /** Значение spend. */
  spend: number;
}

// ─── Цвета вариантов ──────────────────────────────────────────────────────────

const TINT = {
  danger: {
    line: "#C7625C",
    gradOpacity: 0.25,
  },
  accent: {
    line: "#F5F1E8",
    gradOpacity: 0.18,
  },
} as const;

type MiniChartTint = keyof typeof TINT;

interface MiniChartProps {
  data: MiniChartPoint[];
  /** Цветовой акцент: "danger" (по умолчанию для drawer) | "accent". */
  tint?: MiniChartTint;
  /** Высота в пикселях. Default 120. */
  height?: number;
  /** aria-label для контейнера (accessibility). */
  "aria-label"?: string;
}

// ─── Компонент ────────────────────────────────────────────────────────────────

export function MiniChart({
  data,
  tint = "danger",
  height = 120,
  "aria-label": ariaLabel = "Мини-график spend",
}: MiniChartProps) {
  // Уникальный gradient id — безопасен при нескольких MiniChart на странице
  const uid = useId().replace(/:/g, "");
  const gradId = `miniChartGrad_${uid}`;

  const { line, gradOpacity } = TINT[tint];

  return (
    <ChartWrapper height={height}>
      <div
        role="img"
        aria-label={ariaLabel}
        style={{ height: "100%", width: "100%" }}
      >
        <ResponsiveContainer width="100%" height="100%">
          <AreaChart data={data} margin={{ top: 4, right: 0, bottom: 0, left: 0 }}>
            <defs>
              {/* Danger-tinted градиент: 0.25 → 0 (как в mockup) */}
              <linearGradient id={gradId} x1="0" y1="0" x2="0" y2="1">
                <stop offset="0%" stopColor={line} stopOpacity={gradOpacity} />
                <stop offset="100%" stopColor={line} stopOpacity={0} />
              </linearGradient>
            </defs>

            {/* Area без осей, без сетки, без tooltip */}
            <Area
              type="monotone"
              dataKey="spend"
              stroke={line}
              strokeWidth={1.5}
              fill={`url(#${gradId})`}
              dot={false}
              activeDot={false}
              isAnimationActive={false}
            />
          </AreaChart>
        </ResponsiveContainer>
      </div>
    </ChartWrapper>
  );
}
