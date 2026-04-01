import { useState, useEffect, useCallback } from 'react';
import { getOffers, createOffer, updateOffer, deleteOffer, getOfferRules, updateOfferRules } from '../api.js';

/* Тогл-переключатель с ARIA-атрибутами */
function Toggle({ on, onChange, label }) {
  return (
    <button
      className={`toggle-switch ${on ? 'on' : ''}`}
      onClick={() => onChange(!on)}
      type="button"
      role="switch"
      aria-checked={on}
      aria-label={label}
    />
  );
}

/* Всплывающее уведомление */
function Toast({ message, type, onClose }) {
  useEffect(() => {
    const timer = setTimeout(onClose, 3000);
    return () => clearTimeout(timer);
  }, [onClose]);
  return (
    <div className={`toast toast-${type}`} role="alert">
      {message}
    </div>
  );
}

/* Модалка создания/редактирования оффера */
function OfferModal({ offer, onSave, onClose }) {
  const [form, setForm] = useState({
    code: offer?.code || '',
    name: offer?.name || '',
    cpa: offer?.cpa_amount || offer?.cpa || '',
    is_active: offer?.is_active ?? true,
  });
  const [saving, setSaving] = useState(false);

  const handleSubmit = async (e) => {
    e.preventDefault();
    setSaving(true);
    try {
      await onSave({
        code: form.code,
        name: form.name,
        cpa_amount: parseFloat(form.cpa) || 0,
        is_active: form.is_active,
      });
    } finally {
      setSaving(false);
    }
  };

  return (
    <div className="modal-overlay" onClick={onClose} role="dialog" aria-modal="true" aria-label={offer ? 'Редактировать оффер' : 'Создать оффер'}>
      <div className="modal" onClick={(e) => e.stopPropagation()}>
        <h2 className="modal-title">{offer ? 'Редактировать оффер' : 'Новый оффер'}</h2>
        <form onSubmit={handleSubmit}>
          <div className="form-group" style={{ marginBottom: 16 }}>
            <label className="form-label" htmlFor="offer-code">
              Код оффера
            </label>
            <input
              id="offer-code"
              className="form-input"
              type="text"
              placeholder="OFFER_AU_42"
              value={form.code}
              onChange={(e) => setForm({ ...form, code: e.target.value.toUpperCase() })}
              required
              disabled={!!offer}
            />
            <div className="form-hint">
              Код используется для сопоставления — ищется в названии кампании / объявления
            </div>
          </div>
          <div className="form-group" style={{ marginBottom: 16 }}>
            <label className="form-label" htmlFor="offer-name">
              Название
            </label>
            <input
              id="offer-name"
              className="form-input"
              type="text"
              placeholder="Australia — iPhone 15"
              value={form.name}
              onChange={(e) => setForm({ ...form, name: e.target.value })}
              required
            />
          </div>
          <div className="form-group" style={{ marginBottom: 16 }}>
            <label className="form-label" htmlFor="offer-cpa">
              CPA ($)
            </label>
            <input
              id="offer-cpa"
              className="form-input"
              type="number"
              step="0.01"
              min="0"
              placeholder="5.00"
              value={form.cpa}
              onChange={(e) => setForm({ ...form, cpa: e.target.value })}
              required
            />
          </div>
          <div style={{ display: 'flex', alignItems: 'center', gap: 12, marginBottom: 16 }}>
            <Toggle on={form.is_active} onChange={(v) => setForm({ ...form, is_active: v })} label="Оффер активен" />
            <span style={{ color: 'var(--text-secondary)', fontSize: 14 }}>
              {form.is_active ? 'Активен' : 'Выключен'}
            </span>
          </div>
          <div className="modal-actions">
            <button type="button" className="btn btn-outline" onClick={onClose}>
              Отмена
            </button>
            <button type="submit" className="btn btn-primary" disabled={saving}>
              {saving ? 'Сохранение...' : offer ? 'Сохранить' : 'Создать'}
            </button>
          </div>
        </form>
      </div>
    </div>
  );
}

/* Шесть стоп-правил */
const RULE_DEFS = [
  { key: 'cpc_percent', title: 'Правило 1: CPC > X% CPA', hint: 'Стоп при стоимости клика выше установленного % от целевого CPA', fields: [{ name: 'cpc_percent_stop', label: 'Процент стопа (%)', type: 'number' }] },
  { key: 'cpl_percent', title: 'Правило 2: CPL > X% CPA', hint: 'Стоп при стоимости лида выше установленного % от целевого CPA', fields: [{ name: 'cpl_percent_stop', label: 'Процент стопа (%)', type: 'number' }] },
  { key: 'cpr_percent', title: 'Правило 3: CPR > X% CPA', hint: 'Стоп при стоимости регистрации выше установленного % от целевого CPA', fields: [{ name: 'cpr_percent_stop', label: 'Процент стопа (%)', type: 'number' }] },
  { key: 'regs_no_dep', title: 'Правило 4: N регистраций без депозитов', hint: 'Стоп при достижении заданного количества регистраций подряд без депозита', fields: [{ name: 'regs_no_dep_stop_count', label: 'Количество регистраций', type: 'number' }] },
  { key: 'spend_no_dep', title: 'Правило 5: Расход без депозитов', hint: 'Стоп при расходе в диапазоне % от CPA без депозитов', fields: [{ name: 'spend_no_dep_from_percent', label: 'Расход от (% CPA)', type: 'number' }, { name: 'spend_no_dep_to_percent', label: 'Расход до (% CPA)', type: 'number' }] },
  { key: 'spend_with_dep', title: 'Правило 6: Расход с депозитом', hint: 'Стоп при расходе в диапазоне % от CPA с депозитом', fields: [{ name: 'spend_with_dep_from_percent', label: 'Расход от (% CPA)', type: 'number' }, { name: 'spend_with_dep_to_percent', label: 'Расход до (% CPA)', type: 'number' }] },
];

const EARLY_SIGNAL_DEFS = [
  {
    key: 'early_outbound_ctr_signal',
    title: 'Ранний сигнал 1: слабый CTR исходящих кликов',
    hint: 'Предупреждение при низком CTR кликов уходящих на лендинг',
    fields: [
      { name: 'early_outbound_ctr_signal_min_percent', label: 'Минимальный CTR исходящих кликов (%)', type: 'number' },
      { name: 'early_outbound_ctr_signal_min_spend_percent', label: 'Минимальный расход для проверки (% CPA)', type: 'number' },
    ],
  },
  {
    key: 'early_lpv_ratio_signal',
    title: 'Ранний сигнал 2: слабая доходимость до лендинга',
    hint: 'Предупреждение при низкой доле просмотров лендинга от исходящих кликов',
    fields: [
      { name: 'early_lpv_ratio_signal_min_percent', label: 'Минимальная доля LPV (%)', type: 'number' },
      { name: 'early_lpv_ratio_signal_min_outbound_clicks', label: 'Минимум исходящих кликов для проверки', type: 'number' },
    ],
  },
  {
    key: 'early_cost_per_lpv_signal',
    title: 'Ранний сигнал 3: дорогой просмотр лендинга',
    hint: 'Предупреждение при превышении целевой цены за просмотр лендинга',
    fields: [
      { name: 'early_cost_per_lpv_signal_percent_of_cpa', label: 'Лимит цены LPV (% CPA)', type: 'number' },
      { name: 'early_cost_per_lpv_signal_min_views', label: 'Минимум LPV для проверки', type: 'number' },
    ],
  },
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
  early_outbound_ctr_signal_enabled: true, early_outbound_ctr_signal_min_percent: '0.80', early_outbound_ctr_signal_min_spend_percent: '5',
  early_lpv_ratio_signal_enabled: true, early_lpv_ratio_signal_min_percent: '60', early_lpv_ratio_signal_min_outbound_clicks: '5',
  early_cost_per_lpv_signal_enabled: true, early_cost_per_lpv_signal_percent_of_cpa: '5', early_cost_per_lpv_signal_min_views: '2',
  frequency_elevated_threshold: '2',
  frequency_critical_threshold: '3',
};

function FieldHint({ fieldName, value }) {
  const hints = {
    cpc_percent_stop: `Стоп при CPC выше ${value || '—'}% от целевого CPA`,
    cpl_percent_stop: `Стоп при CPL выше ${value || '—'}% от целевого CPA`,
    cpr_percent_stop: `Стоп при CPR выше ${value || '—'}% от целевого CPA`,
    regs_no_dep_stop_count: `Стоп при ${value || '—'} регистрациях без депозитов подряд`,
    spend_no_dep_from_percent: 'Начальная граница расхода без депозитов (% от CPA)',
    spend_no_dep_to_percent: 'Верхняя граница расхода без депозитов (% от CPA)',
    spend_with_dep_from_percent: 'Начальная граница расхода с депозитом (% от CPA)',
    spend_with_dep_to_percent: 'Верхняя граница расхода с депозитом (% от CPA)',
    early_outbound_ctr_signal_min_percent: `Ранний сигнал при CTR исходящих кликов ниже ${value || '—'}%`,
    early_outbound_ctr_signal_min_spend_percent: `Проверка ранних сигналов только при расходе выше ${value || '—'}% от CPA`,
    early_lpv_ratio_signal_min_percent: `Ранний сигнал при доле LPV ниже ${value || '—'}% от исходящих кликов`,
    early_lpv_ratio_signal_min_outbound_clicks: `Проверка только при минимум ${value || '—'} исходящих кликов`,
    early_cost_per_lpv_signal_percent_of_cpa: `Ранний сигнал при цене LPV выше ${value || '—'}% от CPA`,
    early_cost_per_lpv_signal_min_views: `Проверка только при минимум ${value || '—'} просмотров лендинга`,
  };
  return hints[fieldName] ? (
    <p className="text-xs text-gray-400 mt-1">{hints[fieldName]}</p>
  ) : null;
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

  /* Загрузка офферов */
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

  /* Загрузка правил при открытии редактора */
  const openRules = useCallback(async (offerId) => {
    if (editingId === offerId) {
      setEditingId(null);
      return;
    }
    setEditingId(offerId);
    setRulesLoading(true);
    try {
      const data = await getOfferRules(offerId);
      if (data && typeof data === 'object') {
        setRules({ ...DEFAULT_RULES, ...data });
      } else {
        setRules(DEFAULT_RULES);
      }
    } catch {
      setRules(DEFAULT_RULES);
    } finally {
      setRulesLoading(false);
    }
  }, [editingId]);

  /* Сохранение правил */
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

  /* Создание / редактирование оффера */
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

  /* Удаление оффера */
  const handleDelete = async (offer) => {
    if (!confirm(`Удалить оффер "${offer.name}"?`)) return;
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
    <div className="animate-in">
      <div className="page-header">
        <div>
          <h1 className="page-title">Офферы</h1>
          <div className="page-subtitle">
            Управление офферами и стоп-правилами • {offers.length} шт.
          </div>
        </div>
        <button
          className="btn btn-primary"
          onClick={() => {
            setEditOffer(null);
            setShowModal(true);
          }}
        >
          + Добавить оффер
        </button>
      </div>

      {/* Состояние загрузки */}
      {loading && (
        <div className="loading-state">
          <div className="spinner" />
          <div>Загрузка офферов...</div>
        </div>
      )}

      {/* Ошибка */}
      {error && !loading && (
        <div className="error-state">
          <div className="error-state-text">{error}</div>
          <button className="btn btn-outline btn-sm" onClick={fetchOffers}>
            Повторить
          </button>
        </div>
      )}

      {/* Таблица офферов */}
      {!loading && !error && (
        <section aria-label="Список офферов" className="table-container" style={{ marginBottom: 24 }}>
          <div className="table-scroll">
            <table>
              <thead>
                <tr>
                  <th scope="col">Код</th>
                  <th scope="col">Название</th>
                  <th scope="col">CPA</th>
                  <th scope="col">Статус</th>
                  <th scope="col">Правила</th>
                  <th scope="col" className="actions-col">Действия</th>
                </tr>
              </thead>
              <tbody>
                {offers.map((o) => (
                  <tr key={o.id}>
                    <td>
                      <code style={{ color: 'var(--accent-purple)' }}>{o.code}</code>
                    </td>
                    <td style={{ fontWeight: 500, color: 'var(--text-primary)' }}>{o.name}</td>
                    <td style={{ fontWeight: 600 }}>${Number(o.cpa_amount ?? o.cpa).toFixed(2)}</td>
                    <td>
                      <span className={`badge ${o.is_active ? 'badge-success' : 'badge-muted'}`}>
                        {o.is_active ? 'Активен' : 'Выкл.'}
                      </span>
                    </td>
                    <td style={{ fontSize: '11px', color: 'var(--text-muted)' }}>
                      {editingId === o.id ? '↓ развёрнуто' : '✎ натсройте'}
                    </td>
                    <td className="actions-col">
                      <div style={{ display: 'flex', gap: 8 }}>
                        <button
                          className="btn btn-outline btn-sm"
                          onClick={() => openRules(o.id)}
                          aria-expanded={editingId === o.id}
                          title="Настроить правила"
                        >
                          {editingId === o.id ? 'Свернуть' : 'Правила'}
                        </button>
                        <button
                          className="btn btn-outline btn-sm"
                          onClick={() => {
                            setEditOffer(o);
                            setShowModal(true);
                          }}
                          aria-label={`Редактировать ${o.name}`}
                          title="Редактировать"
                        >
                          Изменить
                        </button>
                        <button
                          className="btn btn-outline btn-sm"
                          onClick={() => handleDelete(o)}
                          aria-label={`Удалить ${o.name}`}
                          title="Удалить"
                          style={{ color: 'var(--accent-red)' }}
                        >
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
            <div className="empty-state">
              <div className="empty-state-icon">🎯</div>
              <div className="empty-state-title">Нет офферов</div>
              <div>Создайте первый оффер, чтобы начать мониторинг</div>
            </div>
          )}
        </section>
      )}

      {/* Конфигурация стоп-правил для выбранного оффера */}
      {editingId && (
        <section className="form-section animate-in" aria-label="Стоп-правила">
          <div className="form-section-title">
            Стоп-правила для: {offers.find((o) => o.id === editingId)?.name || '—'}
          </div>

          {rulesLoading ? (
            <div className="loading-state" style={{ padding: '24px 0' }}>
              <div className="spinner" />
              <div>Загрузка правил...</div>
            </div>
          ) : (
            <>
              {RULE_DEFS.map((rule) => (
                <div
                  key={rule.key}
                  className="form-section"
                  style={{ background: 'var(--bg-secondary)', marginBottom: 12 }}
                >
                  <div style={{ display: 'flex', alignItems: 'flex-start', justifyContent: 'space-between', marginBottom: 12, gap: 12 }}>
                    <div style={{ flex: 1, minWidth: 0 }}>
                      <strong style={{ display: 'block', marginBottom: 4 }}>{rule.title}</strong>
                      {rule.hint && <p style={{ fontSize: '12px', color: 'var(--text-secondary)', margin: 0 }}>{rule.hint}</p>}
                    </div>
                    <Toggle
                      on={rules[`${rule.key}_enabled`]}
                      onChange={(v) => setRules({ ...rules, [`${rule.key}_enabled`]: v })}
                      label={`Включить ${rule.title}`}
                    />
                  </div>
                  <div className="form-grid">
                    {rule.fields.map((field) => (
                      <div className="form-group" key={field.name}>
                        <label className="form-label" htmlFor={`rule-${field.name}`}>
                          {field.label}
                        </label>
                        <input
                          id={`rule-${field.name}`}
                          className="form-input"
                          type={field.type}
                          value={rules[field.name] || ''}
                          onChange={(e) => setRules({ ...rules, [field.name]: e.target.value })}
                          disabled={!rules[`${rule.key}_enabled`]}
                        />
                        <FieldHint fieldName={field.name} value={rules[field.name]} />
                      </div>
                    ))}
                  </div>
                </div>
              ))}

              <div className="form-section-title" style={{ marginTop: 20 }}>
                Ранние сигналы до лидов
              </div>
              {EARLY_SIGNAL_DEFS.map((rule) => (
                <div
                  key={rule.key}
                  className="form-section"
                  style={{ background: 'var(--bg-secondary)', marginBottom: 12 }}
                >
                  <div style={{ display: 'flex', alignItems: 'flex-start', justifyContent: 'space-between', marginBottom: 12, gap: 12 }}>
                    <div style={{ flex: 1, minWidth: 0 }}>
                      <strong style={{ display: 'block', marginBottom: 4 }}>{rule.title}</strong>
                      {rule.hint && <p style={{ fontSize: '12px', color: 'var(--text-secondary)', margin: 0 }}>{rule.hint}</p>}
                    </div>
                    <Toggle
                      on={rules[`${rule.key}_enabled`]}
                      onChange={(v) => setRules({ ...rules, [`${rule.key}_enabled`]: v })}
                      label={`Включить ${rule.title}`}
                    />
                  </div>
                  <div className="form-grid">
                    {rule.fields.map((field) => (
                      <div className="form-group" key={field.name}>
                        <label className="form-label" htmlFor={`rule-${field.name}`}>
                          {field.label}
                        </label>
                        <input
                          id={`rule-${field.name}`}
                          className="form-input"
                          type={field.type}
                          value={rules[field.name] || ''}
                          onChange={(e) => setRules({ ...rules, [field.name]: e.target.value })}
                          disabled={!rules[`${rule.key}_enabled`]}
                        />
                        <FieldHint fieldName={field.name} value={rules[field.name]} />
                      </div>
                    ))}
                  </div>
                </div>
              ))}

              <div className="form-section-title" style={{ marginTop: 20 }}>
                Диагностика CPM / частоты
              </div>
              <div className="form-section" style={{ background: 'var(--bg-secondary)', marginBottom: 12 }}>
                <div style={{ color: 'var(--text-secondary)', fontSize: 13, marginBottom: 12 }}>
                  CPM считается динамически от медианы активных объявлений оффера. Здесь настраиваются только границы для частоты.
                </div>
                <div className="form-grid">
                  {DIAGNOSTIC_FIELDS.map((field) => (
                    <div className="form-group" key={field.name}>
                      <label className="form-label" htmlFor={`rule-${field.name}`}>
                        {field.label}
                      </label>
                      <input
                        id={`rule-${field.name}`}
                        className="form-input"
                        type={field.type}
                        value={rules[field.name] || ''}
                        onChange={(e) => setRules({ ...rules, [field.name]: e.target.value })}
                      />
                    </div>
                  ))}
                </div>
              </div>

              <div style={{ display: 'flex', gap: 12, marginTop: 16 }}>
                <button className="btn btn-primary" onClick={handleSaveRules} disabled={savingRules}>
                  {savingRules ? 'Сохранение...' : 'Сохранить правила'}
                </button>
                <button className="btn btn-outline" onClick={() => setEditingId(null)}>
                  Закрыть
                </button>
              </div>
            </>
          )}
        </section>
      )}

      {/* Модалка создания / редактирования */}
      {showModal && (
        <OfferModal
          offer={editOffer}
          onSave={handleSaveOffer}
          onClose={() => {
            setShowModal(false);
            setEditOffer(null);
          }}
        />
      )}

      {/* Toast-уведомления */}
      {toast && <Toast message={toast.message} type={toast.type} onClose={() => setToast(null)} />}
    </div>
  );
}
