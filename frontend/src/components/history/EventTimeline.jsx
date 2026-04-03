// Вертикальная лента событий
import { formatTime } from '../../utils/timeUtils.js';

const EVENT_STYLES = {
  alert_warning: { icon: '▲', color: 'text-warning', bg: 'bg-warning-muted' },
  alert_stop: { icon: '●', color: 'text-danger', bg: 'bg-danger-muted' },
  disable: { icon: '✕', color: 'text-muted', bg: 'bg-elevated' },
  enable: { icon: '✓', color: 'text-success', bg: 'bg-success-muted' },
};

function getEventStyle(event) {
  if (event.event_type === 'disable') return EVENT_STYLES.disable;
  if (event.event_type === 'enable') return EVENT_STYLES.enable;
  if (event.stage === 'STOP') return EVENT_STYLES.alert_stop;
  return EVENT_STYLES.alert_warning;
}

function EventItem({ event }) {
  const style = getEventStyle(event);
  return (
    <div className="flex gap-3 py-2">
      <div className={`flex-shrink-0 w-7 h-7 rounded-full ${style.bg} flex items-center justify-center`}>
        <span className={`text-xs font-bold ${style.color}`}>{style.icon}</span>
      </div>
      <div className="flex-1 min-w-0">
        <div className="flex items-baseline gap-2">
          <span className="text-sm font-medium text-primary truncate">
            {event.ad_name || event.fb_ad_id}
          </span>
          <span className="text-2xs text-muted flex-shrink-0">
            {formatTime(event.created_at)}
          </span>
        </div>
        <div className="text-2xs text-secondary mt-0.5">
          {event.reason || event.event_type}
        </div>
      </div>
    </div>
  );
}

export function EventTimeline({ events = [], onLoadMore }) {
  if (!events.length) {
    return (
      <div>
        <h3 className="mb-3 text-2xs font-bold uppercase tracking-widest text-muted">
          Лента событий
        </h3>
        <div className="py-4 text-center text-sm text-muted">Нет событий</div>
      </div>
    );
  }

  return (
    <div>
      <h3 className="mb-3 text-2xs font-bold uppercase tracking-widest text-muted">
        Лента событий
      </h3>
      <div className="max-h-[500px] overflow-y-auto divide-y divide-border">
        {events.map((event, i) => (
          <EventItem key={event.id || i} event={event} />
        ))}
      </div>
      {onLoadMore && (
        <button
          className="btn-ghost w-full mt-2 text-center text-2xs"
          onClick={onLoadMore}
        >
          Показать ещё
        </button>
      )}
    </div>
  );
}
