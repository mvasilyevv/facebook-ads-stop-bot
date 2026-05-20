import React, { useEffect, useState, useCallback } from "react";
import { fetchJson } from "../api.js";
import Loader from "../components/Loader.jsx";
import ErrorBox from "../components/ErrorBox.jsx";
import EmptyState from "../components/EmptyState.jsx";
import Card from "../components/Card.jsx";
import ThresholdsModal from "../components/ThresholdsModal.jsx";
import { haptic } from "../theme.js";

async function copyOfferId(id) {
  const value = String(id ?? "").trim();
  if (!value) return false;
  try {
    if (navigator.clipboard?.writeText) {
      await navigator.clipboard.writeText(value);
      return true;
    }
  } catch {
    /* fallback ниже */
  }
  try {
    const el = document.createElement("textarea");
    el.value = value;
    el.setAttribute("readonly", "");
    el.style.position = "absolute";
    el.style.left = "-9999px";
    document.body.appendChild(el);
    el.select();
    const ok = document.execCommand("copy");
    document.body.removeChild(el);
    return ok;
  } catch {
    return false;
  }
}

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

// Детали оффера — bottom sheet (master-detail lite)
function OfferDetailSheet({ offer, onClose, onEdit, onThresholds, onToggleActive, onDelete, onCopyId }) {
  if (!offer) return null;

  const payout = offer.payout_per_deposit != null ? Number(offer.payout_per_deposit) : null;

  return (
    <div className="modal-overlay" onClick={onClose}>
      <div className="modal-sheet offer-detail-sheet" onClick={(e) => e.stopPropagation()}>
        <div className="offer-detail-header">
          <h2>{offer.code}</h2>
          <button type="button" className="offer-detail-close" onClick={onClose} aria-label="Закрыть">
            ✕
          </button>
        </div>
        <span className={offer.is_active ? "badge badge-active" : "badge badge-disabled"}>
          {offer.is_active ? "Активен" : "Выключен"}
        </span>
        <dl className="offer-detail-dl">
          <dt>CPA</dt>
          <dd>${Number(offer.cpa_amount ?? offer.cpa).toFixed(2)}</dd>
          {payout != null && !Number.isNaN(payout) && (
            <>
              <dt>Выплата за депозит</dt>
              <dd>${payout.toFixed(2)}</dd>
            </>
          )}
          {offer.country_name && (
            <>
              <dt>Страна</dt>
              <dd>{offer.country_name}</dd>
            </>
          )}
          {offer.landing_url && (
            <>
              <dt>Landing URL</dt>
              <dd className="offer-detail-mono">{offer.landing_url}</dd>
            </>
          )}
          {(offer.geo_code || offer.geo_slot_name) && (
            <>
              <dt>GEO</dt>
              <dd>
                {offer.geo_code}
                {offer.geo_slot_name ? ` · ${offer.geo_slot_name}` : ""}
              </dd>
            </>
          )}
          {offer.cabinet_id && (
            <>
              <dt>Кабинет</dt>
              <dd className="offer-detail-mono">{offer.cabinet_id}</dd>
            </>
          )}
          {offer.pixel_id && (
            <>
              <dt>Пиксель</dt>
              <dd className="offer-detail-mono">{offer.pixel_id}</dd>
            </>
          )}
        </dl>
        <button type="button" className="offer-id-copy" onClick={() => onCopyId(offer)}>
          UUID: {String(offer.id)}
        </button>
        <div className="offer-detail-actions">
          <button type="button" className="btn btn-secondary" onClick={() => onThresholds(offer)}>
            Пороги
          </button>
          <button type="button" className="btn btn-secondary" onClick={() => onEdit(offer)}>
            Редактировать
          </button>
          <button type="button" className="btn btn-secondary" onClick={() => onToggleActive(offer)}>
            {offer.is_active ? "Выключить" : "Включить"}
          </button>
          <button type="button" className="btn btn-danger" onClick={() => onDelete(offer)}>
            Удалить
          </button>
        </div>
      </div>
    </div>
  );
}

// Модальный bottom sheet для добавления/редактирования оффера
function OfferModal({ offer, onSave, onClose }) {
  const [form, setForm] = useState({
    code: offer?.code ?? "",
    cpa_amount: offer?.cpa_amount ?? offer?.cpa ?? "",
    payout_per_deposit: offer?.payout_per_deposit ?? "",
    country_name: offer?.country_name ?? "",
    landing_url: offer?.landing_url ?? "",
    geo_code: offer?.geo_code ?? "",
    geo_slot_name: offer?.geo_slot_name ?? "",
    is_active: offer?.is_active ?? true,
  });
  const [saving, setSaving] = useState(false);

  const handleSubmit = async (e) => {
    e.preventDefault();
    setSaving(true);
    haptic.impact("medium");
    try {
      await onSave({
        code: form.code.toUpperCase().trim(),
        cpa_amount: parseFloat(form.cpa_amount) || 0,
        payout_per_deposit: parseFloat(form.payout_per_deposit) || 0,
        country_name: form.country_name.trim() || null,
        landing_url: form.landing_url.trim() || null,
        geo_code: form.geo_code.trim().toUpperCase().slice(0, 2) || null,
        geo_slot_name: form.geo_slot_name.trim() || null,
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
            <label className="form-label">Выплата за депозит ($)</label>
            <input
              className="form-input"
              type="number"
              step="0.01"
              min="0"
              placeholder="0.00"
              value={form.payout_per_deposit}
              onChange={(e) => setForm({ ...form, payout_per_deposit: e.target.value })}
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
          <p className="hint form-section-label">Параметры автосоздания кампании</p>
          <div className="form-group">
            <label className="form-label">Landing URL</label>
            <input
              className="form-input"
              type="text"
              placeholder="https://landing.example.com"
              value={form.landing_url}
              onChange={(e) => setForm({ ...form, landing_url: e.target.value })}
            />
          </div>
          <div className="form-row-geo">
            <div className="form-group">
              <label className="form-label">GEO код</label>
              <input
                className="form-input"
                type="text"
                placeholder="KE"
                maxLength={2}
                value={form.geo_code}
                onChange={(e) => setForm({ ...form, geo_code: e.target.value.toUpperCase() })}
              />
            </div>
            <div className="form-group form-group-grow">
              <label className="form-label">GEO слот (как в FB)</label>
              <input
                className="form-input"
                type="text"
                placeholder="Кения"
                value={form.geo_slot_name}
                onChange={(e) => setForm({ ...form, geo_slot_name: e.target.value })}
              />
            </div>
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
  const [thresholdsFor, setThresholdsFor] = useState(null);
  const [selectedOffer, setSelectedOffer] = useState(null);
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
          body: JSON.stringify({ ...editOffer, ...data }),
        });
        haptic.notify("success");
        setToast({ type: "ok", text: "Оффер обновлён" });
      } else {
        await fetchJson("/offers", {
          method: "POST",
          body: JSON.stringify(data),
        });
        haptic.notify("success");
        setToast({ type: "ok", text: "Оффер создан" });
      }
      setShowModal(false);
      setEditOffer(null);
      setSelectedOffer(null);
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
      setSelectedOffer(null);
      load();
    } catch (err) {
      haptic.notify("error");
      setToast({ type: "error", text: err.message });
    }
  };

  const handleCopyId = async (offer) => {
    haptic.selection();
    const ok = await copyOfferId(offer.id);
    if (ok) {
      haptic.notify("success");
      setToast({ type: "ok", text: "UUID скопирован" });
    } else {
      haptic.notify("error");
      setToast({ type: "error", text: "Не удалось скопировать UUID" });
    }
  };

  const handleToggleActive = async (offer) => {
    haptic.selection();
    try {
      await fetchJson(`/offers/${offer.id}`, {
        method: "PUT",
        body: JSON.stringify({ ...offer, is_active: !offer.is_active }),
      });
      const next = { ...offer, is_active: !offer.is_active };
      setOffers((prev) => prev.map((o) => (o.id === offer.id ? next : o)));
      setSelectedOffer((prev) => (prev?.id === offer.id ? next : prev));
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
            <div
              key={o.id}
              className={`offer-row offer-row-selectable${selectedOffer?.id === o.id ? " offer-row-selected" : ""}`}
              role="button"
              tabIndex={0}
              onClick={() => {
                haptic.selection();
                setSelectedOffer(o);
              }}
              onKeyDown={(e) => {
                if (e.key === "Enter" || e.key === " ") {
                  e.preventDefault();
                  setSelectedOffer(o);
                }
              }}
            >
              <div className="offer-row-body">
                <div className="offer-code">{o.code}</div>
                <div className="offer-meta">
                  <span style={{ fontWeight: 600 }}>${Number(o.cpa_amount ?? o.cpa).toFixed(2)}</span>
                  {o.country_name && <span> · {o.country_name}</span>}
                  {o.geo_code && <span> · {o.geo_code}</span>}
                </div>
                {(o.cabinet_id || o.pixel_id || o.landing_url) && (
                  <div className="offer-meta offer-meta-ids">
                    {o.landing_url && <span className="offer-meta-truncate">URL: {o.landing_url}</span>}
                    {o.cabinet_id && <span>Кабинет: {o.cabinet_id}</span>}
                    {o.pixel_id && <span>{o.cabinet_id || o.landing_url ? " · " : ""}Пиксель: {o.pixel_id}</span>}
                  </div>
                )}
              </div>
              <div className="offer-row-tail">
                <span
                  className={o.is_active ? "badge badge-active" : "badge badge-disabled"}
                  onClick={(e) => {
                    e.stopPropagation();
                    handleToggleActive(o);
                  }}
                >
                  {o.is_active ? "Активен" : "Выкл."}
                </span>
                <span className="offer-row-chevron" aria-hidden>
                  ›
                </span>
              </div>
            </div>
          ))}
        </Card>
      )}

      {selectedOffer && (
        <OfferDetailSheet
          offer={selectedOffer}
          onClose={() => setSelectedOffer(null)}
          onEdit={(o) => {
            setSelectedOffer(null);
            setEditOffer(o);
            setShowModal(true);
          }}
          onThresholds={(o) => {
            setSelectedOffer(null);
            setThresholdsFor(o);
          }}
          onToggleActive={handleToggleActive}
          onDelete={async (o) => {
            await handleDelete(o);
          }}
          onCopyId={handleCopyId}
        />
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

      {thresholdsFor && (
        <ThresholdsModal
          offer={thresholdsFor}
          onClose={() => setThresholdsFor(null)}
          onSaved={() => setToast({ type: "ok", text: "Пороги обновлены" })}
          onError={(msg) => setToast({ type: "error", text: msg })}
        />
      )}

      {toast && (
        <Toast message={toast.text} type={toast.type} onClose={() => setToast(null)} />
      )}
    </div>
  );
}
