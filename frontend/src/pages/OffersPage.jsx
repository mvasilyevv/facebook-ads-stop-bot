import { useState } from 'react';

const DEMO_OFFERS = [
  { id: '1', code: 'offer_au_42', name: 'Australia — iPhone 15', cpa: '5.00', is_active: true },
  { id: '2', code: 'offer_de_17', name: 'Germany — Samsung S24', cpa: '8.00', is_active: true },
  { id: '3', code: 'offer_us_99', name: 'USA — TikTok Promo', cpa: '3.50', is_active: true },
  { id: '4', code: 'offer_uk_42', name: 'UK — Offer42 Casino', cpa: '6.00', is_active: false },
];

const DEFAULT_RULES = {
  cpc_enabled: true, cpc_percent: '2',
  cpl_enabled: true, cpl_percent: '10',
  cpr_enabled: true, cpr_percent: '20',
  regs_no_dep_enabled: true, regs_no_dep_count: '5',
  spend_no_dep_enabled: true, spend_no_dep_from: '50', spend_no_dep_to: '70',
  spend_with_dep_enabled: true, spend_with_dep_from: '70', spend_with_dep_to: '90',
};

function Toggle({ on, onChange }) {
  return (
    <button
      className={`toggle-switch ${on ? 'on' : ''}`}
      onClick={() => onChange(!on)}
      type="button"
    />
  );
}

export default function OffersPage() {
  const [offers] = useState(DEMO_OFFERS);
  const [editingId, setEditingId] = useState(null);
  const [rules, setRules] = useState(DEFAULT_RULES);

  return (
    <div className="animate-in">
      <div className="page-header">
        <div>
          <h1 className="page-title">Офферы</h1>
          <div className="page-subtitle">
            Управление офферами и стоп-правилами • {offers.length} шт.
          </div>
        </div>
        <button className="btn btn-primary">➕ Добавить оффер</button>
      </div>

      {/* Список офферов */}
      <div className="table-container" style={{ marginBottom: 24 }}>
        <table>
          <thead>
            <tr>
              <th>Код</th>
              <th>Название</th>
              <th>CPA</th>
              <th>Статус</th>
              <th>Действия</th>
            </tr>
          </thead>
          <tbody>
            {offers.map((o) => (
              <tr key={o.id}>
                <td><code style={{ color: 'var(--accent-purple)' }}>{o.code}</code></td>
                <td style={{ fontWeight: 500, color: 'var(--text-primary)' }}>{o.name}</td>
                <td style={{ fontWeight: 600 }}>${o.cpa}</td>
                <td>
                  <span className={`badge ${o.is_active ? 'badge-success' : 'badge-muted'}`}>
                    {o.is_active ? '● Активен' : '○ Выкл.'}
                  </span>
                </td>
                <td>
                  <button
                    className="btn btn-outline btn-sm"
                    onClick={() => setEditingId(editingId === o.id ? null : o.id)}
                  >
                    {editingId === o.id ? 'Свернуть' : '⚙️ Правила'}
                  </button>
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>

      {/* Конфигурация правил для выбранного оффера */}
      {editingId && (
        <div className="form-section animate-in">
          <div className="form-section-title">
            🎯 Стоп-правила для: {offers.find((o) => o.id === editingId)?.name}
          </div>

          {/* Правило 1: CPC */}
          <div className="form-section" style={{ background: 'var(--bg-secondary)', marginBottom: 12 }}>
            <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between', marginBottom: 12 }}>
              <strong>Правило 1: CPC {'>'} X% CPA</strong>
              <Toggle on={rules.cpc_enabled} onChange={(v) => setRules({ ...rules, cpc_enabled: v })} />
            </div>
            <div className="form-grid">
              <div className="form-group">
                <label className="form-label">Процент стопа (%)</label>
                <input className="form-input" type="number" value={rules.cpc_percent}
                  onChange={(e) => setRules({ ...rules, cpc_percent: e.target.value })} />
              </div>
            </div>
          </div>

          {/* Правило 2: CPL */}
          <div className="form-section" style={{ background: 'var(--bg-secondary)', marginBottom: 12 }}>
            <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between', marginBottom: 12 }}>
              <strong>Правило 2: CPL {'>'} X% CPA</strong>
              <Toggle on={rules.cpl_enabled} onChange={(v) => setRules({ ...rules, cpl_enabled: v })} />
            </div>
            <div className="form-grid">
              <div className="form-group">
                <label className="form-label">Процент стопа (%)</label>
                <input className="form-input" type="number" value={rules.cpl_percent}
                  onChange={(e) => setRules({ ...rules, cpl_percent: e.target.value })} />
              </div>
            </div>
          </div>

          {/* Правило 3: CPR */}
          <div className="form-section" style={{ background: 'var(--bg-secondary)', marginBottom: 12 }}>
            <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between', marginBottom: 12 }}>
              <strong>Правило 3: CPR {'>'} X% CPA</strong>
              <Toggle on={rules.cpr_enabled} onChange={(v) => setRules({ ...rules, cpr_enabled: v })} />
            </div>
            <div className="form-grid">
              <div className="form-group">
                <label className="form-label">Процент стопа (%)</label>
                <input className="form-input" type="number" value={rules.cpr_percent}
                  onChange={(e) => setRules({ ...rules, cpr_percent: e.target.value })} />
              </div>
            </div>
          </div>

          {/* Правило 4: N рег без депов */}
          <div className="form-section" style={{ background: 'var(--bg-secondary)', marginBottom: 12 }}>
            <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between', marginBottom: 12 }}>
              <strong>Правило 4: N регистраций без депозитов</strong>
              <Toggle on={rules.regs_no_dep_enabled} onChange={(v) => setRules({ ...rules, regs_no_dep_enabled: v })} />
            </div>
            <div className="form-grid">
              <div className="form-group">
                <label className="form-label">Количество регистраций</label>
                <input className="form-input" type="number" value={rules.regs_no_dep_count}
                  onChange={(e) => setRules({ ...rules, regs_no_dep_count: e.target.value })} />
              </div>
            </div>
          </div>

          {/* Правило 5: Расход без депа */}
          <div className="form-section" style={{ background: 'var(--bg-secondary)', marginBottom: 12 }}>
            <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between', marginBottom: 12 }}>
              <strong>Правило 5: Расход без депов</strong>
              <Toggle on={rules.spend_no_dep_enabled} onChange={(v) => setRules({ ...rules, spend_no_dep_enabled: v })} />
            </div>
            <div className="form-grid">
              <div className="form-group">
                <label className="form-label">Расход от (% CPA)</label>
                <input className="form-input" type="number" value={rules.spend_no_dep_from}
                  onChange={(e) => setRules({ ...rules, spend_no_dep_from: e.target.value })} />
              </div>
              <div className="form-group">
                <label className="form-label">Расход до (% CPA)</label>
                <input className="form-input" type="number" value={rules.spend_no_dep_to}
                  onChange={(e) => setRules({ ...rules, spend_no_dep_to: e.target.value })} />
              </div>
            </div>
          </div>

          {/* Правило 6: Расход с депом */}
          <div className="form-section" style={{ background: 'var(--bg-secondary)', marginBottom: 12 }}>
            <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between', marginBottom: 12 }}>
              <strong>Правило 6: Расход с депозитом</strong>
              <Toggle on={rules.spend_with_dep_enabled} onChange={(v) => setRules({ ...rules, spend_with_dep_enabled: v })} />
            </div>
            <div className="form-grid">
              <div className="form-group">
                <label className="form-label">Расход от (% CPA)</label>
                <input className="form-input" type="number" value={rules.spend_with_dep_from}
                  onChange={(e) => setRules({ ...rules, spend_with_dep_from: e.target.value })} />
              </div>
              <div className="form-group">
                <label className="form-label">Расход до (% CPA)</label>
                <input className="form-input" type="number" value={rules.spend_with_dep_to}
                  onChange={(e) => setRules({ ...rules, spend_with_dep_to: e.target.value })} />
              </div>
            </div>
          </div>

          <div style={{ display: 'flex', gap: 12, marginTop: 16 }}>
            <button className="btn btn-primary">💾 Сохранить правила</button>
            <button className="btn btn-outline" onClick={() => setEditingId(null)}>Отмена</button>
          </div>
        </div>
      )}
    </div>
  );
}
