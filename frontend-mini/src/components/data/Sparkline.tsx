/**
 * Sparkline — миниатюрный filled-линейный график (KPI-ячейки, ad detail).
 * Чистый SVG, без зависимостей. <2 точек → пусто (без фейка). Порт из web.
 */
interface SparklineProps {
  /** Ряд значений. */
  data: number[];
  /** CSS-цвет линии/заливки. */
  color: string;
  /** Ширина в px. */
  w?: number;
  /** Высота в px. */
  h?: number;
  /** Рисовать заливку под линией. */
  fill?: boolean;
}

export function Sparkline({ data, color, w = 72, h = 26, fill = false }: SparklineProps) {
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
    <svg width={w} height={h} aria-hidden="true" className="block overflow-visible">
      {fill && (
        <polygon points={`0,${h} ${pts.join(" ")} ${w},${h}`} fill={color} opacity={0.12} />
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
