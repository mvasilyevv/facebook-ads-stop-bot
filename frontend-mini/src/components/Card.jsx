import React from "react";

// Обёртка-карточка на основе --tg-secondary-bg-color
export default function Card({ children, title, style, className = "" }) {
  return (
    <div className={`card ${className}`} style={style}>
      {title && <div className="card-title">{title}</div>}
      {children}
    </div>
  );
}
