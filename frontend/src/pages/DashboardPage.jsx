import { useState, useEffect } from 'react';

/* Демо-данные для визуализации (пока API не подключен к БД) */
const DEMO_STATS = {
  total_ads_monitored: 47,
  ads_in_warning: 5,
  ads_in_stop: 2,
  ads_disabled: 8,
  total_spend: '2,340.50',
  active_offers: 3,
  pending_disable_tasks: 1,
  last_scan_at: new Date().toLocaleTimeString('ru'),
};

const DEMO_ALERTS = [
  { id: '1', ad_name: 'Lead Gen — AU iPhone 15', stage: 'STOP', state: 'STOP_SENT', rule: 'cpc_stop', spend: '0.15', time: '2 мин назад' },
  { id: '2', ad_name: 'Conversion — DE Samsung', stage: 'WARNING', state: 'WARNING_SENT', rule: 'cpl_stop', spend: '0.42', time: '5 мин назад' },
  { id: '3', ad_name: 'Traffic — US TikTok', stage: 'STOP', state: 'CLAIMED', rule: 'regs_no_dep_stop', spend: '3.20', time: '12 мин назад' },
  { id: '4', ad_name: 'Retarget — UK Offer42', stage: 'WARNING', state: 'WARNING_SENT', rule: 'spend_no_dep_range', spend: '2.85', time: '18 мин назад' },
  { id: '5', ad_name: 'Brand — FR Promo', stage: 'STOP', state: 'DISABLED', rule: 'spend_with_dep_range', spend: '4.10', time: '25 мин назад' },
];

const SPEND_BARS = Array.from({ length: 24 }, (_, i) =>
  Math.floor(20 + Math.random() * 80 + (i > 8 && i < 20 ? 40 : 0))
);

function stageBadge(stage) {
  if (stage === 'STOP') return <span className="badge badge-danger">🛑 STOP</span>;
  return <span className="badge badge-warning">⚠️ WARNING</span>;
}

function stateBadge(state) {
  const map = {
    STOP_SENT: { cls: 'badge-danger', text: '⏳ Ждёт' },
    WARNING_SENT: { cls: 'badge-warning', text: '⚠️ Предупр.' },
    CLAIMED: { cls: 'badge-info', text: '🔄 В работе' },
    DISABLED: { cls: 'badge-success', text: '✅ Выкл.' },
    NORMAL: { cls: 'badge-muted', text: '— Норма' },
  };
  const b = map[state] || map.NORMAL;
  return <span className={`badge ${b.cls}`}>{b.text}</span>;
}

export default function DashboardPage() {
  const s = DEMO_STATS;

  return (
    <div className="animate-in">
      <div className="page-header">
        <div>
          <h1 className="page-title">Dashboard</h1>
          <div className="page-subtitle">
            Мониторинг объявлений • Последний скан: {s.last_scan_at}
          </div>
        </div>
        <div style={{ display: 'flex', gap: 8 }}>
          <span className="badge badge-success pulse">● Активен</span>
        </div>
      </div>

      {/* Stat Cards */}
      <div className="stats-grid">
        <div className="stat-card info">
          <div className="stat-label">Всего объявлений</div>
          <div className="stat-value info">{s.total_ads_monitored}</div>
        </div>
        <div className="stat-card warning">
          <div className="stat-label">Предупреждения</div>
          <div className="stat-value warning">{s.ads_in_warning}</div>
        </div>
        <div className="stat-card danger">
          <div className="stat-label">Стоп-алерты</div>
          <div className="stat-value danger">{s.ads_in_stop}</div>
        </div>
        <div className="stat-card success">
          <div className="stat-label">Выключено</div>
          <div className="stat-value success">{s.ads_disabled}</div>
        </div>
      </div>

      {/* Spend Chart */}
      <div className="chart-container">
        <div className="chart-title">Расход за 24 часа — ${s.total_spend}</div>
        <div className="chart-canvas">
          {SPEND_BARS.map((h, i) => (
            <div
              key={i}
              className="chart-bar"
              style={{ height: `${h}%` }}
              title={`${i}:00 — $${(Math.random() * 100).toFixed(2)}`}
            />
          ))}
        </div>
      </div>

      {/* Recent Alerts */}
      <div className="table-container">
        <div className="table-header">
          <div className="table-title">Последние алерты</div>
          <div className="table-filters">
            <button className="filter-pill active">Все</button>
            <button className="filter-pill">⚠️ Warning</button>
            <button className="filter-pill">🛑 Stop</button>
          </div>
        </div>
        <table>
          <thead>
            <tr>
              <th>Объявление</th>
              <th>Стадия</th>
              <th>Статус</th>
              <th>Правило</th>
              <th>Расход</th>
              <th>Время</th>
            </tr>
          </thead>
          <tbody>
            {DEMO_ALERTS.map((a) => (
              <tr key={a.id}>
                <td style={{ color: 'var(--text-primary)', fontWeight: 500 }}>{a.ad_name}</td>
                <td>{stageBadge(a.stage)}</td>
                <td>{stateBadge(a.state)}</td>
                <td><code style={{ color: 'var(--accent-purple)', fontSize: 12 }}>{a.rule}</code></td>
                <td>${a.spend}</td>
                <td style={{ color: 'var(--text-muted)' }}>{a.time}</td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </div>
  );
}
