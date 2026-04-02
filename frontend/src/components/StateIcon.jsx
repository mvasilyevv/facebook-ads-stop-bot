// Иконка и цвет для статуса алерта объявления
export function StateIcon({ state = 'NORMAL', stage = null, size = 'md' }) {
  const stateMap = {
    NORMAL: { icon: '✅', label: 'Норма', color: 'var(--accent-emerald)' },
    EARLY_SIGNAL_SENT: { icon: '🔎', label: 'Ранний сигнал', color: 'var(--accent-orchid)' },
    WARNING_SENT: { icon: '⚠️', label: 'Предупреждение', color: 'var(--accent-gold)' },
    STOP_SENT: { icon: '🛑', label: 'Стоп-алерт', color: 'var(--accent-crimson)' },
    CLAIMED: { icon: '🔄', label: 'Ожидает OFF', color: 'var(--accent-slate)' },
    DISABLED: { icon: '🔕', label: 'Отключено', color: 'var(--accent-slate)' },
    ARCHIVED: { icon: '🗂', label: 'Архив', color: 'var(--text-muted)' },
  };

  const config = stateMap[state] || { icon: '❓', label: 'Неизвестно', color: 'var(--text-muted)' };

  const iconSize = { sm: '14px', md: '16px', lg: '20px' }[size];
  const labelSize = { sm: '10px', md: '12px', lg: '13px' }[size];
  const gap = { sm: '2px', md: '4px', lg: '6px' }[size];
  const display = size === 'lg' ? 'flex' : 'inline-flex';
  const flexDirection = size === 'lg' ? 'column' : 'row';

  return (
    <span
      className={`state-icon state-icon--${state.toLowerCase()} state-icon--${size}`}
      style={{
        display,
        flexDirection,
        alignItems: 'center',
        gap,
        color: config.color,
      }}
    >
      <span style={{ fontSize: iconSize, lineHeight: 1 }}>
        {config.icon}
      </span>
      {labelSize !== '0px' && (
        <span style={{ fontSize: labelSize, lineHeight: 1.2 }}>
          {config.label}
        </span>
      )}
    </span>
  );
}
