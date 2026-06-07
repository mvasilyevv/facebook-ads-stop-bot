/**
 * MetricsGrid — сетка KPI по канону дизайна.
 * 3 колонки, border-collapse вид (как в дизайн-прототипе AdSheet).
 * Каждая ячейка: eyebrow-лейбл 9px + значение mono tabular-nums 15px.
 * flag → text-danger + bg-danger-bg.
 */

export interface MetricCell {
  label: string;
  value: string | number | null | undefined;
  /** Подсветить ячейку как опасную (превышение порога). */
  flag?: boolean;
}

interface MetricsGridProps {
  cells: MetricCell[];
  className?: string;
}

export function MetricsGrid({ cells, className }: MetricsGridProps) {
  const COLS = 3;
  return (
    <div
      className={className}
      style={{
        display: "grid",
        gridTemplateColumns: "1fr 1fr 1fr",
        border: "1px solid var(--color-bg-5)",
      }}
    >
      {cells.map((cell, i) => {
        const row = Math.floor(i / COLS);
        const col = i % COLS;
        return (
          <div
            key={i}
            style={{
              padding: "10px 12px",
              borderRight: col !== COLS - 1 ? "1px solid var(--color-bg-5)" : undefined,
              borderTop: row > 0 ? "1px solid var(--color-bg-5)" : undefined,
              background: cell.flag ? "var(--color-danger-bg)" : "transparent",
            }}
          >
            {/* eyebrow-лейбл */}
            <span
              style={{
                display: "block",
                fontSize: 9,
                fontFamily: "var(--font-display, inherit)",
                fontWeight: 600,
                textTransform: "uppercase",
                letterSpacing: "0.12em",
                lineHeight: 1,
                color: cell.flag ? "var(--color-danger)" : "var(--color-bg-9)",
              }}
            >
              {cell.label}
            </span>
            {/* значение */}
            <span
              style={{
                display: "block",
                marginTop: 4,
                fontSize: 15,
                fontFamily: "var(--font-display, inherit)",
                fontVariantNumeric: "tabular-nums",
                lineHeight: 1,
                color: cell.flag ? "var(--color-danger)" : "var(--color-bg-11)",
              }}
            >
              {cell.value ?? "—"}
            </span>
          </div>
        );
      })}
    </div>
  );
}
