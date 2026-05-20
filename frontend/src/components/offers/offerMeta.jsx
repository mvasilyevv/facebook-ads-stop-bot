import { useState } from 'react';

export function shortUuid(id) {
  if (!id) return '—';
  const s = String(id);
  return s.length > 8 ? `${s.slice(0, 8)}…` : s;
}

export function CopyableChip({ label, value, display }) {
  const [copied, setCopied] = useState(false);
  const text = value ? String(value) : '';
  const shown = display ?? (text ? shortUuid(text) : '—');

  const copy = async (e) => {
    e.stopPropagation();
    if (!text) return;
    try {
      await navigator.clipboard.writeText(text);
      setCopied(true);
      setTimeout(() => setCopied(false), 1200);
    } catch {
      /* буфер недоступен */
    }
  };

  if (!text) return null;

  return (
    <button
      type="button"
      onClick={copy}
      className={`inline-flex max-w-full items-center gap-1 rounded-md border px-2 py-1 font-mono text-2xs transition-colors ${
        copied
          ? 'border-success/40 bg-success-muted text-success'
          : 'border-border bg-elevated/60 text-secondary hover:border-accent/40 hover:text-accent'
      }`}
      title={text}
      aria-label={`Скопировать ${label}`}
    >
      <span className="shrink-0 text-muted">{label}</span>
      <span className="truncate">{shown}</span>
      <span className="shrink-0">{copied ? '✓' : '⧉'}</span>
    </button>
  );
}

export function OfferMetaChips({ offer }) {
  return (
    <div className="flex flex-wrap gap-1.5">
      <CopyableChip label="ID" value={offer.id} display={shortUuid(offer.id)} />
      <CopyableChip label="Кабинет" value={offer.cabinet_id} />
      <CopyableChip label="Пиксель" value={offer.pixel_id} />
    </div>
  );
}
