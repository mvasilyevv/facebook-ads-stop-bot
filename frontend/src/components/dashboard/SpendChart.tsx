/**
 * SpendChart — мягкий area-график «spend × час» (24 точки) для hero-строки.
 *
 * Канон design_handoff/components.jsx (SpendChart): чистый SVG, БЕЗ жёсткой
 * Bloomberg-сетки — только тонкие пунктирные горизонтальные направляющие +
 * сплошная базовая линия. Линия «рисуется» на mount (stroke-dashoffset),
 * на последней точке — пульсирующий «now»-дот, по hover — тултип.
 *
 * Данные — реальный ряд spend по часам (number[]), из useChartData.
 * Пустой/короткий ряд → плоская заглушка (без фейка).
 */

import { useEffect, useId, useRef, useState } from "react";
import { PulseDot } from "@/components/data/PulseDot";
import { formatSpend } from "@fb/shared";
import { formatDisplayTime } from "@/lib/timezone";

export interface SpendChartPoint {
  ts: string;
  spend: number;
}

interface SpendChartProps {
  /** Реальные timestamp + spend; ось не придумывает номера часов. */
  data: SpendChartPoint[];
  /** Высота области графика в px. */
  height?: number;
  /** Показывать пульсирующий «now»-дот на последней точке. */
  live?: boolean;
  /** Включить draw-on анимацию линии. */
  animate?: boolean;
}

function prefersReducedMotion(): boolean {
  return (
    typeof window !== "undefined" &&
    typeof window.matchMedia === "function" &&
    window.matchMedia("(prefers-reduced-motion: reduce)").matches
  );
}

export function SpendChart({ data, height = 170, live = true, animate = false }: SpendChartProps) {
  const wrapRef = useRef<HTMLDivElement>(null);
  const lineRef = useRef<SVGPolylineElement>(null);
  const [w, setW] = useState(560);
  const [hover, setHover] = useState<number | null>(null);
  const gid = useId().replace(/:/g, "");

  // Резайз-обсервер ширины контейнера (guard для jsdom без ResizeObserver).
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

  // Draw-on анимация линии.
  useEffect(() => {
    const el = lineRef.current;
    if (!animate || !el || typeof el.getTotalLength !== "function") return;
    if (prefersReducedMotion()) return;
    if (data.length < 2) return;
    const len = el.getTotalLength();
    el.style.transition = "none";
    el.style.strokeDasharray = String(len);
    el.style.strokeDashoffset = String(len);
    // форсим reflow, чтобы перезапустить transition
    void el.getBoundingClientRect();
    requestAnimationFrame(() => {
      el.style.transition = "stroke-dashoffset 900ms var(--ease-out)";
      el.style.strokeDashoffset = "0";
    });
  }, [animate, w, data]);

  const H = height;
  const padB = 22;
  const padT = 10;
  const innerH = H - padB - padT;
  const n = data.length;

  // Пустой ряд — плоская заглушка.
  if (n < 2) {
    return (
      <div ref={wrapRef} className="relative w-full" style={{ height: H }}>
        <div className="flex h-full items-center justify-center text-[12px] text-bg-8">
          Нет данных о тратах за период
        </div>
      </div>
    );
  }

  const values = data.map((point) => point.spend);
  const max = Math.max(...values) * 1.1 || 1;
  const padX = 8;
  const x = (i: number) => padX + (i / (n - 1)) * Math.max(0, w - padX * 2);
  const y = (v: number) => padT + innerH - (v / max) * innerH;
  const linePts = data.map((point, i) => `${x(i)},${y(point.spend)}`).join(" ");
  const areaPath =
    `M${x(0)},${y(data[0]!.spend)} ` +
    data.map((point, i) => `L${x(i)},${y(point.spend)}`).join(" ") +
    ` L${x(n - 1)},${H - padB} L${x(0)},${H - padB} Z`;
  const tickCount = Math.max(2, Math.floor(w / 110));
  const tickIndices = Array.from(
    new Set(
      Array.from({ length: tickCount }, (_, index) =>
        Math.round((index / (tickCount - 1)) * (n - 1)),
      ),
    ),
  );

  const onMove = (e: React.MouseEvent<HTMLDivElement>) => {
    const rect = wrapRef.current?.getBoundingClientRect();
    if (!rect) return;
    const px = e.clientX - rect.left;
    const i = Math.max(0, Math.min(n - 1, Math.round((px / rect.width) * (n - 1))));
    setHover(i);
  };

  return (
    <div
      ref={wrapRef}
      className="relative w-full overflow-hidden"
      onMouseMove={onMove}
      onMouseLeave={() => setHover(null)}
    >
      <svg width="100%" height={H} className="block overflow-hidden">
        <defs>
          <linearGradient id={`fill${gid}`} x1="0" y1="0" x2="0" y2="1">
            <stop offset="0%" stopColor="var(--accent)" stopOpacity={0.18} />
            <stop offset="100%" stopColor="var(--accent)" stopOpacity={0} />
          </linearGradient>
        </defs>

        {/* Мягкие горизонтальные направляющие (пунктир) + сплошная база — НЕ сетка */}
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

        {/* Подписи часов по нижней оси */}
        {tickIndices.map((i) => (
          <text
            key={i}
            x={x(i)}
            y={H - 6}
            fontSize={10}
            fontFamily="var(--font-num)"
            fill="var(--bg-8)"
            textAnchor={i === 0 ? "start" : i === n - 1 ? "end" : "middle"}
          >
            {formatDisplayTime(data[i]!.ts)}
          </text>
        ))}

        {/* Hover-курсор */}
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
              cy={y(data[hover]!.spend)}
              r={3.5}
              fill="var(--bg-0)"
              stroke="var(--accent)"
              strokeWidth={1.5}
            />
          </g>
        )}
      </svg>

      {/* Пульсирующий «now»-дот на последней точке */}
      {live && (
        <PulseDot
          size={8}
          color="var(--accent)"
          style={{
            position: "absolute",
            pointerEvents: "none",
            left: x(n - 1) - 4,
            top: y(data[n - 1]!.spend) - 4,
          }}
        />
      )}

      {/* Тултип */}
      {hover != null && (
        <div
          className="pointer-events-none absolute top-0 rounded-[var(--radius-2)] border border-[var(--hairline-strong)] bg-bg-3 px-2.5 py-1.5"
          style={{ left: Math.min(Math.max(x(hover) - 50, 0), w - 100), minWidth: 92 }}
        >
          <div className="font-display text-[9px] font-semibold uppercase tracking-[0.12em] text-bg-9">
            {formatDisplayTime(data[hover]!.ts)} · SPEND
          </div>
          <div className="mt-0.5 font-display text-[14px] tabular-nums text-bg-11">
            {formatSpend(data[hover]!.spend)}
          </div>
        </div>
      )}
    </div>
  );
}
