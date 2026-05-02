import React, { useEffect, useState, useCallback } from "react";
import { fetchJson } from "../api.js";
import { getStoredRole } from "../auth.js";

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
    scan_interval_seconds: data?.scan_interval_seconds ?? 60,
    is_scanning_enabled: data?.is_scanning_enabled ?? true,
    auto_enable_recommendations: data?.auto_enable_recommendations ?? false,
  });

  // Синхронизируем при изменении data извне
  useEffect(() => {
    if (data) {
      setForm({
        scan_interval_seconds: data.scan_interval_seconds ?? 60,
        is_scanning_enabled: data.is_scanning_enabled ?? true,
        auto_enable_recommendations: data.auto_enable_recommendations ?? false,
      });
    }
  }, [data]);

  const handleSubmit = (e) => {
    e.preventDefault();
    onSave("observer", { ...form, scan_interval_seconds: Number(form.scan_interval_seconds) });
  };

  return (
    <div className="card">
      <div className="card-title">Observer</div>
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
        <div className="form-group" style={{ marginTop: 12 }}>
          <label className="form-label">Интервал скана (сек)</label>
          <input
            className="form-input"
            type="number"
            min="10"
            max="600"
            value={form.scan_interval_seconds}
            onChange={(e) => setForm({ ...form, scan_interval_seconds: e.target.value })}
          />
        </div>
        <button type="submit" className="btn" disabled={saving}>
          {saving ? "Сохранение..." : "Сохранить"}
        </button>
      </form>
    </div>
  );
}

// Секция настроек Telegram
function TelegramSection({ data, onSave, saving }) {
  const [token, setToken] = useState("");

  const handleSave = (e) => {
    e.preventDefault();
    if (!token.trim()) return;
    onSave("telegram-token", { bot_token: token.trim() });
    setToken("");
  };

  const handleRevoke = () => {
    if (window.confirm("Отозвать токен Telegram-бота?")) {
      onSave("telegram-revoke", null);
    }
  };

  return (
    <div className="card">
      <div className="card-title">Telegram</div>
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
    </div>
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
    onSave("vision", form);
  };

  return (
    <div className="card">
      <div className="card-title">Vision Browser</div>
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
    </div>
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
        await fetchJson("/settings/observer", { method: "PUT", body: JSON.stringify(payload) });
        setObserverData((prev) => ({ ...prev, ...payload }));
        setToast({ type: "ok", text: "Observer сохранён" });
      } else if (section === "telegram-token") {
        await fetchJson("/settings/telegram/token", { method: "PUT", body: JSON.stringify(payload) });
        setToast({ type: "ok", text: "Токен обновлён" });
        await fetchJson("/settings/telegram").then(setTelegramData).catch(() => {});
      } else if (section === "telegram-revoke") {
        await fetchJson("/settings/telegram", { method: "DELETE" });
        setToast({ type: "ok", text: "Токен отозван" });
        setTelegramData((prev) => ({ ...prev, is_authorized: false }));
      } else if (section === "vision") {
        await fetchJson("/settings/vision", { method: "PUT", body: JSON.stringify(payload) });
        setVisionData((prev) => ({ ...prev, ...payload }));
        setToast({ type: "ok", text: "Vision сохранён" });
      }
    } catch (err) {
      setToast({ type: "error", text: err.message });
    } finally {
      setSaving(false);
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

  return (
    <div>
      <h1>Настройки</h1>

      {/* Роль */}
      <div className="card">
        <div className="card-title">Роль</div>
        <p>{role === "owner" ? "Владелец" : "Получатель"}</p>
      </div>

      <ObserverSection data={observerData} onSave={handleSave} saving={saving} />
      <TelegramSection data={telegramData} onSave={handleSave} saving={saving} />
      <VisionSection data={visionData} onSave={handleSave} saving={saving} />

      {/* Ссылка на полный интерфейс */}
      <div className="card">
        <div className="card-title">Полные настройки</div>
        <p className="hint" style={{ marginBottom: 8 }}>
          Детальные настройки правил, браузера и нейминга — в веб-версии.
        </p>
        <a href="/settings" className="btn btn-secondary" style={{ textAlign: "center" }}>
          Открыть в браузере
        </a>
      </div>

      {toast && (
        <Toast message={toast.text} type={toast.type} onClose={() => setToast(null)} />
      )}
    </div>
  );
}
