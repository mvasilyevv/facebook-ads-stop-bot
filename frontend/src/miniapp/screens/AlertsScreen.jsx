import { useEffect, useState } from 'react';
import { getAlerts } from '../api.js';
import MiniAlert from '../components/MiniAlert.jsx';

export default function AlertsScreen({ onSelectAd }) {
  const [alerts, setAlerts] = useState([]);
  const [loading, setLoading] = useState(true);

  useEffect(() => {
    getAlerts()
      .then((r) => {
        setAlerts(Array.isArray(r) ? r : []);
        setLoading(false);
      })
      .catch(() => setLoading(false));
  }, []);

  if (loading)
    return (
      <div style={{ padding: '16px', color: 'var(--text-muted)' }}>Загрузка...</div>
    );

  return (
    <div style={{ padding: '12px' }}>
      <h2
        style={{
          margin: '0 0 12px',
          fontSize: '14px',
          fontWeight: 600,
          color: 'var(--text-muted)',
          textTransform: 'uppercase',
        }}
      >
        Активные сигналы
      </h2>
      {alerts.length === 0 && (
        <div style={{ color: 'var(--text-muted)', textAlign: 'center', padding: '32px 0' }}>
          Нет активных сигналов
        </div>
      )}
      {alerts.map((a) => (
        <MiniAlert key={a.fb_ad_id} alert={a} onClick={() => onSelectAd(a.fb_ad_id)} />
      ))}
    </div>
  );
}
