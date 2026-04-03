import { ALERT_STATE_LABELS, ALERT_STATE_COLORS, ALERT_STATE_TOOLTIPS } from '../constants/alertStates.js';
import { twMerge } from 'tailwind-merge';

// Маппинг состояний на Tailwind-цвета
const STATE_STYLES = {
  NORMAL:            'bg-success-muted text-success border-success/40',
  EARLY_SIGNAL_SENT: 'bg-early-muted text-early border-early/40',
  WARNING_SENT:      'bg-warning-muted text-warning border-warning/40',
  STOP_SENT:         'bg-danger-muted text-danger border-danger/40',
  CLAIMED:           'bg-elevated text-muted border-border',
  DISABLED:          'bg-elevated text-muted border-border opacity-60',
  ARCHIVED:          'bg-elevated text-muted border-border opacity-60',
};

const SIZE_STYLES = {
  sm: 'px-1.5 py-0.5 text-[10px]',
  md: 'px-2 py-0.5 text-2xs',
  lg: 'px-2.5 py-1 text-2xs',
};

/** Pill-бейдж статуса алерта объявления */
export function StateIcon({ state = 'NORMAL', size = 'md' }) {
  const label = ALERT_STATE_LABELS[state] || '?';
  const tooltip = ALERT_STATE_TOOLTIPS[state] || state;
  const stateStyle = STATE_STYLES[state] || STATE_STYLES.NORMAL;
  const sizeStyle = SIZE_STYLES[size] || SIZE_STYLES.md;

  return (
    <span
      className={twMerge(
        'inline-block rounded-md border font-semibold uppercase tracking-wide leading-snug whitespace-nowrap',
        stateStyle,
        sizeStyle,
      )}
      title={tooltip}
    >
      {label}
    </span>
  );
}
