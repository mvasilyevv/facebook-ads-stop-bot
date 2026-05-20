import { useEffect, useState } from 'react';

const THREAT_BADGE = {
  IMMEDIATE: { label: 'Ре-скан', color: 'bg-danger-muted text-danger animate-pulse' },
  CRITICAL: { label: 'Критично', color: 'bg-danger-muted text-danger' },
  ELEVATED: { label: 'Повышенно', color: 'bg-warning-muted text-warning' },
  ACTIVE: { label: 'Активно', color: 'bg-accent-muted text-accent' },
  CALM: { label: 'Спокойно', color: 'bg-success-muted text-success' },
  IDLE: { label: 'Ожидание', color: 'bg-elevated text-muted' },
};

function parseObserverStatusMessage(msg) {
  if (!msg) return { intervalSec: null, threatLevel: null };
  const intervalMatch = msg.match(/интервал:\s*(\d+)/);
  const threatMatch = msg.match(/Угроза:\s*(\w+)/);
  return {
    intervalSec: intervalMatch ? parseInt(intervalMatch[1], 10) : null,
    threatLevel: threatMatch ? threatMatch[1] : null,
  };
}

function formatScanDuration(seconds) {
  const value = Number(seconds);
  if (!Number.isFinite(value) || value <= 0) return '—';
  if (value < 60) return `${Math.round(value)}с`;
  const minutes = Math.floor(value / 60);
  const rest = Math.round(value % 60);
  return rest > 0 ? `${minutes}м ${rest}с` : `${minutes}м`;
}

function getVisionRuntimeMeta(vision) {
  const status = String(vision?.runtime_status || 'NOT_CONFIGURED').toUpperCase();
  if (status === 'READY') {
    return {
      label: vision?.cdp_port ? `CDP ${vision.cdp_port}` : 'CDP готов',
      color: 'bg-success-muted text-success border-success/30',
      message: vision?.runtime_status_message || 'CDP-порт готов.',
    };
  }
  if (status === 'NOT_RUNNING') {
    return {
      label: 'Vision не запущен',
      color: 'bg-warning/10 text-warning border-warning/30',
      message: vision?.runtime_status_message || 'Профиль стартует при первом обращении к браузеру.',
    };
  }
  if (status === 'MISSING_CDP' || status === 'CDP_NOT_READY') {
    return {
      label: 'Vision без CDP',
      color: 'bg-danger-muted text-danger border-danger/30',
      message: vision?.runtime_status_message || 'Профиль запущен, но CDP-порт недоступен.',
    };
  }
  if (status === 'API_UNAVAILABLE') {
    return {
      label: 'Vision API недоступен',
      color: 'bg-danger-muted text-danger border-danger/30',
      message: vision?.runtime_status_message || 'Не удалось подключиться к Vision API.',
    };
  }
  return {
    label: 'Vision не настроен',
    color: 'bg-elevated text-muted border-border',
    message: vision?.runtime_status_message || 'Vision X-Token или профиль ещё не настроены.',
  };
}

/** Компактная полоса: алерты + статус скана + авто-включение */
export function DashboardCommandBar({
  stats,
  settings,
  vision,
  scanning,
  onToggle,
  onResume,
  onScanNow,
  onAutoEnableToggle,
  onStopClick,
  onWarningClick,
}) {
  const stopCount = stats?.ads_in_stop ?? 0;
  const warnCount = stats?.ads_in_warning ?? 0;
  const allClear = stopCount === 0 && warnCount === 0;

  const pauseUntilMs = settings?.pause_until ? new Date(settings.pause_until).getTime() : null;
  const pauseActive = pauseUntilMs != null && pauseUntilMs > Date.now();
  const pauseMinsLeft = pauseActive ? Math.max(1, Math.round((pauseUntilMs - Date.now()) / 60000)) : null;

  const parsedStatus = parseObserverStatusMessage(stats?.observer_status_message);
  const intervalSec = stats?.current_scan_interval_seconds ?? parsedStatus.intervalSec;
  const threatLevel = stats?.current_scan_threat_level ?? parsedStatus.threatLevel;
  const badge = threatLevel ? THREAT_BADGE[threatLevel] : null;
  const visionMeta = getVisionRuntimeMeta(vision);

  const [secsLeft, setSecsLeft] = useState(null);
  const lastScanAt = stats?.last_scan_at;
  const nextScanAt = stats?.next_scan_at;
  const observerStatus = stats?.observer_status;
  const observerStatusMessage = stats?.observer_status_message;

  useEffect(() => {
    const explicitNextScanAt = nextScanAt ? new Date(nextScanAt).getTime() : null;
    const fallbackNextScanAt = lastScanAt && intervalSec
      ? new Date(lastScanAt).getTime() + intervalSec * 1000
      : null;
    const targetScanAt = Number.isFinite(explicitNextScanAt) ? explicitNextScanAt : fallbackNextScanAt;
    if (!targetScanAt) {
      setSecsLeft(null);
      return undefined;
    }
    const tick = () => setSecsLeft(Math.max(0, Math.round((targetScanAt - Date.now()) / 1000)));
    tick();
    const id = setInterval(tick, 1000);
    return () => clearInterval(id);
  }, [lastScanAt, intervalSec, nextScanAt]);

  const isEnabled = settings?.is_scanning_enabled ?? false;
  const isWaitingNextScan = isEnabled
    && observerStatus === 'RUNNING'
    && (Boolean(nextScanAt) || /^Ожидаем следующий цикл/i.test(observerStatusMessage || ''));
  const isActivelyScanning = scanning || (observerStatus === 'RUNNING' && !isWaitingNextScan);

  let statusText = '';
  let statusDetail = '';
  let statusColor = 'text-muted';
  let showDot = false;

  if (!isEnabled) {
    statusText = 'Выключено';
  } else if (observerStatus === 'WAITING_BROWSER') {
    const rawMessage = (observerStatusMessage || '').trim();
    const isEnableQueue = /^Браузер занят задачами включения/i.test(rawMessage);
    const isDisableQueue = /^Браузер занят задачами отключения/i.test(rawMessage);
    statusText = isEnableQueue
      ? 'Браузер занят включением'
      : isDisableQueue
        ? 'Браузер занят отключением'
        : 'Браузер занят';
    statusColor = 'text-warning';
  } else if (observerStatus === 'DISABLING') {
    statusText = 'Отключаем…';
    statusColor = 'text-warning';
    showDot = true;
  } else if (observerStatus === 'ERROR') {
    statusText = 'Нет подключения к браузеру';
    statusColor = 'text-danger';
  } else if (observerStatus === 'PAUSED') {
    statusText = observerStatusMessage ?? 'Пауза';
    statusColor = 'text-warning';
  } else if (isWaitingNextScan) {
    statusText = 'Ожидание';
    statusColor = 'text-secondary';
  } else if (isActivelyScanning) {
    statusText = 'Сканирую…';
    statusColor = 'text-success';
    showDot = true;
  } else if (isEnabled) {
    statusText = 'Ожидание';
    statusColor = 'text-secondary';
  }

  const showCountdown = isEnabled && isWaitingNextScan && secsLeft !== null && secsLeft > 0;
  const showLastScan = isEnabled && !isActivelyScanning && !showCountdown && lastScanAt;
  const autoEnableOn = settings?.auto_enable_recommendations ?? false;

  return (
    <div className={`panel-ops mb-md overflow-hidden ${isActivelyScanning ? 'animate-scan-pulse' : ''}`}>
      <div className="flex flex-wrap items-center gap-3 border-b border-border/40 px-4 py-3">
        {allClear ? (
          <div className="flex items-center gap-2">
            <span className="status-dot bg-success animate-pulse-dot" />
            <span className="text-sm font-medium text-success">Все объявления в норме</span>
          </div>
        ) : (
          <>
            {stopCount > 0 && (
              <button
                type="button"
                className="flex items-center gap-2 rounded-md px-1 py-0.5 transition-colors hover:bg-danger-muted/40"
                onClick={onStopClick}
                title="Перейти к объявлениям со стоп-алертом"
              >
                <span className="status-dot bg-danger animate-pulse-dot" />
                <span className="font-mono text-xl text-danger">{stopCount}</span>
                <span className="text-2xs uppercase tracking-wider text-danger/70">СТОП</span>
              </button>
            )}
            {warnCount > 0 && (
              <button
                type="button"
                className="flex items-center gap-2 rounded-md px-1 py-0.5 transition-colors hover:bg-warning-muted/40"
                onClick={onWarningClick}
                title="Перейти к ленте предупреждений"
              >
                <span className="status-dot bg-warning animate-pulse-dot" />
                <span className="font-mono text-xl text-warning">{warnCount}</span>
                <span className="text-2xs uppercase tracking-wider text-warning/70">WARNING</span>
              </button>
            )}
          </>
        )}

        <span className="hidden h-6 w-px bg-border/60 sm:block" aria-hidden="true" />

        <button
          onClick={onToggle}
          className="toggle-track"
          data-active={isEnabled}
          role="switch"
          aria-checked={isEnabled}
          aria-label={isEnabled ? 'Выключить сканирование' : 'Включить сканирование'}
        >
          <span className="toggle-knob" data-active={isEnabled} />
        </button>
        <span className="text-2xs font-bold uppercase tracking-widest text-secondary">Скан</span>

        {pauseActive && (
          <span className="flex items-center gap-1.5">
            <span className="rounded-full bg-warning/20 px-2 py-0.5 text-2xs font-semibold text-warning">
              ⏸ до {new Date(settings.pause_until).toLocaleTimeString('ru-RU', { hour: '2-digit', minute: '2-digit' })} ({pauseMinsLeft} мин)
            </span>
            <button type="button" className="btn-ghost text-2xs px-1.5 py-0.5" onClick={onResume}>
              ▶ Возобновить
            </button>
          </span>
        )}

        <span className={`flex items-center gap-1.5 text-2xs font-medium ${statusColor}`}>
          {showDot && <span className="status-dot bg-accent status-dot-pulse" />}
          {statusText}
        </span>
        {statusDetail && <span className="text-[11px] text-muted">{statusDetail}</span>}

        {badge && (
          <span className={`rounded-full px-2 py-0.5 text-2xs font-semibold ${badge.color}`}>
            {badge.label}
            {intervalSec ? ` ${formatScanDuration(intervalSec)}` : ''}
            {stats?.current_scan_jitter_seconds ? ` ±${formatScanDuration(stats.current_scan_jitter_seconds)}` : ''}
          </span>
        )}

        {visionMeta && (
          <span
            className={`rounded-full border px-2 py-0.5 text-2xs font-semibold ${visionMeta.color}`}
            title={visionMeta.message}
          >
            {visionMeta.label}
          </span>
        )}

        {showCountdown && (
          <span className="font-mono text-sm font-semibold text-secondary">
            {Math.floor(secsLeft / 60)}:{String(secsLeft % 60).padStart(2, '0')}
          </span>
        )}
        {showLastScan && (
          <span className="font-mono text-2xs text-muted">
            {new Date(lastScanAt).toLocaleTimeString('ru-RU')}
          </span>
        )}

        <button
          type="button"
          className="btn-ghost ml-auto flex items-center gap-1.5"
          onClick={onScanNow}
          disabled={scanning}
        >
          <span className={scanning ? 'animate-spin' : ''}>↻</span>
          {scanning ? 'Сканирую' : 'Обновить'}
        </button>
      </div>

      <div className="flex flex-wrap items-center gap-3 px-4 py-2.5">
        <button
          onClick={onAutoEnableToggle}
          className="toggle-track"
          data-active={autoEnableOn}
          role="switch"
          aria-checked={autoEnableOn}
          aria-label={autoEnableOn ? 'Выключить авто-включение' : 'Включить авто-включение'}
        >
          <span className="toggle-knob" data-active={autoEnableOn} />
        </button>
        <span className="text-2xs font-bold uppercase tracking-widest text-secondary">Авто-включение</span>
        <span className="text-2xs text-muted">
          {autoEnableOn
            ? 'Рекомендации принимаются автоматически'
            : 'Рекомендации требуют ручного подтверждения'}
        </span>
      </div>
    </div>
  );
}
