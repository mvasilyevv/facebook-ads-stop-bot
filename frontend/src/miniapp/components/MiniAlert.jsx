const ICONS = {
  STOP_SENT: '🛑',
  WARNING_SENT: '⚠️',
  EARLY_SIGNAL_SENT: '🔎',
  CLAIMED: '🔄',
};
const COLORS = {
  STOP_SENT: 'var(--accent-crimson)',
  WARNING_SENT: 'var(--accent-gold)',
  EARLY_SIGNAL_SENT: 'var(--accent-orchid)',
  CLAIMED: 'var(--accent-slate)',
};

function relTime(iso) {
  if (!iso) return '';
  const s = (Date.now() - new Date(iso)) / 1000;
  if (s < 60) return `${Math.round(s)}с`;
  if (s < 3600) return `${Math.round(s / 60)}м`;
  return `${Math.round(s / 3600)}ч`;
}

export default function MiniAlert({ alert, onClick }) {
  return (
    <div
      onClick={onClick}
      style={{
        display: 'flex',
        alignItems: 'center',
        gap: '10px',
        padding: '10px',
        background: 'var(--bg-secondary)',
        borderRadius: '6px',
        marginBottom: '6px',
        cursor: onClick ? 'pointer' : 'default',
        borderLeft: `3px solid ${COLORS[alert.alert_state] || 'var(--border-color)'}`,
      }}
    >
      <span style={{ fontSize: '18px' }}>{ICONS[alert.alert_state] || '❓'}</span>
      <div style={{ flex: 1, minWidth: 0 }}>
        <div
          style={{
            fontWeight: 600,
            fontSize: '13px',
            overflow: 'hidden',
            textOverflow: 'ellipsis',
            whiteSpace: 'nowrap',
          }}
        >
          {alert.ad_name}
        </div>
        <div style={{ fontSize: '11px', color: 'var(--text-muted)' }}>
          {alert.campaign_name}
        </div>
      </div>
      <div style={{ fontSize: '11px', color: 'var(--text-muted)', flexShrink: 0 }}>
        {relTime(alert.last_observed_at)}
      </div>
    </div>
  );
}
