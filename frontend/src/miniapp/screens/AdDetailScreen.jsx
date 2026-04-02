import { useEffect, useState } from 'react';
import { getAd, disableAd } from '../api.js';

const STATE_COLORS = {
  STOP_SENT: 'var(--accent-crimson)',
  WARNING_SENT: 'var(--accent-gold)',
  EARLY_SIGNAL_SENT: 'var(--accent-orchid)',
  CLAIMED: 'var(--accent-slate)',
  NORMAL: 'var(--accent-teal)',
};

export default function AdDetailScreen({ fbAdId }) {
  const [data, setData] = useState(null);
  const [disabling, setDisabling] = useState(false);
  const [done, setDone] = useState(false);

  useEffect(() => {
    getAd(fbAdId).then(setData).catch(() => {});
  }, [fbAdId]);

  const canDisable =
    data &&
    ['STOP_SENT', 'WARNING_SENT'].includes(data.alert_state) &&
    !data.latest_disable_task?.status?.match(/PENDING|RUNNING|RETRYING/);

  async function handleDisable() {
    setDisabling(true);
    try {
      await disableAd(fbAdId);
      setDone(true);
      const fresh = await getAd(fbAdId);
      setData(fresh);
    } finally {
      setDisabling(false);
    }
  }

  if (!data)
    return (
      <div style={{ padding: '16px', color: 'var(--text-muted)' }}>Загрузка...</div>
    );

  return (
    <div style={{ padding: '12px' }}>
      <div style={{ fontSize: '15px', fontWeight: 600, marginBottom: '4px' }}>
        {data.ad_name}
      </div>
      <div style={{ fontSize: '12px', color: 'var(--text-muted)', marginBottom: '12px' }}>
        {data.campaign_name}
      </div>
      <div
        style={{
          display: 'inline-block',
          padding: '3px 8px',
          borderRadius: '12px',
          background: STATE_COLORS[data.alert_state] || 'var(--bg-secondary)',
          color: 'white',
          fontSize: '11px',
          marginBottom: '12px',
        }}
      >
        {data.alert_state}
      </div>
      <div
        style={{
          display: 'grid',
          gridTemplateColumns: '1fr 1fr',
          gap: '8px',
          marginBottom: '16px',
        }}
      >
        {[
          ['Расход', `$${Number(data.spend || 0).toFixed(2)}`],
          ['CPC', `$${Number(data.cpc || 0).toFixed(2)}`],
          ['Лиды', data.leads ?? '—'],
          ['Депозиты', data.deposits ?? '—'],
        ].map(([l, v]) => (
          <div
            key={l}
            style={{
              background: 'var(--bg-secondary)',
              padding: '8px',
              borderRadius: '4px',
              fontSize: '12px',
            }}
          >
            <div style={{ color: 'var(--text-muted)' }}>{l}</div>
            <div style={{ fontWeight: 600 }}>{v}</div>
          </div>
        ))}
      </div>
      {canDisable && (
        <button
          onClick={handleDisable}
          disabled={disabling}
          style={{
            width: '100%',
            padding: '14px',
            background: disabling ? 'var(--bg-tertiary)' : 'var(--accent-crimson)',
            color: 'white',
            border: 'none',
            borderRadius: '8px',
            fontSize: '15px',
            fontWeight: 600,
            cursor: disabling ? 'not-allowed' : 'pointer',
          }}
        >
          {disabling ? 'Отключаем...' : 'Отключить'}
        </button>
      )}
      {done && (
        <div
          style={{
            color: 'var(--accent-teal)',
            textAlign: 'center',
            marginTop: '12px',
            fontSize: '13px',
          }}
        >
          ✓ Задача создана
        </div>
      )}
    </div>
  );
}
