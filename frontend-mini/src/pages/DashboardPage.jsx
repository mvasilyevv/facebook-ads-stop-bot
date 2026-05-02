import React, { useEffect, useState } from "react";
import { fetchJson } from "../api.js";

// Страница дашборда — KPI и управление сканированием
export default function DashboardPage() {
  const [stats, setStats] = useState(null);
  const [obsSettings, setObsSettings] = useState(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState(null);
  const [toggling, setToggling] = useState(false);

  const loadData = () => {
    setLoading(true);
    Promise.all([
      fetchJson("/dashboard/stats"),
      fetchJson("/settings/observer"),
    ])
      .then(([statsData, obsData]) => {
        setStats(statsData);
        setObsSettings(obsData ?? null);
        setError(null);
      })
      .catch((err) => setError(err.message))
      .finally(() => setLoading(false));
  };

  useEffect(() => {
    loadData();
  }, []);

  const scanning = obsSettings?.is_scanning_enabled ?? true;
  const pauseUntilMs = obsSettings?.pause_until ? new Date(obsSettings.pause_until).getTime() : null;
  const pauseActive = pauseUntilMs != null && pauseUntilMs > Date.now();
  const pauseMinsLeft = pauseActive ? Math.max(1, Math.round((pauseUntilMs - Date.now()) / 60000)) : null;

  const doToggle = async (enabled) => {
    setToggling(true);
    try {
      await fetchJson("/settings/observer/scanning", {
        method: "PATCH",
        body: JSON.stringify({ enabled }),
      });
      setObsSettings((prev) => prev ? { ...prev, is_scanning_enabled: enabled, pause_until: enabled ? null : prev.pause_until } : prev);
    } catch (err) {
      setError(err.message);
    } finally {
      setToggling(false);
    }
  };

  if (loading) return <div className="loading">Загрузка...</div>;
  if (error) return <div className="error-screen"><p className="status-error">{error}</p></div>;

  return (
    <div>
      <h1>Дашборд</h1>

      <div className="kpi-strip">
        <div className="kpi-item">
          <div className="kpi-value">{stats?.active_ads_count ?? "—"}</div>
          <div className="kpi-label">Активных объявлений</div>
        </div>
        <div className="kpi-item">
          <div className="kpi-value">{stats?.ads_in_stop ?? "—"}</div>
          <div className="kpi-label">Стоп-сигналов</div>
        </div>
        <div className="kpi-item">
          <div className="kpi-value">{stats?.ads_disabled_today ?? "—"}</div>
          <div className="kpi-label">Отключено сегодня</div>
        </div>
      </div>

      <div className="card" style={{ marginTop: 16 }}>
        <h2>Сканирование</h2>
        <p>
          Статус:{" "}
          <span className={scanning ? "status-ok" : "status-error"}>
            {scanning ? "включено" : pauseActive ? "на паузе" : "приостановлено"}
          </span>
        </p>
        {pauseActive && (
          <p className="status-warning" style={{ fontSize: 13 }}>
            ⏸ Пауза до {new Date(obsSettings.pause_until).toLocaleTimeString("ru-RU", { hour: "2-digit", minute: "2-digit" })} (осталось {pauseMinsLeft} мин)
          </p>
        )}
        <div style={{ display: "flex", gap: 8, flexWrap: "wrap", marginTop: 8 }}>
          {pauseActive ? (
            <button className="btn" onClick={() => doToggle(true)} disabled={toggling}>
              {toggling ? "..." : "▶ Возобновить"}
            </button>
          ) : (
            <button className="btn" onClick={() => doToggle(!scanning)} disabled={toggling}>
              {toggling ? "..." : scanning ? "Приостановить" : "Возобновить"}
            </button>
          )}
        </div>
      </div>

      <button className="btn btn-secondary" onClick={loadData} style={{ marginTop: 8 }}>
        Обновить
      </button>
    </div>
  );
}
