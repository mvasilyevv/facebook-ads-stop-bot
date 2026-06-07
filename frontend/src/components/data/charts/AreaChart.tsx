/**
 * AreaChart — area-линейный Recharts с editorial-монохромным стилем.
 *
 * Решение Recharts (не SVG вручную):
 *   - ResponsiveContainer хорошо работает в обёртке ChartWrapper (div с фиксированной px-высотой).
 *   - Recharts AreaChart даёт полный контроль: defs/linearGradient, CartesianGrid, оси, Tooltip,
 *     кастомный CustomDot для peak-аннотации.
 *   - Единственный кастомный SVG-элемент — peak-аннотация (dashed line + label) через
 *     ReferenceLine + кастомный label, что recharts поддерживает напрямую.
 *   - Чистый SVG вместо Recharts не оправдан: теряем responsive, анимации (isAnimationActive),
 *     tooltip, accessibility (aria), cursor. Recharts + кастомизация через props даёт 95% макета.
 *
 * Макет-эталон: docs/frontend_mockups/dashboard.html (svg#area gradient + grid pattern + annotation).
 */

import { useMemo, useId } from "react";
import {
  AreaChart as RechartsAreaChart,
  Area,
  CartesianGrid,
  ResponsiveContainer,
  Tooltip,
  XAxis,
  YAxis,
  ReferenceLine,
} from "recharts";
import { ChartWrapper } from "./ChartWrapper";
import { formatSpend, formatInt } from "@fb/shared";

// ─── Цвета (берём из ChartWrapper для единообразия) ──────────────────────────
const COLORS = {
  primary: "#F5F1E8",    // accent — warm off-white
  grid: "#1C1C21",       // bg-3
  axisText: "#5C5C66",   // bg-8
  axisLine: "#2C2C33",   // bg-5
  cursor: "#4A4A52",     // bg-7
} as const;

// ─── Типы ─────────────────────────────────────────────────────────────────────

export interface AreaDataPoint {
  /** ISO-метка (ключ оси X — показывается как label). */
  ts: string;
  /** Форматированный лейбл для оси X (например "14:00" или "05-21"). */
  label: string;
  /** Основная метрика — spend ($). */
  spend: number;
  /** Вторичная метрика — количество лидов. */
  leads?: number;
}

interface AreaChartProps {
  data: AreaDataPoint[];
  /** Высота в пикселях. Default 280. */
  height?: number;
  /** Форматтер тика Y-оси. По умолчанию "$N". */
  yTickFormatter?: (v: number) => string;
  /** Показывать аннотацию пикового значения. Default true. */
  showPeak?: boolean;
}

// ─── Кастомный tooltip ────────────────────────────────────────────────────────

interface AreaTooltipProps {
  active?: boolean;
  payload?: Array<{ dataKey?: string | number; value?: number | string }>;
  label?: string;
}

function AreaTooltip({ active, payload, label }: AreaTooltipProps) {
  if (!active || !payload?.length) return null;
  const spend = payload.find((p) => p.dataKey === "spend");
  const leads = payload.find((p) => p.dataKey === "leads");

  return (
    <div className="bg-bg-3 border border-bg-6 px-3 py-2 text-[12px] min-w-[120px]">
      {label ? (
        <div className="font-display text-bg-9 mb-1.5 text-[10px] uppercase tracking-wider">
          {label}
        </div>
      ) : null}
      {spend ? (
        <div className="flex items-center justify-between gap-4 text-bg-11 font-display">
          <span className="text-bg-9">Траты:</span>
          <span className="font-medium tabular-nums">
            {formatSpend(spend.value as number)}
          </span>
        </div>
      ) : null}
      {leads && (leads.value as number) > 0 ? (
        <div className="flex items-center justify-between gap-4 text-bg-11 font-display mt-0.5">
          <span className="text-bg-9">Лиды:</span>
          <span className="font-medium tabular-nums">
            {formatInt(leads.value as number)}
          </span>
        </div>
      ) : null}
    </div>
  );
}

// ─── Аннотация пика ────────────────────────────────────────────────────────────

interface PeakLabelProps {
  viewBox?: { x?: number; y?: number; width?: number; height?: number };
  value: number;
}

/** SVG-лейбл для ReferenceLine пика: dashed line + "PEAK $N" текст. */
function PeakLabel({ viewBox, value }: PeakLabelProps) {
  const x = viewBox?.x ?? 0;
  const y = viewBox?.y ?? 0;
  return (
    <g>
      {/* Пунктирная вертикаль */}
      <line
        x1={x}
        y1={y}
        x2={x}
        y2={y - 20}
        stroke={COLORS.cursor}
        strokeWidth={1}
        strokeDasharray="2 2"
      />
      {/* Лейбл */}
      <text
        x={x}
        y={y - 24}
        textAnchor="middle"
        fontFamily="JetBrains Mono, monospace"
        fontSize={9}
        fill="#A8A8B0"
        letterSpacing="0.05em"
      >
        {`PEAK ${formatSpend(value)}`}
      </text>
    </g>
  );
}

// ─── Основной компонент ───────────────────────────────────────────────────────

export function AreaChart({
  data,
  height = 280,
  yTickFormatter,
  showPeak = true,
}: AreaChartProps) {
  // Уникальный id для gradient def — безопасен при нескольких графиках на странице
  const gradientId = useId().replace(/:/g, "");
  const safeGradId = `spendArea_${gradientId}`;

  // Точка пика для аннотации
  const peakPoint = useMemo<AreaDataPoint | null>(() => {
    if (!showPeak || data.length === 0) return null;
    // data[0] проверен выше через length === 0, но TS нужна явная проверка
    const first = data[0];
    if (!first) return null;
    return data.reduce<AreaDataPoint>(
      (max, p) => (p.spend > max.spend ? p : max),
      first,
    );
  }, [data, showPeak]);

  const defaultYFormatter = (v: number) => `$${formatInt(Math.round(v))}`;
  const yFmt = yTickFormatter ?? defaultYFormatter;

  return (
    <ChartWrapper height={height}>
      <ResponsiveContainer width="100%" height="100%">
        <RechartsAreaChart data={data} margin={{ top: 28, right: 8, bottom: 0, left: 0 }}>
          <defs>
            {/* Акцентный градиент: opacity 0.18 → 0 (точно по mockup) */}
            <linearGradient id={safeGradId} x1="0" y1="0" x2="0" y2="1">
              <stop offset="0%" stopColor={COLORS.primary} stopOpacity={0.18} />
              <stop offset="100%" stopColor={COLORS.primary} stopOpacity={0} />
            </linearGradient>
          </defs>

          {/* Сетка только горизонтальные линии (bg-3 — темно-серая) */}
          <CartesianGrid
            stroke={COLORS.grid}
            vertical={false}
          />

          {/* X-ось: tabular-nums mono, 9px, без tick-линий */}
          <XAxis
            dataKey="label"
            tick={{
              fill: COLORS.axisText,
              fontSize: 9,
              fontFamily: "JetBrains Mono, monospace",
              letterSpacing: "0.04em",
            }}
            axisLine={{ stroke: COLORS.axisLine }}
            tickLine={false}
            minTickGap={40}
            interval="preserveStartEnd"
          />

          {/* Y-ось: tabular-nums mono, 10px, без axisLine */}
          <YAxis
            tick={{
              fill: COLORS.axisText,
              fontSize: 10,
              fontFamily: "JetBrains Mono, monospace",
            }}
            axisLine={false}
            tickLine={false}
            width={52}
            tickFormatter={yFmt}
          />

          {/* Кастомный tooltip */}
          <Tooltip
            content={<AreaTooltip />}
            cursor={{ stroke: COLORS.cursor, strokeDasharray: "2 2" }}
          />

          {/* Area серия */}
          <Area
            type="monotone"
            dataKey="spend"
            name="spend"
            stroke={COLORS.primary}
            strokeWidth={1.5}
            fill={`url(#${safeGradId})`}
            dot={false}
            activeDot={{ r: 3, fill: COLORS.primary, strokeWidth: 0 }}
            isAnimationActive={false}
          />

          {/* Вторичная серия (leads) — скрыта визуально, нужна для tooltip */}
          <Area
            type="monotone"
            dataKey="leads"
            name="leads"
            stroke="transparent"
            fill="transparent"
            dot={false}
            activeDot={false}
            isAnimationActive={false}
          />

          {/* Аннотация пика */}
          {peakPoint ? (
            <ReferenceLine
              x={peakPoint.label}
              stroke="transparent"
              label={
                <PeakLabel value={peakPoint.spend} />
              }
            />
          ) : null}
        </RechartsAreaChart>
      </ResponsiveContainer>
    </ChartWrapper>
  );
}
