import React, { useEffect, useState, useCallback } from "react";
import { fetchJson } from "../api.js";
import Loader from "../components/Loader.jsx";
import ErrorBox from "../components/ErrorBox.jsx";
import Card from "../components/Card.jsx";
import { haptic } from "../theme.js";

const WORKER_LABELS = {
  observer: "Observer",
  telegram_poller: "Telegram Poller",
  disable_worker: "Disable Worker",
  enable_worker: "Enable Worker",
  meta_api_worker: "Meta API Worker",
  enable_recommendation_worker: "Enable Recommendation",
  health_watchdog: "Health Watchdog",
  cleanup_worker: "Cleanup Worker",
  reconciler_worker: "Reconciler",
  digest_scheduler: "Digest Scheduler",
  creator_worker: "Creator Worker",
  creator_recorder: "Creator Recorder",
  cabinet_scheduler: "Cabinet Scheduler",
  tracker_aggregator: "Tracker Aggregator",
};

// Backend health/details отдаёт статус воркера как ONLINE/OFFLINE — маппим
// в внутренние ok/error для dot/label.
function mapWorkerStatus(status) {
  if (status === "ONLINE") return "ok";
  if (status === "OFFLINE") return "error";
  return status; // на случай иных значений
}

function dotClass(status) {
  if (status === "ok") return "health-dot health-dot-success";
  if (status === "warn" || status === "stale") return "health-dot health-dot-warning";
  if (status === "error") return "health-dot health-dot-danger";
  return "health-dot health-dot-neutral";
}

function statusLabel(status) {
  if (status === "ok") return <span className="status-ok">OK</span>;
  if (status === "warn" || status === "stale") return <span className="status-warn">{status}</span>;
  if (status === "error") return <span className="status-error">офлайн</span>;
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
    haptic.impact("medium");
    try {
      await fetchJson("/vision/reconnect", { method: "POST" });
      haptic.notify("success");
      setReconnectMsg({ type: "ok", text: "Переподключение запущено" });
      // Обновляем статус через 2 секунды
      setTimeout(load, 2000);
    } catch (err) {
      haptic.notify("error");
      setReconnectMsg({ type: "err", text: err.message });
    } finally {
      setReconnecting(false);
    }
  };

  if (loading) return <Loader />;
  if (error) return <ErrorBox message={error} onRetry={load} />;

  // health/details.workers — МАССИВ WorkerStatus[] (desktop-фронт ждёт массив).
  const workers = Array.isArray(details?.workers) ? details.workers : [];
  const overall = details?.overall ?? null;

  return (
    <div>
      <h1>Состояние системы</h1>

      {overall && (
        <p
          className={
            overall === "HEALTHY"
              ? "status-ok"
              : overall === "CRITICAL"
              ? "status-error"
              : "status-warn"
          }
          style={{ marginBottom: 8 }}
        >
          Общий статус: {overall}
        </p>
      )}

      {/* Кнопка переподключения Vision */}
      <Card title="Vision Browser">
        <p className="hint" style={{ marginBottom: 8 }}>
          Переподключает CDP-соединение с anti-detect браузером.
        </p>
        {reconnectMsg && (
          <p
            className={reconnectMsg.type === "ok" ? "status-ok" : "status-error"}
            style={{ marginBottom: 8, fontSize: 13 }}
          >
            {reconnectMsg.text}
          </p>
        )}
        <button className="btn" onClick={handleReconnect} disabled={reconnecting}>
          {reconnecting ? "Переподключаю..." : "🔄 Reconnect Vision"}
        </button>
      </Card>

      {/* Воркеры */}
      <Card title="Воркеры">
        {workers.map((w) => {
          const status = mapWorkerStatus(w.status);
          return (
            <div className="health-node" key={w.name}>
              <div className={dotClass(status)} />
              <div style={{ flex: 1 }}>
                <div style={{ fontSize: 14, fontWeight: 500 }}>
                  {WORKER_LABELS[w.name] ?? w.name}
                </div>
                <div style={{ display: "flex", gap: 8, alignItems: "center" }}>
                  {statusLabel(status)}
                  {w.ttl_seconds != null && (
                    <span className="hint" style={{ fontSize: 12 }}>
                      TTL {w.ttl_seconds}s
                    </span>
                  )}
                </div>
              </div>
            </div>
          );
        })}
        {workers.length === 0 && <p className="hint">Данные о воркерах недоступны.</p>}
      </Card>

      <button className="btn btn-secondary" onClick={load}>
        Обновить
      </button>
    </div>
  );
}
