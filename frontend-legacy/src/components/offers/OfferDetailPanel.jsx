import { useState } from 'react';
import { updateOffer } from '../../api.js';
import { OfferMetaChips } from './offerMeta.jsx';
import { inputCls } from './offerRulesConstants.js';
import OfferThresholdsTab from './OfferThresholdsTab.jsx';
import OfferRulesTab from './OfferRulesTab.jsx';

const TABS = [
  { id: 'thresholds', label: 'Пороги' },
  { id: 'rules', label: 'Правила' },
];

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

function OfferMetadataForm({ offer, onSave, onCancel, saving }) {
  const [form, setForm] = useState({
    cpa: offer.cpa_amount ?? offer.cpa ?? '',
    payout_per_deposit: offer.payout_per_deposit ?? '',
    country_name: offer.country_name || '',
    is_active: offer.is_active ?? true,
    landing_url: offer.landing_url || '',
    cabinet_id: offer.cabinet_id || '',
    pixel_id: offer.pixel_id || '',
    geo_code: offer.geo_code || '',
    geo_slot_name: offer.geo_slot_name || '',
  });

  const handleSubmit = (e) => {
    e.preventDefault();
    onSave({
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
  };

  return (
    <form onSubmit={handleSubmit} className="space-y-3 border-t border-border/50 pt-4">
      <div className="grid grid-cols-1 gap-3 sm:grid-cols-2">
        <div>
          <label className="mb-1 block text-2xs font-semibold uppercase tracking-wider text-secondary">
            CPA ($)
          </label>
          <input
            className={inputCls}
            type="number"
            step="0.01"
            min="0"
            value={form.cpa}
            onChange={(e) => setForm({ ...form, cpa: e.target.value })}
            required
          />
        </div>
        <div>
          <label className="mb-1 block text-2xs font-semibold uppercase tracking-wider text-secondary">
            Выплата за депозит ($)
          </label>
          <input
            className={inputCls}
            type="number"
            step="0.01"
            min="0"
            value={form.payout_per_deposit}
            onChange={(e) => setForm({ ...form, payout_per_deposit: e.target.value })}
          />
        </div>
        <div className="sm:col-span-2">
          <label className="mb-1 block text-2xs font-semibold uppercase tracking-wider text-secondary">
            Страна
          </label>
          <input
            className={inputCls}
            type="text"
            value={form.country_name}
            onChange={(e) => setForm({ ...form, country_name: e.target.value })}
          />
        </div>
        <div className="sm:col-span-2">
          <label className="mb-1 block text-2xs font-semibold uppercase tracking-wider text-secondary">
            Landing URL
          </label>
          <input
            className={inputCls}
            type="text"
            value={form.landing_url}
            onChange={(e) => setForm({ ...form, landing_url: e.target.value })}
          />
        </div>
        <div>
          <label className="mb-1 block text-2xs font-semibold uppercase tracking-wider text-secondary">
            Cabinet ID
          </label>
          <input
            className={inputCls}
            type="text"
            value={form.cabinet_id}
            onChange={(e) => setForm({ ...form, cabinet_id: e.target.value })}
          />
        </div>
        <div>
          <label className="mb-1 block text-2xs font-semibold uppercase tracking-wider text-secondary">
            Pixel ID
          </label>
          <input
            className={inputCls}
            type="text"
            value={form.pixel_id}
            onChange={(e) => setForm({ ...form, pixel_id: e.target.value })}
          />
        </div>
        <div>
          <label className="mb-1 block text-2xs font-semibold uppercase tracking-wider text-secondary">
            GEO код
          </label>
          <input
            className={inputCls}
            type="text"
            maxLength={2}
            value={form.geo_code}
            onChange={(e) => setForm({ ...form, geo_code: e.target.value.toUpperCase() })}
          />
        </div>
        <div>
          <label className="mb-1 block text-2xs font-semibold uppercase tracking-wider text-secondary">
            GEO слот
          </label>
          <input
            className={inputCls}
            type="text"
            value={form.geo_slot_name}
            onChange={(e) => setForm({ ...form, geo_slot_name: e.target.value })}
          />
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
      <div className="flex gap-2">
        <button type="button" className="btn-secondary" onClick={onCancel} disabled={saving}>
          Отмена
        </button>
        <button type="submit" className="btn-primary" disabled={saving}>
          {saving ? 'Сохранение...' : 'Сохранить'}
        </button>
      </div>
    </form>
  );
}

export default function OfferDetailPanel({
  offer,
  activeTab,
  onTabChange,
  onOfferUpdated,
  onToast,
  onDelete,
}) {
  const [editing, setEditing] = useState(false);
  const [savingMeta, setSavingMeta] = useState(false);

  const handleSaveMeta = async (data) => {
    setSavingMeta(true);
    try {
      await updateOffer(offer.id, data);
      onToast?.({ message: 'Оффер обновлён', type: 'success' });
      setEditing(false);
      onOfferUpdated?.();
    } catch (err) {
      onToast?.({ message: err.message || 'Ошибка сохранения', type: 'error' });
    } finally {
      setSavingMeta(false);
    }
  };

  return (
    <div className="flex h-full min-h-0 flex-col">
      <div className="shrink-0 border-b border-border px-4 py-4 sm:px-5">
        <div className="flex flex-wrap items-start justify-between gap-3">
          <div className="min-w-0">
            <h2 className="truncate font-display text-xl text-accent">{offer.code}</h2>
            <p className="mt-0.5 font-mono text-sm text-primary">
              ${Number(offer.cpa_amount ?? offer.cpa).toFixed(2)}
              {offer.payout_per_deposit != null && Number(offer.payout_per_deposit) > 0 && (
                <span className="text-muted">
                  {' '}
                  · выплата ${Number(offer.payout_per_deposit).toFixed(2)}
                </span>
              )}
            </p>
            <p className="mt-0.5 text-sm text-secondary">{offer.country_name || 'Страна не задана'}</p>
          </div>
          <span className={offer.is_active ? 'badge-success' : 'badge-neutral'}>
            {offer.is_active ? 'Активен' : 'Выкл.'}
          </span>
        </div>

        <div className="mt-3">
          <OfferMetaChips offer={offer} />
        </div>

        <div className="mt-3 flex flex-wrap gap-2">
          <button
            type="button"
            className="btn-ghost text-2xs"
            onClick={() => setEditing((v) => !v)}
          >
            {editing ? 'Свернуть' : 'Редактировать'}
          </button>
          <button type="button" className="btn-ghost text-2xs text-danger" onClick={() => onDelete?.(offer)}>
            Удалить
          </button>
        </div>

        {editing && (
          <OfferMetadataForm
            key={offer.id}
            offer={offer}
            saving={savingMeta}
            onSave={handleSaveMeta}
            onCancel={() => setEditing(false)}
          />
        )}
      </div>

      <div className="shrink-0 border-b border-border px-4 sm:px-5">
        <div className="flex gap-1" role="tablist" aria-label="Настройки оффера">
          {TABS.map((tab) => (
            <button
              key={tab.id}
              type="button"
              role="tab"
              aria-selected={activeTab === tab.id}
              className={`border-b-2 px-3 py-2.5 text-sm transition-colors ${
                activeTab === tab.id
                  ? 'border-accent text-accent'
                  : 'border-transparent text-muted hover:text-secondary'
              }`}
              onClick={() => onTabChange(tab.id)}
            >
              {tab.label}
            </button>
          ))}
        </div>
      </div>

      <div className="min-h-0 flex-1 overflow-y-auto px-4 py-4 sm:px-5" role="tabpanel">
        {activeTab === 'thresholds' && (
          <OfferThresholdsTab
            key={`th-${offer.id}`}
            offer={offer}
            onSaved={() => onToast?.({ message: 'Пороги обновлены', type: 'success' })}
            onError={(msg) => onToast?.({ message: msg, type: 'error' })}
          />
        )}
        {activeTab === 'rules' && (
          <OfferRulesTab
            key={`rl-${offer.id}`}
            offer={offer}
            onSaved={() => onToast?.({ message: 'Правила сохранены', type: 'success' })}
            onError={(msg) => onToast?.({ message: msg, type: 'error' })}
          />
        )}
      </div>
    </div>
  );
}
