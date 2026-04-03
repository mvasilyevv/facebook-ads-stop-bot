// Мультивыбор офферов
import { useCallback } from 'react';

function toggleValue(arr, val) {
  return arr.includes(val) ? arr.filter((v) => v !== val) : [...arr, val];
}

function OfferLabel({ offer }) {
  if (offer.code === offer.name || !offer.name) return offer.code;
  return `${offer.code} — ${offer.name}`;
}

export function OfferSelector({ offers = [], selected = [], onChange }) {
  const handleToggle = useCallback(
    (code) => {
      onChange(toggleValue(selected, code));
    },
    [selected, onChange],
  );

  const handleClear = useCallback(() => onChange([]), [onChange]);

  if (!offers.length) return null;

  return (
    <div className="flex items-center gap-1.5 flex-wrap">
      <span className="text-2xs text-muted mr-0.5">Оффер:</span>
      {offers.map((o) => {
        const active = selected.includes(o.code);
        return (
          <button
            key={o.id}
            onClick={() => handleToggle(o.code)}
            className={`
              rounded-md border px-2 py-1 text-2xs transition-colors
              ${active
                ? 'border-accent bg-accent-muted text-accent font-medium'
                : 'border-border bg-elevated text-secondary hover:text-primary'}
            `}
          >
            <OfferLabel offer={o} />
          </button>
        );
      })}
      {selected.length > 0 && (
        <button
          onClick={handleClear}
          className="text-2xs text-muted hover:text-primary ml-1"
        >
          ✕
        </button>
      )}
    </div>
  );
}
