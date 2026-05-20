import { useState, useEffect, useCallback } from 'react';
import { getOffers, createOffer, deleteOffer } from '../api.js';
import OfferDetailPanel from '../components/offers/OfferDetailPanel.jsx';
import { shortUuid } from '../components/offers/offerMeta.jsx';
import { inputCls } from '../components/offers/offerRulesConstants.js';

function Toggle({ on, onChange, label }) {
  return (
    <button
      className="toggle-track"
      data-active={on}
      onClick={() => onChange(!on)}
      type="button"
      role="switch"
      aria-checked={on}
      aria-label={label}
    >
      <span className="toggle-knob" data-active={on} />
    </button>
  );
}

function Toast({ message, type, onClose }) {
  useEffect(() => {
    const timer = setTimeout(onClose, 3000);
    return () => clearTimeout(timer);
  }, [onClose]);
  const cls =
    type === 'error'
      ? 'border-danger/30 bg-danger-muted text-danger'
      : 'border-success/30 bg-success-muted text-success';
  return (
    <div
      className={`fixed bottom-4 right-4 z-50 animate-fade-in rounded-md border px-4 py-3 text-sm ${cls}`}
      role="alert"
    >
      {message}
    </div>
  );
}

function CreateOfferModal({ onSave, onClose }) {
  const [form, setForm] = useState({
    code: '',
    cpa: '',
    payout_per_deposit: '',
    country_name: '',
    is_active: true,
    landing_url: '',
    cabinet_id: '',
    pixel_id: '',
    geo_code: '',
    geo_slot_name: '',
  });
  const [saving, setSaving] = useState(false);

  const handleSubmit = async (e) => {
    e.preventDefault();
    setSaving(true);
    try {
      await onSave({
        code: form.code,
        cpa_amount: parseFloat(form.cpa) || 0,
        payout_per_deposit: parseFloat(form.payout_per_deposit) || 0,
        country_name: form.country_name.trim() || null,
        is_active: form.is_active,
        landing_url: form.landing_url.trim() || null,
        cabinet_id: form.cabinet_id.trim() || null,
        pixel_id: form.pixel_id.trim() || null,
        geo_code: form.geo_code.trim().toUpperCase().slice(0, 2) || null,
        geo_slot_name: form.geo_slot_name.trim() || null,
      });
    } finally {
      setSaving(false);
    }
  };

  return (
    <div
      className="fixed inset-0 z-50 flex animate-fade-in items-center justify-center bg-black/60"
      onClick={onClose}
      role="dialog"
      aria-modal="true"
    >
      <div
        className="panel max-h-[90vh] w-full max-w-md space-y-4 overflow-y-auto p-6"
        onClick={(e) => e.stopPropagation()}
      >
        <h2 className="text-lg text-primary">Новый оффер</h2>
        <form onSubmit={handleSubmit} className="space-y-4">
          <div>
            <label
              className="mb-1 block text-2xs font-semibold uppercase tracking-wider text-secondary"
              htmlFor="offer-code"
            >
              Код оффера
            </label>
            <input
              id="offer-code"
              className={inputCls}
              type="text"
              placeholder="OFFER_AU_42"
              value={form.code}
              onChange={(e) => setForm({ ...form, code: e.target.value.toUpperCase() })}
              required
            />
            <div className="mt-1 text-2xs text-muted">
              Код используется для сопоставления — ищется в названии кампании
            </div>
          </div>
          <div>
            <label
              className="mb-1 block text-2xs font-semibold uppercase tracking-wider text-secondary"
              htmlFor="offer-country"
            >
              Страна оффера
            </label>
            <input
              id="offer-country"
              className={inputCls}
              type="text"
              placeholder="Демократическая Республика Конго"
              value={form.country_name}
              onChange={(e) => setForm({ ...form, country_name: e.target.value })}
            />
          </div>
          <div>
            <label
              className="mb-1 block text-2xs font-semibold uppercase tracking-wider text-secondary"
              htmlFor="offer-cpa"
            >
              CPA ($)
            </label>
            <input
              id="offer-cpa"
              className={inputCls}
              type="number"
              step="0.01"
              min="0"
              placeholder="5.00"
              value={form.cpa}
              onChange={(e) => setForm({ ...form, cpa: e.target.value })}
              required
            />
          </div>
          <div>
            <label
              className="mb-1 block text-2xs font-semibold uppercase tracking-wider text-secondary"
              htmlFor="offer-payout"
            >
              Выплата за депозит ($)
            </label>
            <input
              id="offer-payout"
              className={inputCls}
              type="number"
              step="0.01"
              min="0"
              placeholder="0.00"
              value={form.payout_per_deposit}
              onChange={(e) => setForm({ ...form, payout_per_deposit: e.target.value })}
            />
          </div>

          <div className="border-t border-border pt-2">
            <div className="mb-3 font-display text-sm text-secondary">
              Параметры автосоздания кампании
            </div>
            <div className="space-y-3">
              <div>
                <label
                  className="mb-1 block text-2xs font-semibold uppercase tracking-wider text-secondary"
                  htmlFor="offer-landing"
                >
                  Landing URL
                </label>
                <input
                  id="offer-landing"
                  className={inputCls}
                  type="text"
                  placeholder="https://landing.example.com"
                  value={form.landing_url}
                  onChange={(e) => setForm({ ...form, landing_url: e.target.value })}
                />
              </div>
              <div>
                <label
                  className="mb-1 block text-2xs font-semibold uppercase tracking-wider text-secondary"
                  htmlFor="offer-cabinet"
                >
                  Cabinet ID
                </label>
                <input
                  id="offer-cabinet"
                  className={inputCls}
                  type="text"
                  placeholder="act_123456789"
                  value={form.cabinet_id}
                  onChange={(e) => setForm({ ...form, cabinet_id: e.target.value })}
                />
              </div>
              <div>
                <label
                  className="mb-1 block text-2xs font-semibold uppercase tracking-wider text-secondary"
                  htmlFor="offer-pixel"
                >
                  Pixel ID
                </label>
                <input
                  id="offer-pixel"
                  className={inputCls}
                  type="text"
                  placeholder="123456789012345"
                  value={form.pixel_id}
                  onChange={(e) => setForm({ ...form, pixel_id: e.target.value })}
                />
              </div>
              <div className="grid grid-cols-3 gap-2">
                <div>
                  <label
                    className="mb-1 block text-2xs font-semibold uppercase tracking-wider text-secondary"
                    htmlFor="offer-geo-code"
                  >
                    GEO код
                  </label>
                  <input
                    id="offer-geo-code"
                    className={inputCls}
                    type="text"
                    placeholder="KE"
                    maxLength={2}
                    value={form.geo_code}
                    onChange={(e) => setForm({ ...form, geo_code: e.target.value.toUpperCase() })}
                  />
                </div>
                <div className="col-span-2">
                  <label
                    className="mb-1 block text-2xs font-semibold uppercase tracking-wider text-secondary"
                    htmlFor="offer-geo-slot"
                  >
                    GEO слот (как в FB)
                  </label>
                  <input
                    id="offer-geo-slot"
                    className={inputCls}
                    type="text"
                    placeholder="Кения"
                    value={form.geo_slot_name}
                    onChange={(e) => setForm({ ...form, geo_slot_name: e.target.value })}
                  />
                </div>
              </div>
            </div>
          </div>

          <div className="flex items-center gap-3">
            <Toggle
              on={form.is_active}
              onChange={(v) => setForm({ ...form, is_active: v })}
              label="Оффер активен"
            />
            <span className="text-sm text-secondary">{form.is_active ? 'Активен' : 'Выключен'}</span>
          </div>
          <div className="flex gap-2 pt-2">
            <button type="button" className="btn-secondary" onClick={onClose}>
              Отмена
            </button>
            <button type="submit" className="btn-primary" disabled={saving}>
              {saving ? 'Сохранение...' : 'Создать'}
            </button>
          </div>
        </form>
      </div>
    </div>
  );
}

function OfferListItem({ offer, selected, onSelect }) {
  return (
    <button
      type="button"
      onClick={() => onSelect(offer.id)}
      className={`w-full border-l-2 px-3 py-2.5 text-left transition-colors ${
        selected
          ? 'border-l-accent bg-accent-muted/20'
          : 'border-l-transparent hover:border-l-border hover:bg-elevated/50'
      }`}
      aria-current={selected ? 'true' : undefined}
    >
      <div className="flex items-start justify-between gap-2">
        <div className="min-w-0">
          <div className="truncate font-mono text-sm font-semibold text-accent">{offer.code}</div>
          <div className="mt-0.5 font-mono text-2xs text-primary">
            ${Number(offer.cpa_amount ?? offer.cpa).toFixed(2)}
          </div>
          <div className="mt-0.5 truncate text-2xs text-muted">
            {offer.country_name || 'Страна не задана'}
          </div>
          {(offer.cabinet_id || offer.pixel_id || offer.id) && (
            <div className="mt-1 space-y-0.5 font-mono text-2xs text-muted">
              {offer.id && <div title={offer.id}>ID {shortUuid(offer.id)}</div>}
              {offer.cabinet_id && <div className="truncate" title={offer.cabinet_id}>Каб. {shortUuid(offer.cabinet_id)}</div>}
              {offer.pixel_id && (
                <div className="truncate" title={offer.pixel_id}>
                  Пикс. {shortUuid(offer.pixel_id)}
                </div>
              )}
            </div>
          )}
        </div>
        <span className={`shrink-0 ${offer.is_active ? 'badge-success' : 'badge-neutral'}`}>
          {offer.is_active ? '●' : '○'}
        </span>
      </div>
    </button>
  );
}

export default function OffersPage() {
  const [offers, setOffers] = useState([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState(null);
  const [selectedId, setSelectedId] = useState(null);
  const [detailTab, setDetailTab] = useState('thresholds');
  const [showModal, setShowModal] = useState(false);
  const [toast, setToast] = useState(null);

  const fetchOffers = useCallback(async () => {
    try {
      setError(null);
      const data = await getOffers();
      setOffers(Array.isArray(data) ? data : []);
    } catch (err) {
      setError(err.message || 'Не удалось загрузить офферы');
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    fetchOffers();
  }, [fetchOffers]);

  useEffect(() => {
    if (offers.length === 0) {
      setSelectedId(null);
      return;
    }
    if (!selectedId || !offers.some((o) => o.id === selectedId)) {
      setSelectedId(offers[0].id);
    }
  }, [offers, selectedId]);

  const selectedOffer = offers.find((o) => o.id === selectedId) ?? null;

  const handleCreateOffer = async (data) => {
    try {
      const created = await createOffer(data);
      setToast({ message: 'Оффер создан', type: 'success' });
      setShowModal(false);
      await fetchOffers();
      if (created?.id) {
        setSelectedId(created.id);
        setDetailTab('thresholds');
      }
    } catch (err) {
      setToast({ message: err.message || 'Ошибка сохранения', type: 'error' });
    }
  };

  const handleDelete = async (offer) => {
    if (!confirm(`Удалить оффер "${offer.code}"?`)) return;
    try {
      await deleteOffer(offer.id);
      setToast({ message: 'Оффер удалён', type: 'success' });
      if (selectedId === offer.id) setSelectedId(null);
      fetchOffers();
    } catch (err) {
      setToast({ message: err.message || 'Ошибка удаления', type: 'error' });
    }
  };

  return (
    <div className="space-y-md animate-fade-in">
      <div className="flex items-center justify-between">
        <div>
          <h1 className="font-display text-lg text-primary">Офферы</h1>
          <p className="text-sm text-muted">
            Управление офферами и стоп-правилами · {offers.length} шт.
          </p>
          <p className="mt-1 max-w-2xl text-2xs text-muted">
            Слева список, справа пороги и правила выбранного оффера. На узком экране панели
            идут столбцом.
          </p>
        </div>
        <button className="btn-primary" onClick={() => setShowModal(true)}>
          + Новый оффер
        </button>
      </div>

      {loading && (
        <div className="flex items-center gap-3 py-12 text-sm text-muted">
          <div className="h-5 w-5 animate-spin rounded-full border-2 border-accent border-t-transparent" />
          Загрузка офферов...
        </div>
      )}

      {error && !loading && (
        <div className="rounded-md border border-danger/30 bg-danger-muted px-4 py-3 text-sm text-danger">
          {error}
          <button type="button" className="btn-ghost ml-3" onClick={fetchOffers}>
            Повторить
          </button>
        </div>
      )}

      {!loading && !error && (
        <div className="layout-split min-h-[28rem] sm:min-h-[32rem]">
          <aside className="flex w-full shrink-0 flex-col overflow-hidden border-b border-border sm:w-64 sm:border-b-0 sm:border-r lg:w-72">
            <div className="border-b border-border px-3 py-2 font-display text-sm text-secondary">
              Список
            </div>
            <div className="max-h-[40vh] flex-1 space-y-1 overflow-y-auto p-2 sm:max-h-none">
              {offers.length === 0 ? (
                <div className="px-2 py-8 text-center">
                  <div className="text-sm font-medium text-primary">Нет офферов</div>
                  <div className="mt-1 text-2xs text-muted">Создайте первый оффер</div>
                </div>
              ) : (
                offers.map((o) => (
                  <OfferListItem
                    key={o.id}
                    offer={o}
                    selected={o.id === selectedId}
                    onSelect={setSelectedId}
                  />
                ))
              )}
            </div>
          </aside>

          <main className="flex min-h-[20rem] min-w-0 flex-1 flex-col overflow-hidden">
            {selectedOffer ? (
              <OfferDetailPanel
                offer={selectedOffer}
                activeTab={detailTab}
                onTabChange={setDetailTab}
                onOfferUpdated={fetchOffers}
                onToast={setToast}
                onDelete={handleDelete}
              />
            ) : (
              <div className="flex flex-1 flex-col items-center justify-center px-6 py-16 text-center">
                <div className="text-2xl text-muted">○</div>
                <div className="mt-2 text-sm font-medium text-primary">Выберите оффер</div>
                <div className="mt-1 text-2xs text-muted">
                  Или создайте новый через кнопку «+ Новый оффер»
                </div>
              </div>
            )}
          </main>
        </div>
      )}

      {showModal && (
        <CreateOfferModal onSave={handleCreateOffer} onClose={() => setShowModal(false)} />
      )}
      {toast && (
        <Toast message={toast.message} type={toast.type} onClose={() => setToast(null)} />
      )}
    </div>
  );
}
