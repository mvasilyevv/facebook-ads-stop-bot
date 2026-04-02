import { ALERT_STATE_LABELS, ALERT_STATE_COLORS, ALERT_STATE_TOOLTIPS } from '../constants/alertStates.js';

// Pill-бейдж статуса алерта объявления
export function StateIcon({ state = 'NORMAL', size = 'md' }) {
  const label = ALERT_STATE_LABELS[state] || '?';
  const bgColor = ALERT_STATE_COLORS[state] || 'var(--bg-tertiary)';
  const tooltip = ALERT_STATE_TOOLTIPS[state] || state;

  const colorMap = {
    NORMAL:            'var(--accent-emerald-dim)',
    EARLY_SIGNAL_SENT: 'var(--accent-orchid-dim)',
    WARNING_SENT:      'var(--accent-gold-dim)',
    STOP_SENT:         'var(--accent-crimson-dim)',
    CLAIMED:           'var(--bg-tertiary)',
    DISABLED:          'var(--bg-tertiary)',
    ARCHIVED:          'var(--bg-tertiary)',
  };

  const bg = colorMap[state] || 'var(--bg-tertiary)';
  const fontSize = size === 'lg' ? '11px' : size === 'sm' ? '10px' : '11px';
  const padding = size === 'lg' ? '3px 8px' : '2px 6px';

  return (
    <span
      className={`state-icon state-icon--${state.toLowerCase()} state-icon--${size}`}
      title={tooltip}
      style={{
        display: 'inline-block',
        background: bg,
        color: bgColor,
        border: '1px solid currentColor',
        borderRadius: '6px',
        padding,
        fontSize,
        fontWeight: 600,
        letterSpacing: '0.03em',
        textTransform: 'uppercase',
        lineHeight: 1.4,
        whiteSpace: 'nowrap',
        opacity: state === 'DISABLED' || state === 'ARCHIVED' ? 0.6 : 1,
      }}
    >
      {label}
    </span>
  );
}
