/**
 * Sparkline — миниатюрный filled-линейный график (для KPI-ячеек).
 *
 * Портировано из design_handoff/dashboard-shared.jsx (Sparkline). Чистый SVG,
 * без зависимостей. Если данных нет (пусто/одна точка) — ничего не рисуем
 * (никаких фейковых линий). Все точки равноудалены по X, нормируются по Y.
 */

interface SparklineProps {
  /** Ряд значений (например, spend по часам). */
  data: number[];
  /** CSS-цвет линии/заливки (var(--warning) и т.п.). */
  color: string;
  /** Ширина в px. */
  w?: number;
  /** Высота в px. */
  h?: number;
  /** Рисовать заливку под линией. */
  fill?: boolean;
}

export function Sparkline({ data, color, w = 72, h = 26, fill = false }: SparklineProps) {
  // Без минимум двух точек строить нечего — скрываем (без фейка).
  if (!data || data.length < 2) {
    return <svg width={w} height={h} aria-hidden="true" className="block" />;
  }

  const max = Math.max(...data);
  const min = Math.min(...data);
  const rng = max - min || 1;
  const pts = data.map(
    (v, i) => `${(i / (data.length - 1)) * w},${h - ((v - min) / rng) * (h - 4) - 2}`,
  );
  const lastY = h - ((data[data.length - 1]! - min) / rng) * (h - 4) - 2;

  return (
    <svg
      width={w}
      height={h}
      aria-hidden="true"
      className="block overflow-visible"
    >
      {fill && (
        <polygon
          points={`0,${h} ${pts.join(" ")} ${w},${h}`}
          fill={color}
          opacity={0.12}
        />
      )}
      <polyline
        points={pts.join(" ")}
        fill="none"
        stroke={color}
        strokeWidth={1.5}
        strokeLinejoin="round"
        strokeLinecap="round"
      />
      <circle cx={w} cy={lastY} r={2} fill={color} />
    </svg>
  );
}
