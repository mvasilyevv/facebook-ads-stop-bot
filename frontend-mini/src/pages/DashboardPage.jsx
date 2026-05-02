import React, { useEffect, useState } from "react";
import { fetchJson } from "../api.js";
import Loader from "../components/Loader.jsx";
import ErrorBox from "../components/ErrorBox.jsx";
import MetricBadge from "../components/MetricBadge.jsx";
import Card from "../components/Card.jsx";
import { haptic } from "../theme.js";

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
    haptic.impact("medium");
    try {
      await fetchJson("/settings/observer/scanning", {
        method: "PATCH",
        body: JSON.stringify({ enabled }),
      });
      setObsSettings((prev) =>
        prev ? { ...prev, is_scanning_enabled: enabled, pause_until: enabled ? null : prev.pause_until } : prev
      );
      haptic.notify("success");
    } catch (err) {
      setError(err.message);
      haptic.notify("error");
    } finally {
      setToggling(false);
    }
  };

  if (loading) return <Loader />;
  if (error) return <ErrorBox message={error} onRetry={loadData} />;

  return (
    <div>
      <h1>Дашборд</h1>

      {/* KPI-плитки — горизонтальный скролл */}
      <div className="kpi-strip">
        <MetricBadge value={stats?.active_ads_count ?? "—"} label="Активных" />
        <MetricBadge value={stats?.ads_in_stop ?? "—"} label="Стоп-сигналов" danger={(stats?.ads_in_stop ?? 0) > 0} />
        <MetricBadge value={stats?.ads_disabled_today ?? "—"} label="Отключено сегодня" />
      </div>

      <Card title="Сканирование" style={{ marginTop: 6 }}>
        <p>
          Статус:{" "}
          <span className={scanning ? "status-ok" : "status-error"}>
            {scanning ? "включено" : pauseActive ? "на паузе" : "приостановлено"}
          </span>
        </p>
        {pauseActive && (
          <p className="hint" style={{ marginTop: 4 }}>
            ⏸ Пауза до{" "}
            {new Date(obsSettings.pause_until).toLocaleTimeString("ru-RU", {
              hour: "2-digit",
              minute: "2-digit",
            })}{" "}
            (осталось {pauseMinsLeft} мин)
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
      </Card>

      <button className="btn btn-secondary" onClick={loadData} style={{ marginTop: 8 }}>
        Обновить
      </button>
    </div>
  );
}
