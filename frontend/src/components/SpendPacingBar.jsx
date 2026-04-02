// Карточка темпа расхода сегодня
export function SpendPacingBar({ performance = null }) {
  if (!performance || !performance.summary || performance.summary.spend === 0) {
    return null;
  }

  const currentHour = new Date().getHours() + new Date().getMinutes() / 60;
  const adjustedCurrentHour = currentHour < 1 ? 1 : currentHour;
  const currentSpend = performance.summary.spend || 0;
  const projectedDayEnd = (currentSpend / adjustedCurrentHour) * 24;

  const speedPerHour = currentSpend / adjustedCurrentHour;
  const percentOfDay = (adjustedCurrentHour / 24) * 100;

  let spendColor = '#f59e0b';
  if (projectedDayEnd > currentSpend * 3) {
    spendColor = '#10b981';
  } else if (projectedDayEnd > currentSpend * 1.5) {
    spendColor = '#0ea5e9';
  }

  const formatMoney = (value) => `$${value.toFixed(2)}`;

  return (
    <div style={{
      background: 'var(--bg-card)',
      border: '1px solid var(--border-color)',
      borderRadius: '6px',
      boxShadow: 'var(--shadow-sm)',
      marginBottom: '16px',
    }}>
      <div style={{
        padding: '12px 16px',
        borderBottom: '1px solid var(--border-color)',
        fontSize: '13px',
        fontWeight: 700,
        textTransform: 'uppercase',
        color: 'var(--text-muted)',
        letterSpacing: '0.06em',
      }}>
        Темп расхода сегодня
      </div>

      <div style={{ padding: '16px' }}>
      <div style={{
        fontSize: '22px',
        fontWeight: 700,
        fontFamily: 'JetBrains Mono, monospace',
        color: spendColor,
        marginBottom: '4px',
      }}>
        {formatMoney(currentSpend)}
      </div>

      <div style={{
        fontSize: '12px',
        color: 'var(--text-muted)',
        marginBottom: '12px',
      }}>
        {projectedDayEnd === 0
          ? 'нет данных для прогноза'
          : `прогноз к концу дня: ${formatMoney(projectedDayEnd)}`}
      </div>

      <div style={{
        display: 'grid',
        gap: '8px',
      }}>
        <div style={{
          display: 'flex',
          justifyContent: 'space-between',
          fontSize: '13px',
          color: 'var(--text-primary)',
        }}>
          <span>Скорость:</span>
          <span style={{
            fontFamily: 'JetBrains Mono, monospace',
            fontWeight: 600,
          }}>
            {formatMoney(speedPerHour)}/час
          </span>
        </div>

        <div style={{
          display: 'flex',
          justifyContent: 'space-between',
          fontSize: '13px',
          color: 'var(--text-primary)',
        }}>
          <span>Прошло:</span>
          <span style={{
            fontFamily: 'JetBrains Mono, monospace',
            fontWeight: 600,
          }}>
            {percentOfDay.toFixed(1)}% дня
          </span>
        </div>
      </div>
      </div>
    </div>
  );
}
