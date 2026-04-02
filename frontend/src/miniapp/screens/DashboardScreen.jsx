import { useEffect, useState } from 'react';
import { getDashboard } from '../api.js';
import MiniAlert from '../components/MiniAlert.jsx';

export default function DashboardScreen({ onOpenAlerts }) {
  const [data, setData] = useState(null);
  const [error, setError] = useState(null);

  async function load() {
    try {
      setData(await getDashboard());
    } catch (e) {
      setError(e.message);
    }
  }

  useEffect(() => {
    load();
    const t = setInterval(load, 15000);
    return () => clearInterval(t);
  }, []);

  if (!data)
    return (
      <div style={{ padding: '16px', color: 'var(--text-muted)' }}>
        {error || 'Загрузка...'}
      </div>
    );

  return (
    <div style={{ padding: '12px' }}>
      <div
        style={{
          display: 'grid',
          gridTemplateColumns: '1fr 1fr',
          gap: '8px',
          marginBottom: '16px',
        }}
      >
        {[
          {
            label: 'Расход',
            val: data.spend_today != null ? `$${Number(data.spend_today).toFixed(2)}` : '—',
            color: 'var(--accent-teal)',
          },
          { label: 'Лиды', val: data.leads_today ?? '—', color: 'var(--text-primary)' },
          {
            label: 'Депозиты',
            val: data.deposits_today ?? '—',
            color: data.deposits_today === 0 ? 'var(--accent-crimson)' : 'var(--accent-teal)',
          },
          {
            label: 'CPA',
            val: data.cpa_today != null ? `$${Number(data.cpa_today).toFixed(2)}` : '—',
            color: 'var(--text-primary)',
          },
        ].map(({ label, val, color }) => (
          <div
            key={label}
            style={{
              background: 'var(--bg-secondary)',
              borderRadius: '6px',
              padding: '10px',
              textAlign: 'center',
            }}
          >
            <div style={{ fontSize: '18px', fontWeight: 700, color }}>{val}</div>
            <div style={{ fontSize: '11px', color: 'var(--text-muted)' }}>{label}</div>
          </div>
        ))}
      </div>
      {data.top3_alerts?.length > 0 && (
        <div style={{ marginBottom: '12px' }}>
          {data.top3_alerts.map((a) => (
            <MiniAlert key={a.fb_ad_id} alert={a} onClick={() => {}} />
          ))}
        </div>
      )}
      <button
        onClick={onOpenAlerts}
        style={{
          width: '100%',
          padding: '10px',
          background: 'var(--bg-secondary)',
          color: 'var(--accent-teal)',
          border: '1px solid var(--border-color)',
          borderRadius: '6px',
          fontSize: '13px',
          cursor: 'pointer',
        }}
      >
        Все сигналы →
      </button>
    </div>
  );
}
