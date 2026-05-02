import React from "react";

// Спиннер с текстом загрузки
export default function Loader({ text = "Загрузка..." }) {
  return (
    <div className="loader-wrap">
      <div className="spinner" />
      <span>{text}</span>
    </div>
  );
}
