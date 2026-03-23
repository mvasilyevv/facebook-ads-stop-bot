import { formatDateTime, formatMetricText } from "../lib/format";
import type { ProfileLaunchTrendPoint } from "../types";

type TrendStripProps = {
  title: string;
  points: ProfileLaunchTrendPoint[];
};

function toNumber(value: string | number): number {
  const parsed = typeof value === "number" ? value : Number(value);
  return Number.isFinite(parsed) ? parsed : 0;
}

export function TrendStrip({ title, points }: TrendStripProps) {
  const max = Math.max(0, ...points.map((point) => toNumber(point.value)));

  return (
    <div className="trend-strip">
      <div className="trend-strip__head">
        <strong>{title}</strong>
        <span>{points.length > 0 ? `${points.length} точек` : "нет данных"}</span>
      </div>
      {points.length === 0 ? (
        <div className="trend-strip__empty">График появится после сканов текущего запуска</div>
      ) : (
        <div className="trend-strip__bars">
          {points.map((point, index) => {
            const value = toNumber(point.value);
            const height = max <= 0 ? 8 : Math.max(8, Math.round((value / max) * 72));
            return (
              <div key={`${point.timestamp}-${index}`} className="trend-strip__bar-wrap" title={`${formatDateTime(point.timestamp)} · ${formatMetricText(point.value)}`}>
                <div className="trend-strip__bar" style={{ height }} />
                <span className="trend-strip__value">{formatMetricText(point.value)}</span>
              </div>
            );
          })}
        </div>
      )}
    </div>
  );
}
