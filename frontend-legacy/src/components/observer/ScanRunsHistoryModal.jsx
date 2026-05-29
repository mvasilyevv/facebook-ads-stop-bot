import React, { useEffect, useState } from 'react';

import { getScanRuns } from '../../api.js';

const FILTERS = [
  { key: 'all', label: 'Все' },
  { key: 'errors', label: 'С ошибкой' },
  { key: 'slow', label: 'Медленные' },
  { key: 'with_alerts', label: 'С алертами' },
];

const OUTCOME_COLORS = {
  OK: 'text-success',
  OK_PARTIAL: 'text-success',
  EMPTY_OK: 'text-muted',
  EMPTY_BAD: 'text-warning',
  STALE_DATA: 'text-orange-400',
  BROWSER_LOST: 'text-danger',
  INTERRUPTED: 'text-muted',
  RUNNING: 'text-accent',
};

function formatTime(iso) {
  if (!iso) return '—';
  return new Date(iso).toLocaleTimeString('ru-RU', {
    hour: '2-digit',
    minute: '2-digit',
    second: '2-digit',
  });
}

export default function ScanRunsHistoryModal({ onClose }) {
  const [runs, setRuns] = useState([]);
  const [filter, setFilter] = useState('all');
  const [loading, setLoading] = useState(false);
  const [expandedId, setExpandedId] = useState(null);

  const reload = async (nextFilter = filter) => {
    setLoading(true);
    try {
      const body = await getScanRuns({ limit: 50, filter: nextFilter });
      setRuns(body.runs || []);
    } catch (e) {
      setRuns([]);
      // eslint-disable-next-line no-console
      console.error('Не удалось загрузить scan-runs:', e);
    } finally {
      setLoading(false);
    }
  };

  useEffect(() => {
    reload('all');
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  return (
    <div
      className="fixed inset-0 z-50 flex items-center justify-center bg-black/50 p-4"
      onClick={onClose}
    >
      <div
        className="flex max-h-[80vh] w-full max-w-4xl flex-col overflow-hidden rounded-lg border border-border bg-surface shadow-xl"
        onClick={(e) => e.stopPropagation()}
      >
        <div className="flex items-center justify-between border-b border-border px-4 py-3">
          <h2 className="text-sm font-semibold">История сканов</h2>
          <button onClick={onClose} className="text-muted hover:text-text" type="button">
            ✕
          </button>
        </div>

        <div className="flex items-center gap-2 border-b border-border px-4 py-2">
          {FILTERS.map((f) => (
            <button
              key={f.key}
              type="button"
              onClick={() => {
                setFilter(f.key);
                reload(f.key);
              }}
              className={`rounded border px-2 py-1 text-xs ${
                filter === f.key
                  ? 'border-accent/40 bg-accent-muted text-accent'
                  : 'border-border bg-elevated text-muted hover:text-text'
              }`}
            >
              {f.label}
            </button>
          ))}
          <button
            type="button"
            onClick={() => reload()}
            disabled={loading}
            className="ml-auto rounded border border-border bg-elevated px-2 py-1 text-xs text-muted hover:text-text disabled:opacity-50"
          >
            {loading ? 'Обновляю…' : 'Обновить'}
          </button>
        </div>

        <div className="flex-1 overflow-auto">
          <table className="w-full text-xs">
            <thead className="sticky top-0 border-b border-border bg-surface">
              <tr>
                <th className="px-3 py-2 text-left">#</th>
                <th className="px-3 py-2 text-left">Время</th>
                <th className="px-3 py-2 text-left">Outcome</th>
                <th className="px-3 py-2 text-right">Строк</th>
                <th className="px-3 py-2 text-right">Длительность</th>
                <th className="px-3 py-2 text-right">Алерты</th>
                <th className="px-3 py-2 text-left">Сообщение</th>
              </tr>
            </thead>
            <tbody>
              {runs.map((run) => {
                const isExpanded = expandedId === run.id;
                return (
                  <React.Fragment key={run.id}>
                    <tr
                      onClick={() => setExpandedId(isExpanded ? null : run.id)}
                      className="cursor-pointer border-b border-border hover:bg-elevated"
                    >
                      <td className="px-3 py-2 font-mono">{run.scan_id}</td>
                      <td className="px-3 py-2">{formatTime(run.started_at)}</td>
                      <td
                        className={`px-3 py-2 font-medium ${OUTCOME_COLORS[run.outcome] || ''}`}
                      >
                        {run.outcome}
                      </td>
                      <td className="px-3 py-2 text-right font-mono">
                        {run.rows_total ?? '—'}
                      </td>
                      <td className="px-3 py-2 text-right font-mono">
                        {run.duration_seconds
                          ? `${run.duration_seconds.toFixed(1)}с`
                          : '—'}
                      </td>
                      <td className="px-3 py-2 text-right font-mono">
                        {run.alerts_warning}/{run.alerts_stop}
                      </td>
                      <td className="max-w-xs truncate px-3 py-2 text-muted">
                        {run.error_message || run.empty_reason || '—'}
                      </td>
                    </tr>
                    {isExpanded && (
                      <tr className="border-b border-border bg-elevated">
                        <td colSpan={7} className="px-3 py-2">
                          <pre className="overflow-x-auto whitespace-pre-wrap text-2xs text-muted">
                            {JSON.stringify(
                              {
                                phase_timings: run.phase_timings,
                                warnings: run.warnings,
                                threat_level: run.threat_level,
                                next_interval_s: run.next_interval_s,
                                error_kind: run.error_kind,
                              },
                              null,
                              2,
                            )}
                          </pre>
                        </td>
                      </tr>
                    )}
                  </React.Fragment>
                );
              })}
              {!runs.length && !loading && (
                <tr>
                  <td colSpan={7} className="px-3 py-4 text-center text-muted">
                    Нет данных
                  </td>
                </tr>
              )}
            </tbody>
          </table>
        </div>
      </div>
    </div>
  );
}
