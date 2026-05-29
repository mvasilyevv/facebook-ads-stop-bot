// Панель фильтров: период + офферы (мульти) + кампании (dropdown) + сброс
import { useCallback, useState, useRef, useEffect } from 'react';
import { DateRangePicker } from './DateRangePicker.jsx';
import { OfferSelector } from './OfferSelector.jsx';

function toISODate(date) {
  return date.toISOString().slice(0, 10);
}

/* Выпадающий мультиселект кампаний с полными названиями */
function CampaignDropdown({ campaigns = [], selected = [], onChange }) {
  const [open, setOpen] = useState(false);
  const ref = useRef(null);

  // Закрытие по клику вне
  useEffect(() => {
    if (!open) return;
    const handler = (e) => {
      if (ref.current && !ref.current.contains(e.target)) setOpen(false);
    };
    document.addEventListener('mousedown', handler);
    return () => document.removeEventListener('mousedown', handler);
  }, [open]);

  const toggle = useCallback(
    (name) => {
      const next = selected.includes(name)
        ? selected.filter((n) => n !== name)
        : [...selected, name];
      onChange(next);
    },
    [selected, onChange],
  );

  if (!campaigns.length) return null;

  const label = selected.length
    ? `Кампании (${selected.length})`
    : 'Все кампании';

  return (
    <div className="relative" ref={ref}>
      <button
        onClick={() => setOpen(!open)}
        className="rounded-md border border-border bg-elevated px-3 py-1.5 text-sm text-primary
          hover:bg-surface transition-colors flex items-center gap-2 min-w-[180px]"
      >
        <span className="truncate">{label}</span>
        <ChevronIcon open={open} />
      </button>
      {open && (
        <DropdownMenu
          campaigns={campaigns}
          selected={selected}
          onToggle={toggle}
          onClear={() => onChange([])}
        />
      )}
    </div>
  );
}

function ChevronIcon({ open }) {
  return (
    <svg
      width="12" height="12" viewBox="0 0 12 12"
      className={`text-muted transition-transform ${open ? 'rotate-180' : ''}`}
      fill="none" stroke="currentColor" strokeWidth="2"
    >
      <polyline points="2,4 6,8 10,4" />
    </svg>
  );
}

function DropdownMenu({ campaigns, selected, onToggle, onClear }) {
  return (
    <div className="absolute top-full left-0 mt-1 z-20 w-[420px] max-h-[300px]
      overflow-y-auto rounded-md border border-border bg-surface shadow-lg"
    >
      {selected.length > 0 && (
        <button
          onClick={onClear}
          className="w-full px-3 py-1.5 text-left text-2xs text-muted
            hover:bg-elevated border-b border-border"
        >
          Сбросить выбор
        </button>
      )}
      {campaigns.map((name) => (
        <CampaignOption
          key={name}
          name={name}
          checked={selected.includes(name)}
          onToggle={onToggle}
        />
      ))}
    </div>
  );
}

function CampaignOption({ name, checked, onToggle }) {
  return (
    <button
      onClick={() => onToggle(name)}
      className={`w-full px-3 py-2 text-left text-sm flex items-center gap-2
        hover:bg-elevated transition-colors
        ${checked ? 'text-accent' : 'text-primary'}`}
    >
      <span className={`w-4 h-4 rounded border flex-shrink-0 flex items-center justify-center
        ${checked ? 'bg-accent border-accent' : 'border-border'}`}
      >
        {checked && (
          <svg width="10" height="10" viewBox="0 0 10 10" fill="none"
            stroke="white" strokeWidth="2" strokeLinecap="round"
          >
            <polyline points="2,5 4,7 8,3" />
          </svg>
        )}
      </span>
      <span className="break-all leading-snug">{name}</span>
    </button>
  );
}

export function HistoryFilters({ filters, onChange, offers, campaigns = [] }) {
  const handleDateChange = useCallback(({ from, to }) => {
    onChange({ ...filters, dateFrom: from, dateTo: to });
  }, [filters, onChange]);

  const handleOfferChange = useCallback((codes) => {
    onChange({ ...filters, offerCodes: codes, campaignNames: [] });
  }, [filters, onChange]);

  const handleCampaignChange = useCallback((names) => {
    onChange({ ...filters, campaignNames: names });
  }, [filters, onChange]);

  const handleReset = useCallback(() => {
    const end = new Date();
    const start = new Date();
    start.setDate(start.getDate() - 7);
    onChange({
      dateFrom: toISODate(start),
      dateTo: toISODate(end),
      offerCodes: [],
      campaignNames: [],
    });
  }, [onChange]);

  // Каскад: при выборе оффера показывать только его кампании
  const filteredCampaigns = filters.offerCodes?.length
    ? campaigns.filter((name) =>
        filters.offerCodes.some((code) =>
          name.toLowerCase().includes(code.toLowerCase()),
        ),
      )
    : campaigns;

  const hasFilters =
    (filters.offerCodes?.length > 0) || (filters.campaignNames?.length > 0);

  return (
    <div className="sticky top-0 z-10 panel px-4 py-3 bg-surface">
      <div className="flex items-center gap-3 flex-wrap">
        <DateRangePicker
          from={filters.dateFrom}
          to={filters.dateTo}
          onChange={handleDateChange}
        />
        <CampaignDropdown
          campaigns={filteredCampaigns}
          selected={filters.campaignNames || []}
          onChange={handleCampaignChange}
        />
        {hasFilters && (
          <button className="btn-ghost text-2xs text-muted" onClick={handleReset}>
            Сбросить
          </button>
        )}
      </div>
      <div className="mt-2">
        <OfferSelector
          offers={offers}
          selected={filters.offerCodes || []}
          onChange={handleOfferChange}
        />
      </div>
    </div>
  );
}
