/**
 * StatsHourlyChart — SVG-график дельта-серии (по часам или по дням).
 * Порт паттерна SpendChart: draw-on анимация, ResizeObserver, hover-тултип,
 * «Нет данных» при <2 точек. Переключатель метрики (spend | лиды | депы) —
 * pill-кнопки ≥44px. Чистый SVG, без Recharts.
 */
import { useEffect, useId, useMemo, useRef, useState } from "react";
import { formatSpend, formatInt } from "@fb/shared";
import { PulseDot } from "@/components/data/PulseDot";
import { cn } from "@/lib/cn";

export type StatsChartMetric = "spend" | "leads" | "deposits";

const METRIC_OPTIONS: { id: StatsChartMetric; label: string }[] = [
  { id: "spend", label: "Spend" },
  { id: "leads", label: "Лиды" },
  { id: "deposits", label: "Депы" },
];

/** Точка серии — общий формат для series_hourly / series_daily. */
export interface StatsChartPoint {
  /** Метка на оси X (уже отформатирована вызывающей стороной: "14:00" или "02.07"). */
  label: string;
  spend: number;
  leads: number;
  deposits: number;
}

interface StatsHourlyChartProps {
  data: StatsChartPoint[];
  height?: number;
  live?: boolean;
  animate?: boolean;
}

function prefersReducedMotion(): boolean {
  return (
    typeof window !== "undefined" &&
    typeof window.matchMedia === "function" &&
    window.matchMedia("(prefers-reduced-motion: reduce)").matches
  );
}

function formatMetricValue(metric: StatsChartMetric, value: number): string {
  return metric === "spend" ? formatSpend(value) : formatInt(value);
}

export function StatsHourlyChart({ data, height = 140, live = true, animate = true }: StatsHourlyChartProps) {
  const [metric, setMetric] = useState<StatsChartMetric>("spend");
  const wrapRef = useRef<HTMLDivElement>(null);
  const lineRef = useRef<SVGPolylineElement>(null);
  const [w, setW] = useState(320);
  const [hover, setHover] = useState<number | null>(null);
  const gid = useId().replace(/:/g, "");

  const series = useMemo(() => data.map((p) => p[metric]), [data, metric]);

  useEffect(() => {
    const el = wrapRef.current;
    if (!el || typeof ResizeObserver === "undefined") return undefined;
    const ro = new ResizeObserver((entries) => {
      const cr = entries[0]?.contentRect;
      if (cr && cr.width > 0) setW(cr.width);
    });
    ro.observe(el);
    return () => ro.disconnect();
  }, []);

  useEffect(() => {
    const el = lineRef.current;
    if (!animate || !el || typeof el.getTotalLength !== "function") return;
    if (prefersReducedMotion()) return;
    if (series.length < 2) return;
    const len = el.getTotalLength();
    el.style.transition = "none";
    el.style.strokeDasharray = String(len);
    el.style.strokeDashoffset = String(len);
    void el.getBoundingClientRect();
    requestAnimationFrame(() => {
      el.style.transition = "stroke-dashoffset 900ms var(--ease-out)";
      el.style.strokeDashoffset = "0";
    });
  }, [animate, w, series, metric]);

  const H = height;
  const padB = 22;
  const padT = 10;
  const innerH = H - padB - padT;
  const n = series.length;

  const switcher = (
    <div role="group" aria-label="Метрика графика" className="flex items-center gap-1.5 mb-3">
      {METRIC_OPTIONS.map((opt) => {
        const active = opt.id === metric;
        return (
          <button
            key={opt.id}
            type="button"
            aria-pressed={active}
            onClick={() => setMetric(opt.id)}
            className={cn(
              "min-h-[36px] min-w-[44px] px-3 text-[12px] font-display font-semibold uppercase tracking-[0.06em] border rounded-[var(--radius-2)] transition-colors",
              active
                ? "bg-accent text-bg-0 border-accent"
                : "bg-bg-1 text-bg-9 border-[var(--hairline)] hover:text-bg-11",
            )}
          >
            {opt.label}
          </button>
        );
      })}
    </div>
  );

  if (n < 2) {
    return (
      <div>
        {switcher}
        <div ref={wrapRef} className="relative w-full" style={{ height: H }}>
          <div className="flex h-full items-center justify-center text-[12px] text-bg-8">
            Нет данных
          </div>
        </div>
      </div>
    );
  }

  const max = Math.max(...series) * 1.1 || 1;
  const x = (i: number) => (i / (n - 1)) * w;
  const y = (v: number) => padT + innerH - (v / max) * innerH;
  const linePts = series.map((v, i) => `${x(i)},${y(v)}`).join(" ");
  const areaPath =
    `M${x(0)},${y(series[0]!)} ` +
    series.map((v, i) => `L${x(i)},${y(v)}`).join(" ") +
    ` L${x(n - 1)},${H - padB} L${x(0)},${H - padB} Z`;

  const onMove = (e: React.MouseEvent<HTMLDivElement>) => {
    const rect = wrapRef.current?.getBoundingClientRect();
    if (!rect) return;
    const px = e.clientX - rect.left;
    const i = Math.max(0, Math.min(n - 1, Math.round((px / rect.width) * (n - 1))));
    setHover(i);
  };

  // Показываем не более 6 подписей оси X, чтобы не сливались на мобильном.
  const labelStep = Math.max(1, Math.ceil(n / 6));
  const labelIdx = Array.from({ length: n }, (_, i) => i).filter(
    (i) => i % labelStep === 0 || i === n - 1,
  );

  return (
    <div>
      {switcher}
      <div
        ref={wrapRef}
        className="relative w-full"
        onMouseMove={onMove}
        onMouseLeave={() => setHover(null)}
      >
        <svg width="100%" height={H} className="block overflow-visible">
          <defs>
            <linearGradient id={`fill${gid}`} x1="0" y1="0" x2="0" y2="1">
              <stop offset="0%" stopColor="var(--accent)" stopOpacity={0.18} />
              <stop offset="100%" stopColor="var(--accent)" stopOpacity={0} />
            </linearGradient>
          </defs>

          {[0.25, 0.5, 0.75, 1].map((g, i) => (
            <line
              key={g}
              x1={0}
              x2={w}
              y1={padT + innerH * g}
              y2={padT + innerH * g}
              stroke="var(--bg-5)"
              strokeWidth={1}
              strokeDasharray={i === 3 ? "0" : "2 4"}
              opacity={i === 3 ? 1 : 0.6}
            />
          ))}

          <path d={areaPath} fill={`url(#fill${gid})`} />
          <polyline
            ref={lineRef}
            points={linePts}
            fill="none"
            stroke="var(--accent)"
            strokeWidth={1.5}
            strokeLinejoin="round"
          />

          {labelIdx.map((i) => (
            <text
              key={i}
              x={x(i)}
              y={H - 6}
              fontSize={10}
              fontFamily="var(--font-num)"
              fill="var(--bg-8)"
              textAnchor={i === 0 ? "start" : i === n - 1 ? "end" : "middle"}
            >
              {data[i]!.label}
            </text>
          ))}

          {hover != null && (
            <g>
              <line
                x1={x(hover)}
                x2={x(hover)}
                y1={padT}
                y2={H - padB}
                stroke="var(--bg-7)"
                strokeWidth={1}
              />
              <circle
                cx={x(hover)}
                cy={y(series[hover]!)}
                r={3.5}
                fill="var(--bg-0)"
                stroke="var(--accent)"
                strokeWidth={1.5}
              />
            </g>
          )}
        </svg>

        {live && (
          <PulseDot
            size={8}
            color="var(--accent)"
            style={{
              position: "absolute",
              pointerEvents: "none",
              right: -1,
              top: y(series[n - 1]!) - 4,
            }}
          />
        )}

        {hover != null && (
          <div
            className="pointer-events-none absolute top-0 border border-[var(--hairline-strong)] bg-bg-3 px-2.5 py-1.5 rounded-[var(--radius-2)]"
            style={{ left: Math.min(Math.max(x(hover) - 50, 0), w - 100), minWidth: 92 }}
          >
            <div className="font-display text-[9px] font-semibold uppercase tracking-[0.12em] text-bg-9">
              {data[hover]!.label} · {METRIC_OPTIONS.find((o) => o.id === metric)?.label.toUpperCase()}
            </div>
            <div className="mt-0.5 font-display text-[14px] tabular-nums text-bg-11">
              {formatMetricValue(metric, series[hover]!)}
            </div>
          </div>
        )}
      </div>
    </div>
  );
}
