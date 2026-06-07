/**
 * SpendChart — мягкий area-график «spend × час» для hero-строки.
 * Чистый SVG: пунктирные направляющие + база, draw-on линия, пульс на «now»,
 * hover-тултип. Пустой ряд → заглушка (без фейка). Порт из web (единый канон).
 */
import { useEffect, useId, useRef, useState } from "react";
import { formatSpend } from "@fb/shared";
import { PulseDot } from "@/components/data/PulseDot";

interface SpendChartProps {
  /** Ряд значений spend по часам. */
  data: number[];
  /** Высота области графика в px. */
  height?: number;
  /** Показывать пульс на последней точке. */
  live?: boolean;
  /** Draw-on анимация линии. */
  animate?: boolean;
}

function prefersReducedMotion(): boolean {
  return (
    typeof window !== "undefined" &&
    typeof window.matchMedia === "function" &&
    window.matchMedia("(prefers-reduced-motion: reduce)").matches
  );
}

export function SpendChart({ data, height = 120, live = true, animate = true }: SpendChartProps) {
  const wrapRef = useRef<HTMLDivElement>(null);
  const lineRef = useRef<SVGPolylineElement>(null);
  const [w, setW] = useState(320);
  const [hover, setHover] = useState<number | null>(null);
  const gid = useId().replace(/:/g, "");

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
    if (data.length < 2) return;
    const len = el.getTotalLength();
    el.style.transition = "none";
    el.style.strokeDasharray = String(len);
    el.style.strokeDashoffset = String(len);
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

  if (n < 2) {
    return (
      <div ref={wrapRef} className="relative w-full" style={{ height: H }}>
        <div className="flex h-full items-center justify-center text-[12px] text-bg-8">
          Нет данных о тратах за период
        </div>
      </div>
    );
  }

  const max = Math.max(...data) * 1.1 || 1;
  const x = (i: number) => (i / (n - 1)) * w;
  const y = (v: number) => padT + innerH - (v / max) * innerH;
  const linePts = data.map((v, i) => `${x(i)},${y(v)}`).join(" ");
  const areaPath =
    `M${x(0)},${y(data[0]!)} ` +
    data.map((v, i) => `L${x(i)},${y(v)}`).join(" ") +
    ` L${x(n - 1)},${H - padB} L${x(0)},${H - padB} Z`;

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

        {[0, 6, 12, 18, n - 1].map((i) => (
          <text
            key={i}
            x={x(i)}
            y={H - 6}
            fontSize={10}
            fontFamily="var(--font-num)"
            fill="var(--bg-8)"
            textAnchor={i === 0 ? "start" : i === n - 1 ? "end" : "middle"}
          >
            {String(i % 24).padStart(2, "0")}:00
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
              cy={y(data[hover]!)}
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
            top: y(data[n - 1]!) - 4,
          }}
        />
      )}

      {hover != null && (
        <div
          className="pointer-events-none absolute top-0 border border-bg-6 bg-bg-3 px-2 py-1.5"
          style={{ left: Math.min(Math.max(x(hover) - 50, 0), w - 100), minWidth: 92 }}
        >
          <div className="font-display text-[9px] font-semibold uppercase tracking-[0.12em] text-bg-9">
            {String(hover % 24).padStart(2, "0")}:00 · SPEND
          </div>
          <div className="mt-0.5 font-display text-[14px] tabular-nums text-bg-11">
            {formatSpend(data[hover])}
          </div>
        </div>
      )}
    </div>
  );
}
