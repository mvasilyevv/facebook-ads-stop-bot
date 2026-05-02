import React from "react";

// KPI-плитка: число + подпись + опциональная тренд-стрелка
// trend: "up" | "down" | null
export default function MetricBadge({ value, label, trend, danger }) {
  const arrow = trend === "up" ? " ↑" : trend === "down" ? " ↓" : "";
  const arrowColor =
    trend === "up"
      ? "var(--color-success)"
      : trend === "down"
      ? "var(--color-danger)"
      : undefined;

  return (
    <div className="kpi-item">
      <div
        className="kpi-value"
        style={{ color: danger ? "var(--color-danger)" : undefined }}
      >
        {value ?? "—"}
        {arrow && (
          <span style={{ fontSize: 14, color: arrowColor }}>{arrow}</span>
        )}
      </div>
      <div className="kpi-label">{label}</div>
    </div>
  );
}
