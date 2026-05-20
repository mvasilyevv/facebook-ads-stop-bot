import React, { useEffect, useState } from "react";
import { useNavigate } from "react-router-dom";
import { fetchJson, getDashboardIncidents, getDisableTasks, disableAd } from "../api.js";
import Loader from "../components/Loader.jsx";
import ErrorBox from "../components/ErrorBox.jsx";
import Card from "../components/Card.jsx";
import { haptic } from "../theme.js";
import KPIPlate from "../components/kpi/KPIPlate.jsx";
import AIBriefingCard from "../components/ai/AIBriefingCard.jsx";

const STATE_LABELS = {
  STOP_SENT: "Стоп",
  WARNING_SENT: "Предупреждение",
  CLAIMED: "Ожидает OFF",
};

function incidentVariant(state) {
  if (state === "STOP_SENT" || state === "CLAIMED") return "stop";
  if (state === "WARNING_SENT") return "warning";
  return "default";
}

const DISABLE_STATUS_LABELS = {
  PENDING: "В очереди",
  RUNNING: "Выполняется",
  RETRYING: "Повтор",
  FAILED: "Ошибка",
  SUCCEEDED: "Готово",
  CANCELLED: "Отменена",
};

const ACTIVE_DISABLE_STATUSES = new Set(["PENDING", "RUNNING", "RETRYING", "FAILED"]);
const FAILED_DISABLE_TTL_MS = 24 * 60 * 60 * 1000;

function isRelevantDisableTask(task) {
  if (task.status !== "FAILED") return true;
  const ts = task.updated_at || task.created_at;
  if (!ts) return true;
  return Date.now() - new Date(ts).getTime() < FAILED_DISABLE_TTL_MS;
}

function disableStatusVariant(status) {
  if (status === "FAILED") return "stop";
  if (status === "RETRYING") return "warning";
  if (status === "RUNNING") return "default";
  return "default";
}

// Хелперы для вызова нативного Telegram диалога
function tgConfirm(msg, cb) {
  const tg = window.Telegram?.WebApp;
  if (tg?.showConfirm) {
    tg.showConfirm(msg, cb);
  } else {
    cb(window.confirm(msg));
  }
}

function tgAlert(msg) {
  const tg = window.Telegram?.WebApp;
  if (tg?.showAlert) {
    tg.showAlert(msg);
  } else {
    alert(msg);
  }
}

// Страница дашборда — KPI, AI брифинг и управление сканированием
export default function DashboardPage() {
  const navigate = useNavigate();
  const [stats, setStats] = useState(null);
  const [incidents, setIncidents] = useState([]);
  const [disableTasks, setDisableTasks] = useState([]);
  const [obsSettings, setObsSettings] = useState(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState(null);
  const [toggling, setToggling] = useState(false);
  const [scanRequesting, setScanRequesting] = useState(false);
  const [toast, setToast] = useState(null);

  const loadData = () => {
    setLoading(true);
    Promise.all([
      fetchJson("/dashboard/stats"),
      fetchJson("/settings/observer"),
      getDashboardIncidents({ limit: 5 }).catch(() => []),
      getDisableTasks({ limit: 20 }).catch(() => []),
    ])
      .then(([statsData, obsData, incidentsData, disableData]) => {
        setStats(statsData);
        setObsSettings(obsData ?? null);
        setIncidents(Array.isArray(incidentsData) ? incidentsData : []);
        setDisableTasks(Array.isArray(disableData) ? disableData : []);
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

  // Прямое отключение объявления с подтверждением
  const handleDirectDisable = (fbAdId, adName) => {
    haptic.impact("medium");
    tgConfirm(`Вы уверены, что хотите отключить объявление "${adName || fbAdId}"?`, async (confirmed) => {
      if (!confirmed) return;
      try {
        haptic.impact("heavy");
        await disableAd(fbAdId, "Ручное отключение из Mini App");
        haptic.notify("success");
        tgAlert(`Задача на отключение объявления "${adName || fbAdId}" успешно отправлена.`);
        loadData();
      } catch (err) {
        haptic.notify("error");
        tgAlert(`Ошибка: ${err.message}`);
      }
    });
  };

  // Принудительный скан: ставит флаг scan_requested, observer подхватит на ближайшем тике.
  const triggerScan = async () => {
    setScanRequesting(true);
    haptic.impact("medium");
    try {
      await fetchJson("/settings/observer/scan-now", { method: "POST" });
      haptic.notify("success");
      setToast({ type: "ok", text: "Сканирование запущено" });
      setTimeout(() => loadData(), 3000);
    } catch (err) {
      haptic.notify("error");
      setToast({ type: "error", text: err.message });
    } finally {
      setScanRequesting(false);
      setTimeout(() => setToast(null), 3000);
    }
  };

  if (loading) return <Loader />;
  if (error) return <ErrorBox message={error} onRetry={loadData} />;

  const activeDisableTasks = disableTasks
    .filter((t) => ACTIVE_DISABLE_STATUSES.has(t.status) && isRelevantDisableTask(t))
    .slice(0, 8);

  return (
    <div style={{ paddingBottom: "24px" }}>
      <h1>Дашборд</h1>

      {/* Глобальный AI брифинг */}
      <AIBriefingCard />

      {/* KPI-плитки — сетка 2х2 */}
      <div style={{ display: "grid", gridTemplateColumns: "1fr 1fr", gap: "10px", marginBottom: "14px" }}>
        <KPIPlate title="Активно" value={stats?.active_ads_count} status="info" />
        <KPIPlate title="Стоп-сигналы" value={stats?.ads_in_stop} status={(stats?.ads_in_stop ?? 0) > 0 ? "stop" : "default"} />
        <KPIPlate title="Отключено сегодня" value={stats?.ads_disabled_today} status="ok" />
        <KPIPlate title="Предупреждения" value={stats?.ads_in_warning} status={(stats?.ads_in_warning ?? 0) > 0 ? "warn" : "default"} />
      </div>

      <Card title="Активные сигналы" style={{ marginTop: 6 }}>
        {incidents.length === 0 ? (
          <p className="hint">Нет активных сигналов</p>
        ) : (
          <div className="incident-list">
            {incidents.map((inc, index) => {
              const variant = incidentVariant(inc.current_state);
              const reason =
                inc.reason_title ||
                (inc.matched_rule_codes?.length ? inc.matched_rule_codes.join(", ") : null) ||
                STATE_LABELS[inc.current_state] ||
                inc.current_state;
              return (
                <div
                  key={inc.incident_key || inc.fb_ad_id}
                  className={`incident-row incident-row-${variant}`}
                  style={{ 
                    animationDelay: `${index * 50}ms`,
                    display: "flex",
                    flexDirection: "column",
                    alignItems: "stretch",
                    cursor: "default"
                  }}
                >
                  <div 
                    onClick={() => {
                      haptic.selection();
                      navigate(`/ads/${encodeURIComponent(inc.fb_ad_id)}`);
                    }}
                    style={{ cursor: "pointer", display: "flex", justifyContent: "space-between", alignItems: "flex-start", gap: "8px" }}
                  >
                    <div className="incident-row-main">
                      <div className="incident-ad-name" style={{ fontSize: "14px", fontWeight: "600" }}>{inc.ad_name || inc.fb_ad_id}</div>
                      <div className="hint incident-reason" style={{ marginTop: "2px" }}>{reason}</div>
                    </div>
                    <span className={`badge badge-${variant === "stop" ? "stop" : variant === "warning" ? "warning" : "normal"}`}>
                      {STATE_LABELS[inc.current_state] ?? inc.current_state}
                    </span>
                  </div>

                  {/* Кнопка быстрого отключения нативной конфирмацией */}
                  {inc.current_state !== "DISABLED" && (
                    <button
                      className="btn btn-danger btn-sm"
                      style={{ 
                        marginTop: "10px", 
                        alignSelf: "flex-end", 
                        padding: "6px 12px", 
                        fontSize: "12px", 
                        minHeight: "32px",
                        margin: 0,
                        width: "auto"
                      }}
                      onClick={(e) => {
                        e.stopPropagation();
                        handleDirectDisable(inc.fb_ad_id, inc.ad_name);
                      }}
                    >
                      ⛔ Отключить
                    </button>
                  )}
                </div>
              );
            })}
          </div>
        )}
      </Card>

      <Card title="Очередь отключений" style={{ marginTop: 6 }}>
        {activeDisableTasks.length === 0 ? (
          <p className="hint">Очередь пуста</p>
        ) : (
          <div className="disable-task-list">
            {activeDisableTasks.map((task) => {
              const variant = disableStatusVariant(task.status);
              const statusLabel = DISABLE_STATUS_LABELS[task.status] ?? task.status;
              return (
                <div key={task.id} className={`disable-task-row disable-task-row-${variant}`}>
                  <div className="disable-task-main">
                    <div className="disable-task-name">{task.ad_name || task.fb_ad_id}</div>
                    <div className="hint disable-task-meta">
                      {statusLabel}
                      {task.attempt_count > 1 ? ` · попытка ${task.attempt_count}` : ""}
                      {task.last_error ? ` · ${task.last_error}` : ""}
                    </div>
                  </div>
                  <span
                    className={`badge badge-${variant === "stop" ? "stop" : variant === "warning" ? "warning" : "normal"}`}
                  >
                    {statusLabel}
                  </span>
                </div>
              );
            })}
          </div>
        )}
        <p className="hint" style={{ marginTop: 8 }}>
          Только просмотр. Управление — в веб-версии.
        </p>
      </Card>

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
        Обновить данные
      </button>
      <button
        className="btn"
        onClick={triggerScan}
        disabled={scanRequesting}
        style={{ marginTop: 8, marginLeft: 8 }}
      >
        {scanRequesting ? "Запрос..." : "⚡ Сканировать сейчас"}
      </button>
      {toast && (
        <p className={toast.type === "error" ? "status-error" : "status-ok"} style={{ marginTop: 8 }}>
          {toast.text}
        </p>
      )}
    </div>
  );
}
