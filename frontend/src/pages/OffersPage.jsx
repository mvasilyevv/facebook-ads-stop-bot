import { useState, useEffect, useCallback } from 'react';
import { getOffers, createOffer, updateOffer, deleteOffer, getOfferRules, updateOfferRules } from '../api.js';
import ThresholdsModal from '../components/offers/ThresholdsModal.jsx';

/* Тогл-переключатель */
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

/* Всплывающее уведомление */
function Toast({ message, type, onClose }) {
  useEffect(() => {
    const timer = setTimeout(onClose, 3000);
    return () => clearTimeout(timer);
  }, [onClose]);
  const cls = type === 'error' ? 'border-danger/30 bg-danger-muted text-danger' : 'border-success/30 bg-success-muted text-success';
  return (
    <div className={`fixed bottom-4 right-4 z-50 rounded-md border px-4 py-3 text-sm animate-fade-in ${cls}`} role="alert">
      {message}
    </div>
  );
}

/* Инпут */
const inputCls = 'w-full rounded bg-elevated border border-border px-3 py-2 text-sm text-primary focus:border-accent focus:ring-1 focus:ring-accent focus:outline-none disabled:opacity-50';

/* Модалка создания/редактирования оффера */
function OfferModal({ offer, onSave, onClose }) {
  const [form, setForm] = useState({
    code: offer?.code || '',
    cpa: offer?.cpa_amount || offer?.cpa || '',
    country_name: offer?.country_name || '',
    is_active: offer?.is_active ?? true,
  });
  const [saving, setSaving] = useState(false);

  const handleSubmit = async (e) => {
    e.preventDefault();
    setSaving(true);
    try {
      await onSave({
        code: form.code,
        cpa_amount: parseFloat(form.cpa) || 0,
        country_name: form.country_name.trim() || null,
        is_active: form.is_active,
      });
    } finally {
      setSaving(false);
    }
  };

  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/60 animate-fade-in" onClick={onClose} role="dialog" aria-modal="true">
      <div className="panel w-full max-w-md p-6 space-y-4" onClick={(e) => e.stopPropagation()}>
        <h2 className="text-lg text-primary">{offer ? 'Редактировать оффер' : 'Новый оффер'}</h2>
        <form onSubmit={handleSubmit} className="space-y-4">
          <div>
            <label className="mb-1 block text-2xs font-semibold uppercase tracking-wider text-secondary" htmlFor="offer-code">Код оффера</label>
            <input id="offer-code" className={inputCls} type="text" placeholder="OFFER_AU_42" value={form.code} onChange={(e) => setForm({ ...form, code: e.target.value.toUpperCase() })} required disabled={!!offer} />
            <div className="mt-1 text-2xs text-muted">Код используется для сопоставления — ищется в названии кампании</div>
          </div>
          <div>
            <label className="mb-1 block text-2xs font-semibold uppercase tracking-wider text-secondary" htmlFor="offer-country">Страна оффера</label>
            <input id="offer-country" className={inputCls} type="text" placeholder="Демократическая Республика Конго" value={form.country_name} onChange={(e) => setForm({ ...form, country_name: e.target.value })} />
            <div className="mt-1 text-2xs text-muted">Используется в скрипте создания кампаний как страна уровня Страна/регион</div>
          </div>
          <div>
            <label className="mb-1 block text-2xs font-semibold uppercase tracking-wider text-secondary" htmlFor="offer-cpa">CPA ($)</label>
            <input id="offer-cpa" className={inputCls} type="number" step="0.01" min="0" placeholder="5.00" value={form.cpa} onChange={(e) => setForm({ ...form, cpa: e.target.value })} required />
          </div>
          <div className="flex items-center gap-3">
            <Toggle on={form.is_active} onChange={(v) => setForm({ ...form, is_active: v })} label="Оффер активен" />
            <span className="text-sm text-secondary">{form.is_active ? 'Активен' : 'Выключен'}</span>
          </div>
          <div className="flex gap-2 pt-2">
            <button type="button" className="btn-secondary" onClick={onClose}>Отмена</button>
            <button type="submit" className="btn-primary" disabled={saving}>{saving ? 'Сохранение...' : offer ? 'Сохранить' : 'Создать'}</button>
          </div>
        </form>
      </div>
    </div>
  );
}

/* Определения стоп-правил */
const RULE_DEFS = [
  { key: 'cpc_percent', title: 'Правило 1: CPC > X% CPA', hint: 'Стоп при стоимости клика выше установленного % от целевого CPA', fields: [{ name: 'cpc_percent_stop', label: 'Процент стопа (%)', type: 'number' }] },
  { key: 'cpl_percent', title: 'Правило 2: CPL > X% CPA', hint: 'Стоп при стоимости лида выше установленного % от целевого CPA', fields: [{ name: 'cpl_percent_stop', label: 'Процент стопа (%)', type: 'number' }] },
  { key: 'cpr_percent', title: 'Правило 3: CPR > X% CPA', hint: 'Стоп при стоимости регистрации выше установленного % от целевого CPA', fields: [{ name: 'cpr_percent_stop', label: 'Процент стопа (%)', type: 'number' }] },
  { key: 'regs_no_dep', title: 'Правило 4: N регистраций без депозитов', hint: 'Стоп при заданном количестве регистраций без депозита', fields: [{ name: 'regs_no_dep_stop_count', label: 'Количество регистраций', type: 'number' }] },
  { key: 'spend_no_dep', title: 'Правило 5: Расход без депозитов', hint: 'Стоп при расходе в диапазоне % от CPA без депозитов', fields: [{ name: 'spend_no_dep_from_percent', label: 'Расход от (% CPA)', type: 'number' }, { name: 'spend_no_dep_to_percent', label: 'Расход до (% CPA)', type: 'number' }] },
  { key: 'spend_with_dep', title: 'Правило 6: Расход с депозитом', hint: 'Стоп при расходе в диапазоне % от CPA с депозитом', fields: [{ name: 'spend_with_dep_from_percent', label: 'Расход от (% CPA)', type: 'number' }, { name: 'spend_with_dep_to_percent', label: 'Расход до (% CPA)', type: 'number' }] },
];

const DIAGNOSTIC_FIELDS = [
  { name: 'frequency_elevated_threshold', label: 'Частота: повышено от', type: 'number' },
  { name: 'frequency_critical_threshold', label: 'Частота: критично от', type: 'number' },
];

const DEFAULT_RULES = {
  cpc_percent_enabled: true, cpc_percent_stop: '2',
  cpl_percent_enabled: true, cpl_percent_stop: '10',
  cpr_percent_enabled: true, cpr_percent_stop: '20',
  regs_no_dep_enabled: true, regs_no_dep_stop_count: '5',
  spend_no_dep_enabled: true, spend_no_dep_from_percent: '50', spend_no_dep_to_percent: '70',
  spend_with_dep_enabled: true, spend_with_dep_from_percent: '70', spend_with_dep_to_percent: '90',
  frequency_elevated_threshold: '2',
  frequency_critical_threshold: '3',
};

/* Блок правила (используется для стоп-правил и ранних сигналов) */
function RuleBlock({ rule, rules, setRules }) {
  return (
    <div className="rounded-md border border-border bg-elevated/50 p-4 space-y-3">
      <div className="flex items-start justify-between gap-3">
        <div className="min-w-0 flex-1">
          <div className="text-sm font-medium text-primary">{rule.title}</div>
          {rule.hint && <p className="mt-0.5 text-2xs text-muted">{rule.hint}</p>}
        </div>
        <Toggle on={rules[`${rule.key}_enabled`]} onChange={(v) => setRules({ ...rules, [`${rule.key}_enabled`]: v })} label={`Включить ${rule.title}`} />
      </div>
      <div className="grid grid-cols-1 sm:grid-cols-2 gap-3">
        {rule.fields.map((field) => (
          <div key={field.name}>
            <label className="mb-1 block text-2xs font-semibold uppercase tracking-wider text-secondary" htmlFor={`rule-${field.name}`}>
              {field.label}
            </label>
            <input
              id={`rule-${field.name}`}
              className={inputCls}
              type="text"
              inputMode="numeric"
              pattern="[0-9]*"
              value={rules[field.name] || ''}
              onChange={(e) => setRules({ ...rules, [field.name]: e.target.value.replace(/\D/g, '') })}
              disabled={!rules[`${rule.key}_enabled`]}
            />
          </div>
        ))}
      </div>
    </div>
  );
}

export default function OffersPage() {
  const [offers, setOffers] = useState([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState(null);
  const [editingId, setEditingId] = useState(null);
  const [rules, setRules] = useState(DEFAULT_RULES);
  const [rulesLoading, setRulesLoading] = useState(false);
  const [showModal, setShowModal] = useState(false);
  const [editOffer, setEditOffer] = useState(null);
  const [toast, setToast] = useState(null);
  const [savingRules, setSavingRules] = useState(false);
  const [thresholdsFor, setThresholdsFor] = useState(null);

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

  useEffect(() => { fetchOffers(); }, [fetchOffers]);

  const openRules = useCallback(async (offerId) => {
    if (editingId === offerId) { setEditingId(null); return; }
    setEditingId(offerId);
    setRulesLoading(true);
    try {
      const data = await getOfferRules(offerId);
      setRules(data && typeof data === 'object' ? { ...DEFAULT_RULES, ...data } : DEFAULT_RULES);
    } catch {
      setRules(DEFAULT_RULES);
    } finally {
      setRulesLoading(false);
    }
  }, [editingId]);

  const handleSaveRules = async () => {
    if (!editingId) return;
    setSavingRules(true);
    try {
      await updateOfferRules(editingId, rules);
      setToast({ message: 'Правила сохранены', type: 'success' });
    } catch (err) {
      setToast({ message: err.message || 'Ошибка сохранения', type: 'error' });
    } finally {
      setSavingRules(false);
    }
  };

  const handleSaveOffer = async (data) => {
    try {
      if (editOffer) {
        await updateOffer(editOffer.id, data);
        setToast({ message: 'Оффер обновлён', type: 'success' });
      } else {
        await createOffer(data);
        setToast({ message: 'Оффер создан', type: 'success' });
      }
      setShowModal(false);
      setEditOffer(null);
      fetchOffers();
    } catch (err) {
      setToast({ message: err.message || 'Ошибка сохранения', type: 'error' });
    }
  };

  const handleDelete = async (offer) => {
    if (!confirm(`Удалить оффер "${offer.code}"?`)) return;
    try {
      await deleteOffer(offer.id);
      setToast({ message: 'Оффер удалён', type: 'success' });
      if (editingId === offer.id) setEditingId(null);
      fetchOffers();
    } catch (err) {
      setToast({ message: err.message || 'Ошибка удаления', type: 'error' });
    }
  };

  return (
    <div className="space-y-md animate-fade-in">
      {/* Заголовок */}
      <div className="flex items-center justify-between">
        <div>
          <h1 className="text-lg text-primary">Офферы</h1>
          <p className="text-sm text-muted">Управление офферами и стоп-правилами · {offers.length} шт.</p>
        </div>
        <button className="btn-primary" onClick={() => { setEditOffer(null); setShowModal(true); }}>
          + Добавить оффер
        </button>
      </div>

      {/* Загрузка */}
      {loading && (
        <div className="flex items-center gap-3 py-12 text-sm text-muted">
          <div className="h-5 w-5 animate-spin rounded-full border-2 border-accent border-t-transparent" />
          Загрузка офферов...
        </div>
      )}

      {/* Ошибка */}
      {error && !loading && (
        <div className="rounded-md bg-danger-muted border border-danger/30 px-4 py-3 text-sm text-danger">
          {error}
          <button className="btn-ghost ml-3" onClick={fetchOffers}>Повторить</button>
        </div>
      )}

      {/* Таблица офферов */}
      {!loading && !error && (
        <div className="panel overflow-hidden">
          <div className="grid gap-2 p-3 md:hidden">
            {offers.map((o) => (
              <div key={o.id} className="rounded-md border border-border bg-elevated/35 px-3 py-2.5">
                <div className="mb-2 flex items-start justify-between gap-3">
                  <div>
                    <div className="font-mono text-sm font-semibold text-accent">{o.code}</div>
                    <div className="mt-0.5 font-mono text-2xs text-primary">${Number(o.cpa_amount ?? o.cpa).toFixed(2)}</div>
                    <div className="mt-0.5 text-2xs text-muted">{o.country_name || 'Страна не задана'}</div>
                  </div>
                  <span className={o.is_active ? 'badge-success' : 'badge-neutral'}>
                    {o.is_active ? 'Активен' : 'Выкл.'}
                  </span>
                </div>
                <div className="flex flex-wrap gap-1.5">
                  <button className="btn-ghost text-2xs" onClick={() => openRules(o.id)} aria-expanded={editingId === o.id}>
                    {editingId === o.id ? 'Свернуть' : 'Правила'}
                  </button>
                  <button className="btn-ghost text-2xs" onClick={() => setThresholdsFor(o)}>
                    Пороги
                  </button>
                  <button className="btn-ghost text-2xs" onClick={() => { setEditOffer(o); setShowModal(true); }}>
                    Изменить
                  </button>
                  <button className="btn-ghost text-2xs text-danger" onClick={() => handleDelete(o)}>
                    Удалить
                  </button>
                </div>
              </div>
            ))}
          </div>
          <div className="hidden overflow-x-auto md:block">
            <table className="w-full text-sm">
              <thead>
                <tr className="border-b border-border bg-elevated/50">
                  <th className="px-3 py-2 text-2xs uppercase tracking-wider text-muted text-left">Код</th>
                  <th className="px-3 py-2 text-2xs uppercase tracking-wider text-muted text-right">CPA</th>
                  <th className="px-3 py-2 text-2xs uppercase tracking-wider text-muted text-left">Страна</th>
                  <th className="px-3 py-2 text-2xs uppercase tracking-wider text-muted text-center">Статус</th>
                  <th className="px-3 py-2 text-2xs uppercase tracking-wider text-muted text-center">Пороги</th>
                  <th className="px-3 py-2 text-2xs uppercase tracking-wider text-muted text-right">Действия</th>
                </tr>
              </thead>
              <tbody>
                {offers.map((o) => (
                  <tr key={o.id} className="tr-hover border-b border-border">
                    <td className="px-3 py-2.5 font-mono text-accent">{o.code}</td>
                    <td className="px-3 py-2.5 text-right font-mono font-semibold text-primary">${Number(o.cpa_amount ?? o.cpa).toFixed(2)}</td>
                    <td className="px-3 py-2.5 text-secondary">{o.country_name || '—'}</td>
                    <td className="px-3 py-2.5 text-center">
                      <span className={o.is_active ? 'badge-success' : 'badge-neutral'}>
                        {o.is_active ? 'Активен' : 'Выкл.'}
                      </span>
                    </td>
                    <td className="px-3 py-2.5 text-center">
                      <button className="btn-ghost text-2xs" onClick={() => setThresholdsFor(o)}>
                        Настроить пороги
                      </button>
                    </td>
                    <td className="px-3 py-2.5">
                      <div className="flex justify-end gap-1.5">
                        <button className="btn-ghost text-2xs" onClick={() => openRules(o.id)} aria-expanded={editingId === o.id}>
                          {editingId === o.id ? 'Свернуть' : 'Правила'}
                        </button>
                        <button className="btn-ghost text-2xs" onClick={() => { setEditOffer(o); setShowModal(true); }}>
                          Изменить
                        </button>
                        <button className="btn-ghost text-2xs text-danger" onClick={() => handleDelete(o)}>
                          Удалить
                        </button>
                      </div>
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
          {offers.length === 0 && (
            <div className="py-12 text-center">
              <div className="text-2xl text-muted">○</div>
              <div className="mt-2 text-sm font-medium text-primary">Нет офферов</div>
              <div className="text-2xs text-muted">Создайте первый оффер, чтобы начать мониторинг</div>
            </div>
          )}
        </div>
      )}

      {/* Стоп-правила */}
      {editingId && (
        <div className="panel p-5 space-y-4 animate-fade-in">
          <h2 className="text-base font-semibold text-primary">
            Стоп-правила: {offers.find((o) => o.id === editingId)?.code || '—'}
          </h2>

          {rulesLoading ? (
            <div className="flex items-center gap-3 py-8 text-sm text-muted">
              <div className="h-5 w-5 animate-spin rounded-full border-2 border-accent border-t-transparent" />
              Загрузка правил...
            </div>
          ) : (
            <>
              <div className="space-y-3">
                {RULE_DEFS.map((rule) => <RuleBlock key={rule.key} rule={rule} rules={rules} setRules={setRules} />)}
              </div>

              <h3 className="pt-2 text-sm font-semibold text-primary">Диагностика CPM / частоты</h3>
              <div className="rounded-md border border-border bg-elevated/50 p-4">
                <p className="mb-3 text-2xs text-muted">CPM считается от медианы. Здесь только границы для частоты.</p>
                <div className="grid grid-cols-1 sm:grid-cols-2 gap-3">
                  {DIAGNOSTIC_FIELDS.map((field) => (
                    <div key={field.name}>
                      <label className="mb-1 block text-2xs font-semibold uppercase tracking-wider text-secondary" htmlFor={`rule-${field.name}`}>{field.label}</label>
                      <input id={`rule-${field.name}`} className={inputCls} type="text" inputMode="numeric" pattern="[0-9]*" value={rules[field.name] || ''} onChange={(e) => setRules({ ...rules, [field.name]: e.target.value.replace(/\D/g, '') })} />
                    </div>
                  ))}
                </div>
              </div>

              <div className="flex gap-2 pt-2">
                <button className="btn-primary" onClick={handleSaveRules} disabled={savingRules}>
                  {savingRules ? 'Сохранение...' : 'Сохранить правила'}
                </button>
                <button className="btn-secondary" onClick={() => setEditingId(null)}>Закрыть</button>
              </div>
            </>
          )}
        </div>
      )}

      {showModal && <OfferModal offer={editOffer} onSave={handleSaveOffer} onClose={() => { setShowModal(false); setEditOffer(null); }} />}
      {thresholdsFor && (
        <ThresholdsModal
          offer={thresholdsFor}
          onClose={() => setThresholdsFor(null)}
          onSaved={() => setToast({ message: 'Пороги обновлены', type: 'success' })}
        />
      )}
      {toast && <Toast message={toast.message} type={toast.type} onClose={() => setToast(null)} />}
    </div>
  );
}
