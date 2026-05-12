import React, { useEffect, useState, useCallback } from "react";
import { fetchJson, setTelegramWebAppUrl } from "../api.js";
import { getStoredRole } from "../auth.js";
import Loader from "../components/Loader.jsx";
import ErrorBox from "../components/ErrorBox.jsx";
import Card from "../components/Card.jsx";
import { haptic } from "../theme.js";

// Тост-уведомление
function Toast({ message, type, onClose }) {
  useEffect(() => {
    const t = setTimeout(onClose, 2500);
    return () => clearTimeout(t);
  }, [onClose]);
  return (
    <div className={`toast ${type === "error" ? "toast-error" : "toast-success"}`}>
      {message}
    </div>
  );
}

// iOS-style toggle
function Toggle({ checked, onChange, disabled }) {
  return (
    <label className="toggle-switch">
      <input type="checkbox" checked={checked} onChange={(e) => onChange(e.target.checked)} disabled={disabled} />
      <div className="toggle-track">
        <div className="toggle-knob" style={{ transform: checked ? "translateX(20px)" : "translateX(0)" }} />
      </div>
    </label>
  );
}

// Секция настроек observer
function ObserverSection({ data, onSave, saving }) {
  const [form, setForm] = useState({
    is_scanning_enabled: data?.is_scanning_enabled ?? true,
    auto_enable_recommendations: data?.auto_enable_recommendations ?? false,
  });

  useEffect(() => {
    if (data) {
      setForm({
        is_scanning_enabled: data.is_scanning_enabled ?? true,
        auto_enable_recommendations: data.auto_enable_recommendations ?? false,
      });
    }
  }, [data]);

  const handleSubmit = (e) => {
    e.preventDefault();
    haptic.impact("medium");
    onSave("observer", { ...form });
  };

  return (
    <Card title="Observer">
      <form onSubmit={handleSubmit}>
        <div className="toggle-row">
          <div>
            <div className="toggle-label">Сканирование</div>
            <div className="toggle-sub">Включить/выключить мониторинг</div>
          </div>
          <Toggle
            checked={form.is_scanning_enabled}
            onChange={(v) => setForm({ ...form, is_scanning_enabled: v })}
          />
        </div>
        <div className="toggle-row">
          <div>
            <div className="toggle-label">Авто-включение</div>
            <div className="toggle-sub">Рекомендации включения</div>
          </div>
          <Toggle
            checked={form.auto_enable_recommendations}
            onChange={(v) => setForm({ ...form, auto_enable_recommendations: v })}
          />
        </div>
        <p className="hint" style={{ marginTop: 10, marginBottom: 10 }}>
          Интервал скана управляется автоматически (адаптивный). Ручная настройка — в веб-версии.
        </p>
        <button type="submit" className="btn" disabled={saving}>
          {saving ? "Сохранение..." : "Сохранить"}
        </button>
      </form>
    </Card>
  );
}

// Секция настроек Telegram
function TelegramSection({ data, onSave, saving }) {
  const [token, setToken] = useState("");
  const [webAppUrl, setWebAppUrl] = useState(data?.web_app_url || "");

  useEffect(() => {
    if (data?.web_app_url !== undefined) {
      setWebAppUrl(data.web_app_url || "");
    }
  }, [data?.web_app_url]);

  const handleSave = (e) => {
    e.preventDefault();
    if (!token.trim()) return;
    haptic.impact("medium");
    onSave("telegram-token", { bot_token: token.trim() });
    setToken("");
  };

  const handleRevoke = () => {
    if (window.confirm("Отозвать токен Telegram-бота?")) {
      haptic.impact("heavy");
      onSave("telegram-revoke", null);
    }
  };

  const handleSaveWebAppUrl = () => {
    haptic.impact("medium");
    onSave("telegram-webapp", { web_app_url: webAppUrl });
  };

  return (
    <Card title="Telegram">
      <div style={{ marginBottom: 10 }}>
        <span className="hint">Статус поллера: </span>
        <span className={data?.poller_status === "ONLINE" ? "status-ok" : "status-warn"}>
          {data?.poller_status ?? "—"}
        </span>
      </div>
      <div style={{ marginBottom: 10 }}>
        <span className="hint">Авторизован: </span>
        <span>{data?.is_authorized ? "✓ да" : "✗ нет"}</span>
      </div>
      <form onSubmit={handleSave}>
        <div className="form-group">
          <label className="form-label">Новый Bot Token</label>
          <input
            className="form-input"
            type="text"
            placeholder="1234567890:AABBcc..."
            value={token}
            onChange={(e) => setToken(e.target.value)}
          />
        </div>
        <button type="submit" className="btn" disabled={saving || !token.trim()}>
          Обновить токен
        </button>
      </form>
      {data?.is_authorized && (
        <button className="btn btn-danger" style={{ marginTop: 8 }} onClick={handleRevoke} disabled={saving}>
          Отозвать токен
        </button>
      )}
      <div className="form-group" style={{ marginTop: 16 }}>
        <label className="form-label">Web App URL (Mini App)</label>
        <input
          className="form-input"
          type="url"
          value={webAppUrl}
          onChange={(e) => setWebAppUrl(e.target.value)}
          placeholder="https://app.example.com/tma/"
        />
        <p className="hint" style={{ marginTop: 4 }}>HTTPS-URL Mini App. Пусто = использовать значение из .env.</p>
        <button className="btn" style={{ marginTop: 8 }} onClick={handleSaveWebAppUrl} disabled={saving}>
          {saving ? "Сохранение..." : "Сохранить"}
        </button>
      </div>
    </Card>
  );
}

// Секция настроек Vision
function VisionSection({ data, onSave, saving }) {
  const [form, setForm] = useState({
    profile_id: data?.profile_id ?? "",
    auto_restart_on_missing_cdp: data?.auto_restart_on_missing_cdp ?? true,
  });

  useEffect(() => {
    if (data) {
      setForm({
        profile_id: data.profile_id ?? "",
        auto_restart_on_missing_cdp: data.auto_restart_on_missing_cdp ?? true,
      });
    }
  }, [data]);

  const handleSubmit = (e) => {
    e.preventDefault();
    haptic.impact("medium");
    onSave("vision", form);
  };

  return (
    <Card title="Vision Browser">
      <div style={{ marginBottom: 10 }}>
        <span className="hint">Статус: </span>
        <span className={data?.cdp_ready ? "status-ok" : "status-warn"}>
          {data?.runtime_status ?? "—"}
        </span>
      </div>
      <form onSubmit={handleSubmit}>
        <div className="form-group">
          <label className="form-label">Profile ID</label>
          <input
            className="form-input"
            type="text"
            placeholder="profile-uuid"
            value={form.profile_id}
            onChange={(e) => setForm({ ...form, profile_id: e.target.value })}
          />
        </div>
        <div className="toggle-row">
          <div>
            <div className="toggle-label">Авто-рестарт CDP</div>
            <div className="toggle-sub">Перезапуск при потере соединения</div>
          </div>
          <Toggle
            checked={form.auto_restart_on_missing_cdp}
            onChange={(v) => setForm({ ...form, auto_restart_on_missing_cdp: v })}
          />
        </div>
        <button type="submit" className="btn" disabled={saving}>
          {saving ? "Сохранение..." : "Сохранить"}
        </button>
      </form>
    </Card>
  );
}

// Полноценный экран настроек
export default function SettingsPage() {
  const role = getStoredRole();
  const [observerData, setObserverData] = useState(null);
  const [telegramData, setTelegramData] = useState(null);
  const [visionData, setVisionData] = useState(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState(null);
  const [saving, setSaving] = useState(false);
  const [toast, setToast] = useState(null);

  const load = useCallback(async () => {
    setLoading(true);
    setError(null);
    try {
      const [obs, tg, vis] = await Promise.all([
        fetchJson("/settings/observer").catch(() => null),
        fetchJson("/settings/telegram").catch(() => null),
        fetchJson("/settings/vision").catch(() => null),
      ]);
      setObserverData(obs);
      setTelegramData(tg);
      setVisionData(vis);
    } catch (err) {
      setError(err.message);
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    load();
  }, [load]);

  const handleSave = async (section, payload) => {
    setSaving(true);
    try {
      if (section === "observer") {
        // Сохраняем сканирование через отдельный PATCH (не затирает auto_enable_recommendations).
        await fetchJson("/settings/observer/scanning", {
          method: "PATCH",
          body: JSON.stringify({ enabled: payload.is_scanning_enabled }),
        });
        // Авто-включение — отдельный PATCH, как в web-UI, чтобы не было рассинхрона.
        await fetchJson("/settings/observer/auto-enable", {
          method: "PATCH",
          body: JSON.stringify({ enabled: payload.auto_enable_recommendations }),
        });
        setObserverData((prev) => ({ ...prev, ...payload }));
        haptic.notify("success");
        setToast({ type: "ok", text: "Observer сохранён" });
      } else if (section === "telegram-token") {
        await fetchJson("/settings/telegram/token", { method: "PUT", body: JSON.stringify(payload) });
        haptic.notify("success");
        setToast({ type: "ok", text: "Токен обновлён" });
        await fetchJson("/settings/telegram").then(setTelegramData).catch(() => {});
      } else if (section === "telegram-revoke") {
        await fetchJson("/settings/telegram", { method: "DELETE" });
        haptic.notify("success");
        setToast({ type: "ok", text: "Токен отозван" });
        setTelegramData((prev) => ({ ...prev, is_authorized: false }));
      } else if (section === "telegram-webapp") {
        const result = await setTelegramWebAppUrl(payload.web_app_url);
        setTelegramData((prev) => ({ ...prev, web_app_url: result.web_app_url ?? payload.web_app_url }));
        haptic.notify("success");
        setToast({ type: "ok", text: "Web App URL сохранён" });
      } else if (section === "vision") {
        await fetchJson("/settings/vision", { method: "PUT", body: JSON.stringify(payload) });
        setVisionData((prev) => ({ ...prev, ...payload }));
        haptic.notify("success");
        setToast({ type: "ok", text: "Vision сохранён" });
      }
    } catch (err) {
      haptic.notify("error");
      setToast({ type: "error", text: err.message });
    } finally {
      setSaving(false);
    }
  };

  if (loading) return <Loader />;
  if (error) return <ErrorBox message={error} onRetry={load} />;

  return (
    <div>
      <h1>Настройки</h1>

      {/* Роль */}
      <Card title="Роль">
        <p>{role === "owner" ? "Владелец" : "Получатель"}</p>
      </Card>

      <ObserverSection data={observerData} onSave={handleSave} saving={saving} />
      <TelegramSection data={telegramData} onSave={handleSave} saving={saving} />
      <VisionSection data={visionData} onSave={handleSave} saving={saving} />

      {toast && (
        <Toast message={toast.text} type={toast.type} onClose={() => setToast(null)} />
      )}
    </div>
  );
}
