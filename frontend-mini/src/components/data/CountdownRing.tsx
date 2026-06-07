/**
 * CountdownRing + PausedRing — кольца scan-кластера шапки Dashboard.
 *   CountdownRing — SVG-кольцо обратного отсчёта (stroke-dashoffset), секунды по центру.
 *   PausedRing — dashed-кольцо + иконка паузы (Observer выключен).
 * Порт из web (единый канон).
 */
import { Pause } from "lucide-react";

interface CountdownRingProps {
  /** Текущее значение (секунды до скана). */
  value: number;
  /** Максимум (interval). */
  max: number;
  /** Размер кольца в px. */
  size?: number;
  /** Идёт ли скан — меняет цвет на accent. */
  active?: boolean;
}

export function CountdownRing({ value, max, size = 34, active }: CountdownRingProps) {
  const r = (size - 5) / 2;
  const circ = 2 * Math.PI * r;
  const frac = Math.max(0, Math.min(1, max > 0 ? value / max : 0));

  return (
    <span
      className="relative inline-flex items-center justify-center shrink-0"
      style={{ width: size, height: size }}
    >
      <svg width={size} height={size} style={{ transform: "rotate(-90deg)" }} aria-hidden="true">
        <circle cx={size / 2} cy={size / 2} r={r} fill="none" stroke="var(--bg-6)" strokeWidth={2} />
        <circle
          cx={size / 2}
          cy={size / 2}
          r={r}
          fill="none"
          stroke={active ? "var(--accent)" : "var(--accent-muted)"}
          strokeWidth={2}
          strokeDasharray={circ}
          strokeDashoffset={circ * (1 - frac)}
          strokeLinecap="round"
          style={{ transition: "stroke-dashoffset 0.95s linear" }}
        />
      </svg>
      <span
        className="absolute font-display tabular-nums text-bg-10"
        style={{ fontSize: size > 30 ? 11 : 10 }}
      >
        {value}
      </span>
    </span>
  );
}

interface PausedRingProps {
  size?: number;
}

export function PausedRing({ size = 34 }: PausedRingProps) {
  const r = (size - 5) / 2;
  return (
    <span
      className="relative inline-flex items-center justify-center shrink-0"
      style={{ width: size, height: size }}
    >
      <svg width={size} height={size} aria-hidden="true">
        <circle
          cx={size / 2}
          cy={size / 2}
          r={r}
          fill="none"
          stroke="var(--bg-6)"
          strokeWidth={2}
          strokeDasharray="3 4"
        />
      </svg>
      <span className="absolute flex text-warning" aria-hidden="true">
        <Pause size={size > 30 ? 14 : 12} strokeWidth={2} />
      </span>
    </span>
  );
}
