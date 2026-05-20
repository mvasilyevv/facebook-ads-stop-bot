import React from "react";

/**
 * Плитка KPI с левым акцентным баром и моноширинным шрифтом для Telegram Mini App.
 * Neo Control Room тема.
 */
export default function KPIPlate({ title, value, status = "default" }) {
  const statusColors = {
    default: "var(--ops-accent)",
    ok: "var(--color-success)",
    warn: "var(--color-warning)",
    stop: "var(--color-danger)",
    info: "var(--color-info)",
  };

  const accentColor = statusColors[status] || "var(--ops-accent)";

  return (
    <div 
      style={{
        position: "relative",
        overflow: "hidden",
        borderRadius: "8px",
        border: "1px solid var(--border)",
        backgroundColor: "var(--tg-secondary-bg-color)",
        padding: "10px 12px 10px 16px",
        borderLeft: `4px solid ${accentColor}`,
        display: "flex",
        flexDirection: "column",
        justifyContent: "center",
        minHeight: "68px",
        boxShadow: "0 2px 8px rgba(0, 0, 0, 0.15)",
        transition: "transform 0.15s, border-color 0.15s"
      }}
    >
      <span style={{ 
        fontFamily: "monospace", 
        fontSize: "10px", 
        textTransform: "uppercase", 
        letterSpacing: "0.5px", 
        color: "var(--tg-hint-color)",
        whiteSpace: "nowrap",
        overflow: "hidden",
        textOverflow: "ellipsis"
      }}>
        {title}
      </span>
      <div style={{ 
        marginTop: "4px", 
        fontFamily: "'JetBrains Mono', monospace", 
        fontSize: "20px", 
        fontWeight: "700", 
        color: "var(--tg-text-color)",
        lineHeight: "1.2"
      }}>
        {value ?? "—"}
      </div>
    </div>
  );
}
