// Выбор периода: два поля дат + пресеты
import { useCallback } from 'react';

function toISODate(date) {
  return date.toISOString().slice(0, 10);
}

function PresetButton({ label, onClick }) {
  return (
    <button
      className="btn-ghost text-2xs whitespace-nowrap"
      onClick={onClick}
    >
      {label}
    </button>
  );
}

export function DateRangePicker({ from, to, onChange }) {
  const handlePreset = useCallback((days) => {
    const end = new Date();
    const start = new Date();
    start.setDate(start.getDate() - days);
    onChange({ from: toISODate(start), to: toISODate(end) });
  }, [onChange]);

  const handleToday = useCallback(() => {
    const today = toISODate(new Date());
    onChange({ from: today, to: today });
  }, [onChange]);

  return (
    <div className="flex items-center gap-2 flex-wrap">
      <input
        type="date"
        value={from || ''}
        onChange={(e) => onChange({ from: e.target.value, to })}
        className="rounded-md border border-border bg-elevated px-2.5 py-1.5 text-sm text-primary"
      />
      <span className="text-muted text-sm">—</span>
      <input
        type="date"
        value={to || ''}
        onChange={(e) => onChange({ from, to: e.target.value })}
        className="rounded-md border border-border bg-elevated px-2.5 py-1.5 text-sm text-primary"
      />
      <PresetButton label="Сегодня" onClick={handleToday} />
      <PresetButton label="7 дней" onClick={() => handlePreset(7)} />
      <PresetButton label="30 дней" onClick={() => handlePreset(30)} />
    </div>
  );
}
