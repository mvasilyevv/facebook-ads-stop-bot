import React, { useEffect, useState, useCallback } from "react";
import { fetchJson } from "../api.js";

const WORKER_LABELS = {
  observer: "Observer",
  telegram_poller: "Telegram Poller",
  disable: "Disable Worker",
  enable: "Enable Worker",
  enable_recommendation: "Enable Recommendation",
  health_watchdog: "Health Watchdog",
};

function dotClass(status) {
  if (status === "ok") return "health-dot health-dot-success";
  if (status === "warn" || status === "stale") return "health-dot health-dot-warning";
  if (status === "error") return "health-dot health-dot-danger";
  return "health-dot health-dot-neutral";
}

function statusLabel(status) {
  if (status === "ok") return <span className="status-ok">OK</span>;
  if (status === "warn" || status === "stale") return <span className="status-warn">{status}</span>;
  if (status === "error") return <span className="status-error">ошибка</span>;
  return <span className="hint">{status ?? "неизвестно"}</span>;
}

// Страница здоровья — статусы воркеров + кнопка переподключения Vision
export default function HealthPage() {
  const [details, setDetails] = useState(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState(null);
  const [reconnecting, setReconnecting] = useState(false);
  const [reconnectMsg, setReconnectMsg] = useState(null);

  const load = useCallback(() => {
    setLoading(true);
    fetchJson("/health/details")
      .then((data) => {
        setDetails(data);
        setError(null);
      })
      .catch((err) => setError(err.message))
      .finally(() => setLoading(false));
  }, []);

  useEffect(() => {
    load();
  }, [load]);

  const handleReconnect = async () => {
    setReconnecting(true);
    setReconnectMsg(null);
    try {
      await fetchJson("/vision/reconnect", { method: "POST" });
      setReconnectMsg({ type: "ok", text: "Переподключение запущено" });
      // Обновляем статус через 2 секунды
      setTimeout(load, 2000);
    } catch (err) {
      setReconnectMsg({ type: "err", text: err.message });
    } finally {
      setReconnecting(false);
    }
  };

  if (loading) return <div className="loading">Загрузка...</div>;
  if (error)
    return (
      <div className="error-screen">
        <p className="status-error">{error}</p>
        <button className="btn btn-secondary" style={{ marginTop: 16 }} onClick={load}>
          Повторить
        </button>
      </div>
    );

  const workers = details?.workers ?? {};

  return (
    <div>
      <h1>Состояние системы</h1>

      {/* Кнопка переподключения Vision */}
      <div className="card">
        <div className="card-title">Vision Browser</div>
        <p className="hint" style={{ marginBottom: 8 }}>
          Переподключает CDP-соединение с anti-detect браузером.
        </p>
        {reconnectMsg && (
          <p className={reconnectMsg.type === "ok" ? "status-ok" : "status-error"} style={{ marginBottom: 8, fontSize: 13 }}>
            {reconnectMsg.text}
          </p>
        )}
        <button className="btn" onClick={handleReconnect} disabled={reconnecting}>
          {reconnecting ? "Переподключаю..." : "🔄 Reconnect Vision"}
        </button>
      </div>

      {/* Воркеры */}
      <div className="card">
        <div className="card-title">Воркеры</div>
        {Object.entries(workers).map(([key, info]) => (
          <div className="health-node" key={key}>
            <div className={dotClass(info?.status)} />
            <div style={{ flex: 1 }}>
              <div style={{ fontSize: 14, fontWeight: 500 }}>{WORKER_LABELS[key] ?? key}</div>
              <div style={{ display: "flex", gap: 8, alignItems: "center" }}>
                {statusLabel(info?.status)}
                {info?.message && (
                  <span className="hint" style={{ fontSize: 12 }}>
                    {info.message}
                  </span>
                )}
              </div>
            </div>
          </div>
        ))}
        {Object.keys(workers).length === 0 && (
          <p className="hint">Данные о воркерах недоступны.</p>
        )}
      </div>

      <button className="btn btn-secondary" onClick={load}>
        Обновить
      </button>
    </div>
  );
}
