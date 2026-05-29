// Slide-over панель деталей объявления
import { useState, useEffect } from 'react';
import { getAdTimeline } from '../../api.js';
import { StateIcon } from '../StateIcon.jsx';

import { fmt$, fmtN } from '../../utils/formatters.js';
import { formatTime } from '../../utils/timeUtils.js';

function AdMetrics({ metrics }) {
  if (!metrics) return null;
  const items = [
    { label: 'Расход', value: fmt$(metrics.spend) },
    { label: 'Клики', value: fmtN(metrics.clicks) },
    { label: 'Лиды', value: fmtN(metrics.leads) },
    { label: 'Реги', value: fmtN(metrics.registrations) },
    { label: 'Депозиты', value: fmtN(metrics.deposits) },
    { label: 'CPC', value: fmt$(metrics.cpc) },
    { label: 'CPL', value: fmt$(metrics.cpl) },
    { label: 'CPR', value: fmt$(metrics.cpr) },
  ];
  return (
    <div className="grid grid-cols-4 gap-3 mb-4">
      {items.map((m) => (
        <div key={m.label} className="flex flex-col gap-0.5">
          <span className="text-2xs uppercase tracking-wider text-secondary">
            {m.label}
          </span>
          <span className="font-mono text-sm font-semibold text-primary">
            {m.value}
          </span>
        </div>
      ))}
    </div>
  );
}

function TimelineEvent({ event }) {
  return (
    <div className="flex gap-3 py-2 border-b border-border last:border-0">
      <div className="flex-shrink-0 pt-0.5">
        <StateIcon state={event.stage || event.status} size="sm" />
      </div>
      <div className="flex-1 min-w-0">
        <div className="flex items-baseline gap-2">
          <span className="text-sm font-medium text-primary">
            {event.event_type || event.type}
          </span>
          <span className="text-2xs text-muted flex-shrink-0">
            {formatTime(event.created_at)}
          </span>
        </div>
        {event.reason && (
          <div className="text-2xs text-secondary mt-0.5">{event.reason}</div>
        )}
        {event.metrics && (
          <div className="text-2xs text-muted mt-0.5 font-mono">
            spend: {fmt$(event.metrics.spend)} | leads: {fmtN(event.metrics.leads)}
          </div>
        )}
      </div>
    </div>
  );
}

export function AdDetailPanel({ fbAdId, onClose }) {
  const [data, setData] = useState(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState(null);

  useEffect(() => {
    if (!fbAdId) return;
    setLoading(true);
    setError(null);
    getAdTimeline(fbAdId)
      .then(setData)
      .catch((e) => setError(e.message))
      .finally(() => setLoading(false));
  }, [fbAdId]);

  if (!fbAdId) return null;

  return (
    <>
      <div
        className="fixed inset-0 z-40 bg-black/60 animate-fade-in"
        onClick={onClose}
      />
      <div className="fixed inset-y-0 right-0 z-50 w-full max-w-lg bg-surface border-l border-border overflow-y-auto p-5">
        <button className="btn-ghost mb-4 text-sm" onClick={onClose}>
          ← Назад
        </button>

        {loading && (
          <div className="py-8 text-center text-sm text-muted">Загрузка…</div>
        )}

        {error && (
          <div className="text-sm text-danger mb-4">{error}</div>
        )}

        {data && (
          <>
            <AdHeader data={data} />
            <AdMetrics metrics={data.current_metrics} />
            <h3 className="mb-3 text-2xs font-bold uppercase tracking-widest text-muted">
              Цепочка событий
            </h3>
            <div className="max-h-[500px] overflow-y-auto">
              {(data.timeline || []).map((event, i) => (
                <TimelineEvent key={event.id || i} event={event} />
              ))}
            </div>
          </>
        )}
      </div>
    </>
  );
}

function AdHeader({ data }) {
  return (
    <div className="mb-4">
      <h2 className="text-sm font-semibold text-primary truncate" title={data.ad_name}>
        {data.ad_name || data.fb_ad_id}
      </h2>
      <div className="flex items-center gap-2 mt-1 text-2xs text-secondary">
        {data.campaign_name && <span>{data.campaign_name}</span>}
        {data.offer_code && (
          <>
            <span className="text-muted">·</span>
            <span>{data.offer_code}</span>
          </>
        )}
        {data.current_state && (
          <>
            <span className="text-muted">·</span>
            <StateIcon state={data.current_state} size="sm" />
          </>
        )}
      </div>
    </div>
  );
}
