import React from "react";

// Компактный блок ошибки с кнопкой повтора
export default function ErrorBox({ message, onRetry }) {
  return (
    <div className="error-box">
      <p>{message || "Произошла ошибка"}</p>
      {onRetry && (
        <button className="btn btn-secondary btn-sm" style={{ width: "auto", margin: "0 auto" }} onClick={onRetry}>
          Повторить
        </button>
      )}
    </div>
  );
}
