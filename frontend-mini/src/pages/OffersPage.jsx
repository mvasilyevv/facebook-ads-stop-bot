import React, { useEffect, useState, useCallback } from "react";
import { fetchJson, getOfferRules, updateOfferRules } from "../api.js";
import Loader from "../components/Loader.jsx";
import ErrorBox from "../components/ErrorBox.jsx";
import EmptyState from "../components/EmptyState.jsx";
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

// Модальный bottom sheet для добавления/редактирования оффера
function OfferModal({ offer, onSave, onClose }) {
  const [form, setForm] = useState({
    code: offer?.code ?? "",
    cpa_amount: offer?.cpa_amount ?? offer?.cpa ?? "",
    country_name: offer?.country_name ?? "",
    is_active: offer?.is_active ?? true,
  });
  const [rules, setRules] = useState(null);
  const [rulesLoading, setRulesLoading] = useState(!!offer);
  const [saving, setSaving] = useState(false);

  // Загружаем правила оффера при открытии режима редактирования
  useEffect(() => {
    let alive = true;
    if (!offer) {
      setRules(null);
      setRulesLoading(false);
      return;
    }
    setRulesLoading(true);
    (async () => {
      try {
        const data = await getOfferRules(offer.id);
        if (alive) setRules(data || {});
      } catch {
        if (alive) setRules({});
      } finally {
        if (alive) setRulesLoading(false);
      }
    })();
    return () => { alive = false; };
  }, [offer]);

  // Возвращает значение поля порога как строку
  const overrideValue = (key) => {
    const v = rules?.[key];
    return v === null || v === undefined || v === "" ? "" : String(v);
  };

  const setOverride = (key, value) => {
    setRules((prev) => ({ ...(prev || {}), [key]: value === "" ? null : value }));
  };

  const handleSubmit = async (e) => {
    e.preventDefault();
    setSaving(true);
    haptic.impact("medium");
    try {
      await onSave(
        {
          code: form.code.toUpperCase().trim(),
          cpa_amount: parseFloat(form.cpa_amount) || 0,
          country_name: form.country_name.trim() || null,
          is_active: form.is_active,
        },
        offer ? rules : null,
      );
    } finally {
      setSaving(false);
    }
  };

  // Хелпер: рендер пары инпутов warning/stop для одного шага (cpc/cpl/cpr)
  const ThresholdRow = ({ stepKey, label }) => {
    const warnKey = `${stepKey}_warning_percent_of_stop`;
    const stopKey = `${stepKey}_stop_percent_of_base`;
    return (
      <div style={{ display: "flex", gap: 8, marginBottom: 8 }}>
        <div style={{ flex: 1 }}>
          <label className="form-label" style={{ fontSize: 12 }}>{label} warning %</label>
          <input
            className="form-input"
            type="number"
            min="50"
            max="100"
            step="1"
            placeholder="80"
            value={overrideValue(warnKey)}
            onChange={(e) => setOverride(warnKey, e.target.value)}
          />
        </div>
        <div style={{ flex: 1 }}>
          <label className="form-label" style={{ fontSize: 12 }}>{label} stop %</label>
          <input
            className="form-input"
            type="number"
            min="1"
            max="100"
            step="1"
            placeholder="80"
            value={overrideValue(stopKey)}
            onChange={(e) => setOverride(stopKey, e.target.value)}
          />
        </div>
      </div>
    );
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

          {offer && (
            <div className="form-group" style={{ marginTop: 12 }}>
              <div className="form-label" style={{ fontWeight: 600, marginBottom: 6 }}>
                Пороги stop / warning (этого оффера)
              </div>
              <p className="hint" style={{ marginBottom: 8 }}>
                По умолчанию: warning 80%, stop 100%. Измените под нужды оффера.
              </p>
              {rulesLoading ? (
                <p className="hint">Загрузка правил...</p>
              ) : (
                <>
                  <ThresholdRow stepKey="cpc" label="CPC" />
                  <ThresholdRow stepKey="cpl" label="CPL" />
                  <ThresholdRow stepKey="cpr" label="CPR" />
                </>
              )}
            </div>
          )}

          <div style={{ display: "flex", gap: 8, marginTop: 16 }}>
            <button type="button" className="btn btn-secondary" onClick={onClose}>
              Отмена
            </button>
            <button type="submit" className="btn" disabled={saving || rulesLoading}>
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

  const handleSave = async (data, rulesOverride) => {
    try {
      let savedOfferId = editOffer?.id;
      if (editOffer) {
        await fetchJson(`/offers/${editOffer.id}`, {
          method: "PUT",
          body: JSON.stringify(data),
        });
        haptic.notify("success");
        setToast({ type: "ok", text: "Оффер обновлён" });
      } else {
        const created = await fetchJson("/offers", {
          method: "POST",
          body: JSON.stringify(data),
        });
        savedOfferId = created?.id;
        haptic.notify("success");
        setToast({ type: "ok", text: "Оффер создан" });
      }
      // Если редактируем — сохраняем правила (включая per-offer пороги)
      if (rulesOverride && savedOfferId) {
        try {
          await updateOfferRules(savedOfferId, rulesOverride);
        } catch (err) {
          setToast({ type: "error", text: `Правила: ${err.message}` });
        }
      }
      setShowModal(false);
      setEditOffer(null);
      load();
    } catch (err) {
      haptic.notify("error");
      setToast({ type: "error", text: err.message });
    }
  };

  const handleDelete = async (offer) => {
    if (!window.confirm(`Удалить оффер "${offer.code}"?`)) return;
    haptic.impact("medium");
    try {
      await fetchJson(`/offers/${offer.id}`, { method: "DELETE" });
      haptic.notify("success");
      setToast({ type: "ok", text: "Оффер удалён" });
      load();
    } catch (err) {
      haptic.notify("error");
      setToast({ type: "error", text: err.message });
    }
  };

  const handleToggleActive = async (offer) => {
    haptic.selection();
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

  if (loading) return <Loader text="Загрузка офферов..." />;

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

      {error && <ErrorBox message={error} onRetry={load} />}

      {offers.length === 0 && !loading && (
        <Card>
          <EmptyState icon="🎯" title="Нет офферов" subtitle="Создайте первый оффер" />
        </Card>
      )}

      {offers.length > 0 && (
        <Card style={{ padding: "8px 14px" }}>
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
                    padding: "8px",
                    cursor: "pointer",
                    color: "var(--tg-hint-color)",
                    fontSize: 16,
                    minWidth: 36,
                    minHeight: 44,
                    display: "flex",
                    alignItems: "center",
                    justifyContent: "center",
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
                    padding: "8px",
                    cursor: "pointer",
                    color: "var(--color-danger)",
                    fontSize: 16,
                    minWidth: 36,
                    minHeight: 44,
                    display: "flex",
                    alignItems: "center",
                    justifyContent: "center",
                  }}
                  onClick={() => handleDelete(o)}
                >
                  ✕
                </button>
              </div>
            </div>
          ))}
        </Card>
      )}

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
