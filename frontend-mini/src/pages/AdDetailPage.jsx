import React, { useEffect, useState, useCallback } from "react";
import { useParams } from "react-router-dom";
import { getAdDetail, disableAd, snoozeAd, claimAd } from "../api.js";
import Loader from "../components/Loader.jsx";
import ErrorBox from "../components/ErrorBox.jsx";
import Card from "../components/Card.jsx";
import { haptic } from "../theme.js";

const STATE_LABELS = {
  NORMAL: "Норма",
  WARNING_SENT: "Предупреждение",
  STOP_SENT: "Стоп",
  CLAIMED: "Ожидает OFF",
  DISABLED: "Отключено",
  ARCHIVED: "Архив",
};

function stateBadgeClass(state) {
  if (state === "STOP_SENT" || state === "CLAIMED") return "badge badge-stop";
  if (state === "WARNING_SENT") return "badge badge-warning";
  if (state === "DISABLED" || state === "ARCHIVED") return "badge badge-disabled";
  return "badge badge-normal";
}

function fmt$(v) {
  if (v == null || v === "") return "—";
  return "$" + Number(v).toFixed(2);
}

function fmtN(v) {
  if (v == null || v === "") return "—";
  return String(Number(v));
}

function fmtPct(v) {
  if (v == null || v === "") return "—";
  return Number(v).toFixed(2) + "%";
}

function fmtTime(isoStr) {
  if (!isoStr) return "—";
  const d = new Date(isoStr);
  return d.toLocaleTimeString("ru-RU", { hour: "2-digit", minute: "2-digit" });
}

function fmtDateTime(isoStr) {
  if (!isoStr) return "—";
  const d = new Date(isoStr);
  return d.toLocaleString("ru-RU", {
    day: "2-digit", month: "2-digit",
    hour: "2-digit", minute: "2-digit",
  });
}

function isFuture(isoStr) {
  if (!isoStr) return false;
  return new Date(isoStr).getTime() > Date.now();
}

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

function tgOpenLink(url) {
  const tg = window.Telegram?.WebApp;
  if (tg?.openLink) {
    tg.openLink(url);
  } else {
    window.open(url, "_blank");
  }
}

// Страница детали объявления — открывается из Telegram WebApp-кнопки
export default function AdDetailPage() {
  const { fbAdId } = useParams();
  const [data, setData] = useState(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState(null);
  const [busy, setBusy] = useState(false);

  const loadDetail = useCallback(async () => {
    try {
      setError(null);
      const result = await getAdDetail(fbAdId);
      setData(result);
    } catch (err) {
      setError(err.message);
    } finally {
      setLoading(false);
    }
  }, [fbAdId]);

  useEffect(() => {
    loadDetail();
  }, [loadDetail]);

  async function handleAction(actionFn, successMsg) {
    setBusy(true);
    try {
      await actionFn();
      tgAlert(successMsg);
      await loadDetail();
    } catch (err) {
      tgAlert(err.message);
    } finally {
      setBusy(false);
    }
  }

  function handleDisable() {
    haptic.impact();
    tgConfirm("Отключить объявление?", (confirmed) => {
      if (!confirmed) return;
      handleAction(() => disableAd(fbAdId), "Задача поставлена");
    });
  }

  function handleSnooze(minutes) {
    haptic.selection();
    handleAction(
      () => snoozeAd(fbAdId, minutes),
      `Снуз на ${minutes} мин установлен`
    );
  }

  function handleClaim() {
    haptic.selection();
    handleAction(() => claimAd(fbAdId), "Алерт снят");
  }

  function handleOpenAdsManager() {
    haptic.selection();
    const url = `https://adsmanager.facebook.com/adsmanager/manage/ads?act=${data.account_id}&selected_ad_ids=${fbAdId}`;
    tgOpenLink(url);
  }

  if (loading) return <Loader text="Загрузка объявления..." />;
  if (error) return <ErrorBox message={error} onRetry={loadDetail} />;
  if (!data) return null;

  const { ad_name, campaign_name, adset_name, state, snooze_until, metrics = {}, recent_alerts = [], can_open_in_ads_manager } = data;

  return (
    <div style={{ paddingBottom: 24 }}>
      {/* Шапка */}
      <div style={{ marginBottom: 12 }}>
        {campaign_name && (
          <div className="hint" style={{ marginBottom: 2, fontSize: 12 }}>📁 {campaign_name}</div>
        )}
        {adset_name && (
          <div className="hint" style={{ marginBottom: 4, fontSize: 12 }}>🎯 {adset_name}</div>
        )}
        <div style={{ fontWeight: 700, fontSize: 16, lineHeight: 1.3, marginBottom: 4 }}>
          {ad_name || fbAdId}
        </div>
        <div style={{ display: "flex", alignItems: "center", gap: 8, flexWrap: "wrap" }}>
          <span className={stateBadgeClass(state)}>{STATE_LABELS[state] ?? state}</span>
          <span style={{ fontFamily: "monospace", fontSize: 11, color: "var(--tg-hint-color)" }}>
            {fbAdId}
          </span>
        </div>
      </div>

      {/* Снуз */}
      {isFuture(snooze_until) && (
        <Card style={{ marginBottom: 12, background: "rgba(255,214,10,0.08)" }}>
          <div style={{ display: "flex", alignItems: "center", gap: 6 }}>
            <span>😴</span>
            <span>Снуз до {fmtTime(snooze_until)}</span>
          </div>
        </Card>
      )}

      {/* Метрики */}
      <Card title="Метрики" style={{ marginBottom: 12 }}>
        <div className="ad-metrics">
          <div className="metric-chip">
            <div className="label">Расход</div>
            <div className="value">{fmt$(metrics.spend)}</div>
          </div>
          <div className="metric-chip">
            <div className="label">Лиды</div>
            <div className="value">{fmtN(metrics.leads)}</div>
          </div>
          <div className="metric-chip">
            <div className="label">Деп</div>
            <div className="value">{fmtN(metrics.deposits)}</div>
          </div>
          <div className="metric-chip">
            <div className="label">CPC</div>
            <div className="value">{fmt$(metrics.cpc)}</div>
          </div>
          <div className="metric-chip">
            <div className="label">CTR</div>
            <div className="value">{fmtPct(metrics.ctr)}</div>
          </div>
          {metrics.holds != null && (
            <div className="metric-chip">
              <div className="label">Холды</div>
              <div className="value">{fmtN(metrics.holds)}</div>
            </div>
          )}
        </div>
      </Card>

      {/* История алертов */}
      {recent_alerts.length > 0 && (
        <Card title="История алертов" style={{ marginBottom: 12 }}>
          {recent_alerts.slice(0, 10).map((al, i) => (
            <div
              key={i}
              style={{
                display: "flex",
                alignItems: "flex-start",
                gap: 8,
                padding: "6px 0",
                borderBottom: i < recent_alerts.length - 1 ? "1px solid rgba(255,255,255,0.06)" : "none",
              }}
            >
              <span
                className={al.stage === "STOP" ? "badge badge-stop" : "badge badge-warning"}
                style={{ flexShrink: 0, fontSize: 10 }}
              >
                {al.stage}
              </span>
              <div style={{ flex: 1, minWidth: 0 }}>
                <div style={{ fontSize: 12, color: "var(--tg-hint-color)" }}>
                  {fmtDateTime(al.created_at)}
                </div>
                {al.reason_title && (
                  <div style={{ fontSize: 13, marginTop: 1 }}>{al.reason_title}</div>
                )}
              </div>
            </div>
          ))}
        </Card>
      )}

      {/* Действия */}
      <Card title="Действия">
        <button
          className="btn btn-danger"
          style={{ width: "100%", marginBottom: 8 }}
          disabled={busy}
          onClick={handleDisable}
        >
          ⛔ Отключить
        </button>

        <div style={{ display: "flex", gap: 8, marginBottom: 8 }}>
          {[30, 60, 120].map((min) => (
            <button
              key={min}
              className="btn btn-secondary"
              style={{ flex: 1 }}
              disabled={busy}
              onClick={() => handleSnooze(min)}
            >
              Снуз {min}
            </button>
          ))}
        </div>

        <button
          className="btn btn-secondary"
          style={{ width: "100%", marginBottom: can_open_in_ads_manager ? 8 : 0 }}
          disabled={busy}
          onClick={handleClaim}
        >
          ✅ Снять алерт
        </button>

        {can_open_in_ads_manager && (
          <button
            className="btn btn-secondary"
            style={{ width: "100%" }}
            disabled={busy}
            onClick={handleOpenAdsManager}
          >
            Открыть в Ads Manager ↗
          </button>
        )}
      </Card>
    </div>
  );
}
