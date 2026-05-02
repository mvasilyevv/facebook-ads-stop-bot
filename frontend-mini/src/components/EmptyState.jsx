import React from "react";

// Пустое состояние с иконкой и текстом
export default function EmptyState({ icon = "○", title, subtitle }) {
  return (
    <div className="empty-state">
      <div className="empty-icon">{icon}</div>
      {title && <div className="empty-title">{title}</div>}
      {subtitle && <div className="empty-sub">{subtitle}</div>}
    </div>
  );
}
