import { useState, useCallback, useEffect } from 'react';
import { getNamingPatterns, getOffers } from '../api';

/* Варианты фильтра по дням */
const DAY_OPTIONS = [
  { value: 7, label: '7 дней' },
  { value: 14, label: '14 дней' },
  { value: 30, label: '30 дней' },
  { value: 90, label: '90 дней' },
];

export default function NamingTrackerPage() {
  const [patterns, setPatterns] = useState([]);
  const [totalPatterns, setTotalPatterns] = useState(0);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState(null);

  const [days, setDays] = useState(30);
  const [offerFilter, setOfferFilter] = useState('');
  const [offers, setOffers] = useState([]);
  const [expandedRows, setExpandedRows] = useState(new Set());
  const [sortBy, setSortBy] = useState('max_number');
  const [sortDir, setSortDir] = useState('desc');

  /* Загрузка офферов для фильтра */
  useEffect(() => {
    getOffers()
      .then((data) => setOffers(Array.isArray(data) ? data : []))
      .catch(() => {});
  }, []);

  /* Загрузка паттернов */
  const fetchPatterns = useCallback(async () => {
    setLoading(true);
    setError(null);
    try {
      const params = { days };
      if (offerFilter) params.offer_code = offerFilter;
      const data = await getNamingPatterns(params);
      setPatterns(data.patterns || []);
      setTotalPatterns(data.total_patterns || 0);
    } catch (err) {
      setError(err.message || 'Ошибка загрузки');
    } finally {
      setLoading(false);
    }
  }, [days, offerFilter]);

  useEffect(() => {
    fetchPatterns();
  }, [fetchPatterns]);

  /* Сортировка */
  const handleSort = (field) => {
    if (sortBy === field) {
      setSortDir((d) => (d === 'asc' ? 'desc' : 'asc'));
    } else {
      setSortBy(field);
      setSortDir('desc');
    }
  };

  const sorted = [...patterns].sort((a, b) => {
    let va, vb;
    if (sortBy === 'max_number') {
      va = a.max_number;
      vb = b.max_number;
    } else if (sortBy === 'total_count') {
      va = a.total_count;
      vb = b.total_count;
    } else if (sortBy === 'prefix') {
      va = a.prefix;
      vb = b.prefix;
    } else {
      va = a.offer_code || '';
      vb = b.offer_code || '';
    }
    if (va < vb) return sortDir === 'asc' ? -1 : 1;
    if (va > vb) return sortDir === 'asc' ? 1 : -1;
    return 0;
  });

  /* Раскрытие строки */
  const toggleRow = (prefix, offerCode) => {
    const key = `${prefix}::${offerCode}`;
    setExpandedRows((prev) => {
      const next = new Set(prev);
      if (next.has(key)) next.delete(key);
      else next.add(key);
      return next;
    });
  };

  const sortIcon = (field) => {
    if (sortBy !== field) return '';
    return sortDir === 'asc' ? ' \u2191' : ' \u2193';
  };

  return (
    <div className="space-y-md animate-fade-in">
      {/* Заголовок + фильтры */}
      <div className="flex flex-col gap-3 sm:flex-row sm:items-center sm:justify-between">
        <div>
          <h1 className="text-lg text-primary">Трекер нейминга</h1>
          <p className="text-sm text-muted">
            Последний номер объявления по каждому паттерну
          </p>
        </div>
        <div className="flex items-center gap-2">
          {/* Фильтр по дням */}
          <select
            className="rounded-md border border-border bg-surface px-2 py-1.5 text-xs text-primary focus:border-accent focus:outline-none"
            value={days}
            onChange={(e) => setDays(Number(e.target.value))}
          >
            {DAY_OPTIONS.map((opt) => (
              <option key={opt.value} value={opt.value}>
                {opt.label}
              </option>
            ))}
          </select>

          {/* Фильтр по офферу */}
          <select
            className="rounded-md border border-border bg-surface px-2 py-1.5 text-xs text-primary focus:border-accent focus:outline-none"
            value={offerFilter}
            onChange={(e) => setOfferFilter(e.target.value)}
          >
            <option value="">Все офферы</option>
            {offers.map((o) => (
              <option key={o.id} value={o.code}>
                {o.code}
              </option>
            ))}
          </select>

          {/* Кнопка обновить */}
          <button
            className="btn-secondary text-xs"
            onClick={fetchPatterns}
            disabled={loading}
          >
            {loading ? 'Загрузка...' : 'Обновить'}
          </button>
        </div>
      </div>

      {/* Загрузка */}
      {loading && (
        <div className="flex items-center justify-center py-12">
          <div className="h-6 w-6 animate-spin rounded-full border-2 border-accent border-t-transparent" />
        </div>
      )}

      {/* Ошибка */}
      {error && !loading && (
        <div className="rounded-md bg-danger-muted border border-danger/30 px-4 py-3 text-sm text-danger">
          {error}
        </div>
      )}

      {/* Пустое состояние */}
      {!loading && !error && patterns.length === 0 && (
        <div className="panel flex items-center justify-center py-12">
          <p className="text-sm text-muted">
            Нет паттернов нейминга за выбранный период
          </p>
        </div>
      )}

      {/* Таблица паттернов */}
      {!loading && !error && patterns.length > 0 && (
        <div className="panel overflow-hidden">
          <div className="px-4 py-3 border-b border-border">
            <span className="text-xs text-muted">
              Найдено паттернов: {totalPatterns}
            </span>
          </div>
          <div className="overflow-x-auto">
            <table className="w-full text-sm">
              <thead>
                <tr className="border-b border-border text-left text-xs text-muted">
                  <th className="px-4 py-2.5" />
                  <th
                    className="px-4 py-2.5 th-sortable cursor-pointer select-none"
                    onClick={() => handleSort('prefix')}
                  >
                    Префикс{sortIcon('prefix')}
                  </th>
                  <th
                    className="px-4 py-2.5 th-sortable cursor-pointer select-none"
                    onClick={() => handleSort('offer_code')}
                  >
                    Оффер{sortIcon('offer_code')}
                  </th>
                  <th
                    className="px-4 py-2.5 th-sortable cursor-pointer select-none text-right"
                    onClick={() => handleSort('max_number')}
                  >
                    Последний №{sortIcon('max_number')}
                  </th>
                  <th
                    className="px-4 py-2.5 th-sortable cursor-pointer select-none text-right"
                    onClick={() => handleSort('total_count')}
                  >
                    Всего{sortIcon('total_count')}
                  </th>
                  <th className="px-4 py-2.5 text-right">Следующий</th>
                </tr>
              </thead>
              <tbody>
                {sorted.map((group) => {
                  const rowKey = `${group.prefix}::${group.offer_code}`;
                  const isExpanded = expandedRows.has(rowKey);
                  const nextNumber = String(group.max_number + 1).padStart(
                    String(group.max_number).length >= 3 ? String(group.max_number).length : 3,
                    '0'
                  );
                  return (
                    <PatternRow
                      key={rowKey}
                      group={group}
                      isExpanded={isExpanded}
                      nextNumber={nextNumber}
                      onToggle={() => toggleRow(group.prefix, group.offer_code)}
                    />
                  );
                })}
              </tbody>
            </table>
          </div>
        </div>
      )}
    </div>
  );
}

/* Строка таблицы с раскрывающимися деталями */
function PatternRow({ group, isExpanded, nextNumber, onToggle }) {
  const [increment, setIncrement] = useState(0);
  const [copied, setCopied] = useState(false);

  /* Сброс инкремента при обновлении данных */
  useEffect(() => {
    setIncrement(0);
  }, [group.max_number]);

  const baseNumber = group.max_number + 1;
  const currentNumber = baseNumber + increment;
  const padLen = Math.max(String(group.max_number).length, 3);
  const displayNumber = String(currentNumber).padStart(padLen, '0');
  const fullName = `${group.prefix}${displayNumber}`;

  /* Копировать и инкрементировать */
  const handleCopy = (e) => {
    e.stopPropagation();
    navigator.clipboard.writeText(fullName).then(() => {
      setCopied(true);
      setTimeout(() => setCopied(false), 1200);
    });
    setIncrement((prev) => prev + 1);
  };

  /* Сбросить к исходному значению */
  const handleReset = (e) => {
    e.stopPropagation();
    setIncrement(0);
  };

  return (
    <>
      <tr
        className="tr-hover border-b border-border/50 cursor-pointer"
        onClick={onToggle}
      >
        <td className="px-4 py-2.5 w-8 text-muted">
          <span className={`inline-block transition-transform ${isExpanded ? 'rotate-90' : ''}`}>
            &#9654;
          </span>
        </td>
        <td className="px-4 py-2.5 font-mono text-xs text-primary">
          {group.prefix}
        </td>
        <td className="px-4 py-2.5">
          {group.offer_code ? (
            <span className="badge badge-neutral text-2xs">
              {group.offer_code}
            </span>
          ) : (
            <span className="text-xs text-muted">—</span>
          )}
        </td>
        <td className="px-4 py-2.5 text-right">
          <span className="font-mono text-lg font-semibold text-accent">
            {group.max_number}
          </span>
        </td>
        <td className="px-4 py-2.5 text-right text-xs text-secondary">
          {group.total_count}
        </td>
        <td className="px-4 py-2.5 text-right min-w-[180px]">
          <div className="flex items-center justify-end gap-2">
            <span className="font-mono text-sm font-medium text-success">
              {fullName}
            </span>
            <button
              className={`rounded px-2 py-0.5 text-2xs font-medium transition-colors ${
                copied
                  ? 'bg-success/20 text-success'
                  : 'bg-accent/10 text-accent hover:bg-accent/20'
              }`}
              onClick={handleCopy}
              title="Копировать и получить следующий номер"
            >
              {copied ? 'Скопировано' : 'Копировать'}
            </button>
            {increment > 0 && (
              <button
                className="rounded px-2 py-0.5 text-2xs font-medium text-muted hover:text-primary hover:bg-surface transition-colors"
                onClick={handleReset}
                title="Сбросить к исходному значению"
              >
                Сбросить
              </button>
            )}
          </div>
        </td>
      </tr>
      {isExpanded && group.recent_ads?.length > 0 && (
        <tr>
          <td colSpan={6} className="bg-elevated/50 px-8 py-3">
            <p className="text-2xs text-muted mb-2">Последние объявления:</p>
            <div className="grid grid-cols-[minmax(0,1fr)_auto_auto] gap-x-4 gap-y-1 text-xs">
              {group.recent_ads.map((ad) => (
                <div key={ad.fb_ad_id} className="contents">
                  <span className="font-mono text-primary truncate min-w-0">{ad.ad_name}</span>
                  <span className="text-muted tabular-nums">ID: {ad.fb_ad_id}</span>
                  <span className="text-muted tabular-nums">
                    {ad.last_observed_at
                      ? new Date(ad.last_observed_at).toLocaleDateString('ru-RU')
                      : ''}
                  </span>
                </div>
              ))}
            </div>
          </td>
        </tr>
      )}
    </>
  );
}
