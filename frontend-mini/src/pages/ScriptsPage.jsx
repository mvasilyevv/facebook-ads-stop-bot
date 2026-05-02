import React from "react";

// Страница скриптов — ссылка на полный веб-интерфейс
// Загрузка файлов и скрипты создания кампаний требуют десктопного интерфейса
export default function ScriptsPage() {
  return (
    <div>
      <h1>Скрипты</h1>

      <div className="card scripts-notice">
        <div className="icon">🖥️</div>
        <p style={{ fontSize: 15, fontWeight: 600, marginBottom: 6 }}>
          Требуется десктопный браузер
        </p>
        <p className="hint" style={{ marginBottom: 16 }}>
          Уникализация креативов и создание кампаний из папки доступны только в полной
          версии интерфейса — из-за загрузки файлов и выбора папок.
        </p>
        <a
          href="/scripts"
          className="btn"
          style={{ textDecoration: "none", textAlign: "center" }}
        >
          Открыть Скрипты в браузере
        </a>
      </div>

      {/* Быстрые ссылки на другие инструменты */}
      <div className="card">
        <div className="card-title">Быстрые действия</div>

        <a
          href="/offers"
          className="btn btn-secondary"
          style={{ textAlign: "center", textDecoration: "none", marginTop: 0, marginBottom: 8 }}
        >
          🎯 Управление офферами
        </a>
        <a
          href="/ads"
          className="btn btn-secondary"
          style={{ textAlign: "center", textDecoration: "none", marginTop: 0 }}
        >
          📢 Мониторинг объявлений
        </a>
      </div>
    </div>
  );
}
