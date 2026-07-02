/**
 * SpendChart — мягкий area-график «spend × час» для hero-строки.
 * Чистый SVG: пунктирные направляющие + база, draw-on линия, пульс на «now»,
 * hover-тултип. Пустой ряд → заглушка (без фейка). Порт из web (единый канон).
 *
 * null-точки (аудит 02.07, LOW F2): "нет данных за бакет" (null) визуально
 * отличается от "потрачено 0" — рисуем разрыв линии/области вместо просадки
 * в ноль, чтобы не искажать тренд. null исключён из hover/пика/последней точки.
 */
import { useEffect, useId, useMemo, useRef, useState } from "react";
import { formatSpend } from "@fb/shared";
import { PulseDot } from "@/components/data/PulseDot";

interface SpendChartProps {
  /** Ряд значений spend по часам. null — нет данных за бакет (разрыв). */
  data: (number | null)[];
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

  // Непрерывные участки без null — на них рисуем линию/область, между ними разрыв.
  const segments = useMemo(() => {
    const out: number[][] = [];
    let cur: number[] = [];
    data.forEach((v, i) => {
      if (v == null) {
        if (cur.length) out.push(cur);
        cur = [];
      } else {
        cur.push(i);
      }
    });
    if (cur.length) out.push(cur);
    return out;
  }, [data]);

  const validValues = data.filter((v): v is number => v != null);

  if (n < 2 || validValues.length === 0) {
    return (
      <div ref={wrapRef} className="relative w-full" style={{ height: H }}>
        <div className="flex h-full items-center justify-center text-[12px] text-bg-8">
          Нет данных о тратах за период
        </div>
      </div>
    );
  }

  const max = Math.max(...validValues) * 1.1 || 1;
  const x = (i: number) => (i / (n - 1)) * w;
  const y = (v: number) => padT + innerH - (v / max) * innerH;

  // Индекс последней НЕ-null точки — на неё ставим pulse/last-value, а не
  // на последний элемент массива (может быть null-разрывом на самом краю).
  const lastValidIdx = (() => {
    for (let i = data.length - 1; i >= 0; i--) {
      if (data[i] != null) return i;
    }
    return null;
  })();

  const linePts = segments.map((seg) => seg.map((i) => `${x(i)},${y(data[i]!)}`).join(" "));
  const areaPaths = segments.map((seg) => {
    const first = seg[0]!;
    const last = seg[seg.length - 1]!;
    return (
      `M${x(first)},${y(data[first]!)} ` +
      seg.map((i) => `L${x(i)},${y(data[i]!)}`).join(" ") +
      ` L${x(last)},${H - padB} L${x(first)},${H - padB} Z`
    );
  });

  const onMove = (e: React.MouseEvent<HTMLDivElement>) => {
    const rect = wrapRef.current?.getBoundingClientRect();
    if (!rect) return;
    const px = e.clientX - rect.left;
    const i = Math.max(0, Math.min(n - 1, Math.round((px / rect.width) * (n - 1))));
    // Наведение на разрыв (null-точку) — тултип не показываем, курсор снят.
    if (data[i] == null) {
      setHover(null);
      return;
    }
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

        {areaPaths.map((d, i) => (
          <path key={`area-${i}`} d={d} fill={`url(#fill${gid})`} />
        ))}
        {linePts.map((pts, i) => (
          <polyline
            key={`line-${i}`}
            // draw-on анимация цепляется за первый сегмент (обычный случай — без разрывов).
            ref={i === 0 ? lineRef : undefined}
            points={pts}
            fill="none"
            stroke="var(--accent)"
            strokeWidth={1.5}
            strokeLinejoin="round"
          />
        ))}

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

        {hover != null && data[hover] != null && (
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

      {live && lastValidIdx != null && (
        <PulseDot
          size={8}
          color="var(--accent)"
          style={{
            position: "absolute",
            pointerEvents: "none",
            right: -1,
            top: y(data[lastValidIdx]!) - 4,
          }}
        />
      )}

      {hover != null && data[hover] != null && (
        <div
          className="pointer-events-none absolute top-0 border border-[var(--hairline-strong)] bg-bg-3 px-2.5 py-1.5 rounded-[var(--radius-2)]"
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
