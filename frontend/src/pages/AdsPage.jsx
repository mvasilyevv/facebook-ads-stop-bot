import { useState } from 'react';

const FILTERS = ['Все', 'Активные', '⚠️ Warning', '🛑 Stop', '✅ Выключено'];

const FILTER_MAP = {
  'Все': null,
  'Активные': 'NORMAL',
  '⚠️ Warning': 'WARNING_SENT',
  '🛑 Stop': 'STOP_SENT',
  '✅ Выключено': 'DISABLED',
};

/* Демо-данные объявлений */
const DEMO_ADS = [
  { id: '1', fb_ad_id: '120214853', ad_name: 'Lead Gen — AU iPhone 15', campaign: 'AU_iOS_Mar24', offer: 'offer_au_42', status: 'ACTIVE', alert_state: 'STOP_SENT', spend: '0.15', clicks: 1, cpc: '0.15', leads: 0, regs: 0, deps: 0, rules: ['cpc_stop'] },
  { id: '2', fb_ad_id: '120214897', ad_name: 'Conversion — DE Samsung', campaign: 'DE_Android_Q1', offer: 'offer_de_17', status: 'ACTIVE', alert_state: 'WARNING_SENT', spend: '0.42', clicks: 3, cpc: '0.14', leads: 1, regs: 0, deps: 0, rules: ['cpl_stop'] },
  { id: '3', fb_ad_id: '120215012', ad_name: 'Traffic — US TikTok', campaign: 'US_Social_Mar', offer: 'offer_us_99', status: 'ACTIVE', alert_state: 'CLAIMED', spend: '3.20', clicks: 15, cpc: '0.21', leads: 5, regs: 5, deps: 0, rules: ['regs_no_dep_stop'] },
  { id: '4', fb_ad_id: '120215128', ad_name: 'Retarget — UK Offer42', campaign: 'UK_Retarget_Q1', offer: 'offer_uk_42', status: 'ACTIVE', alert_state: 'WARNING_SENT', spend: '2.85', clicks: 20, cpc: '0.14', leads: 8, regs: 3, deps: 0, rules: ['spend_no_dep_range'] },
  { id: '5', fb_ad_id: '120215299', ad_name: 'Brand — FR Promo', campaign: 'FR_Brand_Mar', offer: 'offer_fr_55', status: 'PAUSED', alert_state: 'DISABLED', spend: '4.10', clicks: 30, cpc: '0.14', leads: 10, regs: 4, deps: 1, rules: ['spend_with_dep_range'] },
  { id: '6', fb_ad_id: '120215387', ad_name: 'Engagement — IT Casino', campaign: 'IT_Engage_Q1', offer: 'offer_it_33', status: 'ACTIVE', alert_state: 'NORMAL', spend: '0.80', clicks: 8, cpc: '0.10', leads: 2, regs: 1, deps: 0, rules: [] },
  { id: '7', fb_ad_id: '120215445', ad_name: 'Video — ES Betting', campaign: 'ES_Video_Mar', offer: 'offer_es_77', status: 'ACTIVE', alert_state: 'NORMAL', spend: '0.45', clicks: 5, cpc: '0.09', leads: 1, regs: 0, deps: 0, rules: [] },
];

function alertStateBadge(state) {
  const map = {
    STOP_SENT: { cls: 'badge-danger', text: '🛑 Стоп' },
    WARNING_SENT: { cls: 'badge-warning', text: '⚠️ Предупр.' },
    CLAIMED: { cls: 'badge-info', text: '🔄 В работе' },
    DISABLED: { cls: 'badge-success', text: '✅ Выкл.' },
    NORMAL: { cls: 'badge-muted', text: '— Норма' },
  };
  const b = map[state] || map.NORMAL;
  return <span className={`badge ${b.cls}`}>{b.text}</span>;
}

export default function AdsPage() {
  const [activeFilter, setActiveFilter] = useState('Все');

  const filteredAds = DEMO_ADS.filter((ad) => {
    const filterState = FILTER_MAP[activeFilter];
    if (!filterState) return true;
    return ad.alert_state === filterState;
  });

  return (
    <div className="animate-in">
      <div className="page-header">
        <div>
          <h1 className="page-title">Объявления</h1>
          <div className="page-subtitle">
            Все объявления под наблюдением • {DEMO_ADS.length} шт.
          </div>
        </div>
      </div>

      <div className="table-container">
        <div className="table-header">
          <div className="table-title">Объявления ({filteredAds.length})</div>
          <div className="table-filters">
            {FILTERS.map((f) => (
              <button
                key={f}
                className={`filter-pill ${activeFilter === f ? 'active' : ''}`}
                onClick={() => setActiveFilter(f)}
              >
                {f}
              </button>
            ))}
          </div>
        </div>
        <table>
          <thead>
            <tr>
              <th>Объявление</th>
              <th>Кампания</th>
              <th>Оффер</th>
              <th>Статус</th>
              <th>Расход</th>
              <th>CPC</th>
              <th>Клики</th>
              <th>Лиды</th>
              <th>Реги</th>
              <th>Депы</th>
              <th>Правила</th>
            </tr>
          </thead>
          <tbody>
            {filteredAds.map((ad) => (
              <tr key={ad.id}>
                <td>
                  <div style={{ fontWeight: 500, color: 'var(--text-primary)' }}>{ad.ad_name}</div>
                  <div style={{ fontSize: 11, color: 'var(--text-muted)' }}>ID: {ad.fb_ad_id}</div>
                </td>
                <td>{ad.campaign}</td>
                <td><code style={{ color: 'var(--accent-purple)', fontSize: 12 }}>{ad.offer}</code></td>
                <td>{alertStateBadge(ad.alert_state)}</td>
                <td style={{ fontWeight: 600 }}>${ad.spend}</td>
                <td>${ad.cpc}</td>
                <td>{ad.clicks}</td>
                <td>{ad.leads}</td>
                <td>{ad.regs}</td>
                <td>{ad.deps}</td>
                <td>
                  {ad.rules.map((r) => (
                    <span key={r} className="badge badge-danger" style={{ marginRight: 4, fontSize: 10 }}>{r}</span>
                  ))}
                  {ad.rules.length === 0 && <span style={{ color: 'var(--text-muted)' }}>—</span>}
                </td>
              </tr>
            ))}
          </tbody>
        </table>
        {filteredAds.length === 0 && (
          <div className="empty-state">
            <div className="empty-state-icon">📭</div>
            <div className="empty-state-title">Нет объявлений</div>
            <div>По выбранному фильтру объявлений не найдено</div>
          </div>
        )}
      </div>
    </div>
  );
}
