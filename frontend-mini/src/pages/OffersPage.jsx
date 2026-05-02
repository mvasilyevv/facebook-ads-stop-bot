import React, { useEffect, useState, useCallback } from "react";
import { fetchJson } from "../api.js";

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

// Модальный bottom sheet для добавления/редактирования оффера
function OfferModal({ offer, onSave, onClose }) {
  const [form, setForm] = useState({
    code: offer?.code ?? "",
    cpa_amount: offer?.cpa_amount ?? offer?.cpa ?? "",
    country_name: offer?.country_name ?? "",
    is_active: offer?.is_active ?? true,
  });
  const [saving, setSaving] = useState(false);

  const handleSubmit = async (e) => {
    e.preventDefault();
    setSaving(true);
    try {
      await onSave({
        code: form.code.toUpperCase().trim(),
        cpa_amount: parseFloat(form.cpa_amount) || 0,
        country_name: form.country_name.trim() || null,
        is_active: form.is_active,
      });
    } finally {
      setSaving(false);
    }
  };

  return (
    <div className="modal-overlay" onClick={onClose}>
      <div className="modal-sheet" onClick={(e) => e.stopPropagation()}>
        <h2>{offer ? "Редактировать оффер" : "Новый оффер"}</h2>
        <form onSubmit={handleSubmit}>
          <div className="form-group">
            <label className="form-label">Код оффера</label>
            <input
              className="form-input"
              type="text"
              placeholder="OFFER_AU_42"
              value={form.code}
              onChange={(e) => setForm({ ...form, code: e.target.value })}
              required
              disabled={!!offer}
            />
          </div>
          <div className="form-group">
            <label className="form-label">CPA ($)</label>
            <input
              className="form-input"
              type="number"
              step="0.01"
              min="0"
              placeholder="5.00"
              value={form.cpa_amount}
              onChange={(e) => setForm({ ...form, cpa_amount: e.target.value })}
              required
            />
          </div>
          <div className="form-group">
            <label className="form-label">Страна</label>
            <input
              className="form-input"
              type="text"
              placeholder="Демократическая Республика Конго"
              value={form.country_name}
              onChange={(e) => setForm({ ...form, country_name: e.target.value })}
            />
          </div>
          <div className="toggle-row">
            <div>
              <div className="toggle-label">Активен</div>
            </div>
            <label className="toggle-switch">
              <input
                type="checkbox"
                checked={form.is_active}
                onChange={(e) => setForm({ ...form, is_active: e.target.checked })}
              />
              <div className="toggle-track">
                <div
                  className="toggle-knob"
                  style={{ transform: form.is_active ? "translateX(20px)" : "translateX(0)" }}
                />
              </div>
            </label>
          </div>
          <div style={{ display: "flex", gap: 8, marginTop: 16 }}>
            <button type="button" className="btn btn-secondary" onClick={onClose}>
              Отмена
            </button>
            <button type="submit" className="btn" disabled={saving}>
              {saving ? "Сохранение..." : offer ? "Сохранить" : "Создать"}
            </button>
          </div>
        </form>
      </div>
    </div>
  );
}

// Страница офферов — карточный mobile layout
export default function OffersPage() {
  const [offers, setOffers] = useState([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState(null);
  const [showModal, setShowModal] = useState(false);
  const [editOffer, setEditOffer] = useState(null);
  const [toast, setToast] = useState(null);

  const load = useCallback(async () => {
    try {
      setError(null);
      const data = await fetchJson("/offers");
      setOffers(Array.isArray(data) ? data : []);
    } catch (err) {
      setError(err.message);
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    load();
  }, [load]);

  const handleSave = async (data) => {
    try {
      if (editOffer) {
        await fetchJson(`/offers/${editOffer.id}`, {
          method: "PUT",
          body: JSON.stringify(data),
        });
        setToast({ type: "ok", text: "Оффер обновлён" });
      } else {
        await fetchJson("/offers", {
          method: "POST",
          body: JSON.stringify(data),
        });
        setToast({ type: "ok", text: "Оффер создан" });
      }
      setShowModal(false);
      setEditOffer(null);
      load();
    } catch (err) {
      setToast({ type: "error", text: err.message });
    }
  };

  const handleDelete = async (offer) => {
    if (!window.confirm(`Удалить оффер "${offer.code}"?`)) return;
    try {
      await fetchJson(`/offers/${offer.id}`, { method: "DELETE" });
      setToast({ type: "ok", text: "Оффер удалён" });
      load();
    } catch (err) {
      setToast({ type: "error", text: err.message });
    }
  };

  const handleToggleActive = async (offer) => {
    try {
      await fetchJson(`/offers/${offer.id}`, {
        method: "PUT",
        body: JSON.stringify({ ...offer, is_active: !offer.is_active }),
      });
      setOffers((prev) =>
        prev.map((o) => (o.id === offer.id ? { ...o, is_active: !o.is_active } : o))
      );
    } catch (err) {
      setToast({ type: "error", text: err.message });
    }
  };

  if (loading) return <div className="loading">Загрузка офферов...</div>;

  return (
    <div>
      <div style={{ display: "flex", alignItems: "center", justifyContent: "space-between", marginBottom: 14 }}>
        <h1 style={{ marginBottom: 0 }}>Офферы</h1>
        <button
          className="btn btn-secondary btn-sm"
          onClick={() => {
            setEditOffer(null);
            setShowModal(true);
          }}
        >
          + Добавить
        </button>
      </div>

      {error && (
        <div className="card" style={{ borderLeft: "3px solid var(--color-danger)", marginBottom: 10 }}>
          <span className="status-error">{error}</span>
          <button className="btn btn-secondary btn-sm" style={{ marginTop: 8 }} onClick={load}>
            Повторить
          </button>
        </div>
      )}

      {offers.length === 0 && !loading && (
        <div className="card" style={{ textAlign: "center", padding: "32px 16px" }}>
          <p style={{ fontSize: 32, marginBottom: 8 }}>○</p>
          <p style={{ fontSize: 14, fontWeight: 500 }}>Нет офферов</p>
          <p className="hint">Создайте первый оффер</p>
        </div>
      )}

      <div className="card" style={{ padding: "8px 14px" }}>
        {offers.map((o) => (
          <div key={o.id} className="offer-row">
            <div style={{ flex: 1, minWidth: 0 }}>
              <div className="offer-code">{o.code}</div>
              <div className="offer-meta">
                <span style={{ fontWeight: 600 }}>${Number(o.cpa_amount ?? o.cpa).toFixed(2)}</span>
                {o.country_name && <span> · {o.country_name}</span>}
              </div>
            </div>
            <div style={{ display: "flex", alignItems: "center", gap: 6 }}>
              <span
                className={o.is_active ? "badge badge-active" : "badge badge-disabled"}
                style={{ cursor: "pointer" }}
                onClick={() => handleToggleActive(o)}
              >
                {o.is_active ? "Активен" : "Выкл."}
              </span>
              <button
                style={{
                  background: "none",
                  border: "none",
                  padding: "4px 6px",
                  cursor: "pointer",
                  color: "var(--tg-hint)",
                  fontSize: 16,
                }}
                onClick={() => {
                  setEditOffer(o);
                  setShowModal(true);
                }}
              >
                ✎
              </button>
              <button
                style={{
                  background: "none",
                  border: "none",
                  padding: "4px 6px",
                  cursor: "pointer",
                  color: "var(--color-danger)",
                  fontSize: 16,
                }}
                onClick={() => handleDelete(o)}
              >
                ✕
              </button>
            </div>
          </div>
        ))}
      </div>

      {showModal && (
        <OfferModal
          offer={editOffer}
          onSave={handleSave}
          onClose={() => {
            setShowModal(false);
            setEditOffer(null);
          }}
        />
      )}

      {toast && (
        <Toast message={toast.text} type={toast.type} onClose={() => setToast(null)} />
      )}
    </div>
  );
}
