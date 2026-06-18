/**
 * AreaChart — area-линейный Recharts с editorial-монохромным стилем.
 *
 * Макет-эталон: docs/frontend_mockups/dashboard.html (svg#area gradient + grid pattern + annotation).
 *
 * Ключевые требования из mockup:
 *   - Grid-pattern фон 48×56px, stroke #1C1C21 (bg-3) — обе оси
 *   - XAxis с подписями 00:00/06:00/12:00/18:00/NOW, mono 9px, цвет #5C5C66 (bg-8)
 *   - PEAK-аннотация: dashed вертикаль к точке пика + текст "PEAK $N/h" (mono 10px, bg-10)
 *   - Area gradient: accent #F5F1E8, opacity 0.18→0, line stroke 1.5px
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
  ReferenceDot,
} from "recharts";
import { ChartWrapper } from "./ChartWrapper";
import { formatSpend, formatInt } from "@fb/shared";

// ─── Цвета ────────────────────────────────────────────────────────────────────

const COLORS = {
  primary:  "#F5F1E8",    // accent
  grid:     "#1C1C21",    // bg-3
  axisText: "#5C5C66",    // bg-8
  axisLine: "#2C2C33",    // bg-5
  cursor:   "#4A4A52",    // bg-7
  peak:     "#A8A8B0",    // bg-10 — текст аннотации пика
} as const;

// ─── Типы ─────────────────────────────────────────────────────────────────────

export interface AreaDataPoint {
  /** ISO-метка (ключ оси X). */
  ts: string;
  /** Форматированный лейбл для оси X (например "14:00"). */
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
    <div className="bg-bg-3 border border-[var(--hairline)] rounded-[var(--radius-2)] px-3 py-2 text-[12px] min-w-[120px]">
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

// ─── Кастомный лейбл для аннотации пика ──────────────────────────────────────

interface PeakLabelProps {
  viewBox?: { x?: number; y?: number; width?: number; height?: number };
  value: number;
}

/**
 * SVG-лейбл для ReferenceDot пика:
 * dot + dashed line вверх + "PEAK $N/h" текст над линией.
 */
function PeakLabel({ viewBox, value }: PeakLabelProps) {
  const x = viewBox?.x ?? 0;
  const y = viewBox?.y ?? 0;

  return (
    <g>
      {/* Точка пика */}
      <circle cx={x} cy={y} r={3} fill={COLORS.primary} />
      <circle cx={x} cy={y} r={6} fill="none" stroke={COLORS.primary} strokeOpacity={0.3} />
      {/* Пунктирная вертикаль вверх от точки */}
      <line
        x1={x}
        y1={y - 8}
        x2={x}
        y2={y - 28}
        stroke={COLORS.cursor}
        strokeWidth={1}
        strokeDasharray="2 2"
      />
      {/* Текст аннотации над линией */}
      <text
        x={x}
        y={y - 32}
        textAnchor="middle"
        fontFamily="JetBrains Mono, monospace"
        fontSize={10}
        fill={COLORS.peak}
        letterSpacing="0.05em"
      >
        {`PEAK ${formatSpend(value)}/h`}
      </text>
    </g>
  );
}

// ─── Кастомный X-тик ─────────────────────────────────────────────────────────

interface XTickProps {
  x?: number;
  y?: number;
  payload?: { value?: string };
}

function XTick({ x = 0, y = 0, payload }: XTickProps) {
  const val = payload?.value ?? "";
  if (!val) return null;
  const isNow = val === "NOW";
  return (
    <text
      x={x}
      y={y + 6}
      textAnchor={isNow ? "end" : "middle"}
      fontFamily="JetBrains Mono, monospace"
      fontSize={9}
      fill={COLORS.axisText}
      letterSpacing="0.04em"
    >
      {val}
    </text>
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
    const first = data[0];
    if (!first) return null;
    return data.reduce<AreaDataPoint>(
      (max, p) => (p.spend > max.spend ? p : max),
      first,
    );
  }, [data, showPeak]);

  // Тики оси X: 5 равномерных точек — первая, 25%, 50%, 75%, последняя (→ "NOW")
  const xTicks = useMemo<string[]>(() => {
    if (data.length === 0) return [];
    const n = data.length;
    const indices = [0, Math.floor(n * 0.25), Math.floor(n * 0.5), Math.floor(n * 0.75), n - 1];
    const unique = [...new Set(indices)];
    return unique.map((idx, pos) => {
      const label = data[idx]?.label ?? "";
      // Последнюю точку помечаем "NOW"
      return pos === unique.length - 1 ? "NOW" : label;
    });
  }, [data]);

  // Маппинг label → display (заменяем последний тик на "NOW")
  const lastLabel = data.length > 0 ? (data[data.length - 1]?.label ?? "") : "";
  const tickFormatter = (val: string) => {
    if (val === lastLabel && xTicks.includes("NOW")) return "NOW";
    return val;
  };

  // recharts ticks берём из данных — заменяем последний на lastLabel (recharts по dataKey)
  const axisTicks = useMemo<string[]>(() => {
    if (data.length === 0) return [];
    const n = data.length;
    const indices = [0, Math.floor(n * 0.25), Math.floor(n * 0.5), Math.floor(n * 0.75), n - 1];
    const unique = [...new Set(indices)];
    return unique.map((idx) => data[idx]?.label ?? "");
  }, [data]);

  const defaultYFormatter = (v: number) => `$${formatInt(Math.round(v))}`;
  const yFmt = yTickFormatter ?? defaultYFormatter;

  return (
    <ChartWrapper height={height}>
      <ResponsiveContainer width="100%" height="100%">
        <RechartsAreaChart data={data} margin={{ top: 44, right: 8, bottom: 20, left: 0 }}>
          <defs>
            {/* Акцентный градиент 0.18→0 (точно по mockup) */}
            <linearGradient id={safeGradId} x1="0" y1="0" x2="0" y2="1">
              <stop offset="0%" stopColor={COLORS.primary} stopOpacity={0.18} />
              <stop offset="100%" stopColor={COLORS.primary} stopOpacity={0} />
            </linearGradient>
          </defs>

          {/* Editorial-сетка — обе оси, сплошные линии bg-3 (имитация pattern из mockup) */}
          <CartesianGrid
            stroke={COLORS.grid}
            strokeWidth={1}
            vertical={true}
            horizontal={true}
          />

          {/* X-ось: 5 меток, mono 9px, bg-8 */}
          <XAxis
            dataKey="label"
            tick={(props) => <XTick {...(props as XTickProps)} />}
            axisLine={{ stroke: COLORS.axisLine }}
            tickLine={false}
            ticks={axisTicks}
            tickFormatter={tickFormatter}
            interval={0}
          />

          {/* Y-ось */}
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

          {/* Area серия — accent */}
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

          {/* Leads — скрытая серия для tooltip */}
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

          {/* Аннотация пика: dot + dashed line + label */}
          {peakPoint ? (
            <ReferenceDot
              x={peakPoint.label}
              y={peakPoint.spend}
              r={0}
              fill="transparent"
              stroke="transparent"
              label={<PeakLabel value={peakPoint.spend} />}
            />
          ) : null}
        </RechartsAreaChart>
      </ResponsiveContainer>
    </ChartWrapper>
  );
}


