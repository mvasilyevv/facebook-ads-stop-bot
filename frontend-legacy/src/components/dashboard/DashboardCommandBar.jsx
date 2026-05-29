const THREAT_BADGE = {
  IMMEDIATE: { label: 'Ре-скан', color: 'bg-danger-muted text-danger animate-pulse' },
  CRITICAL: { label: 'Критично', color: 'bg-danger-muted text-danger' },
  ELEVATED: { label: 'Повышенно', color: 'bg-warning-muted text-warning' },
  ACTIVE: { label: 'Активно', color: 'bg-accent-muted text-accent' },
  CALM: { label: 'Спокойно', color: 'bg-success-muted text-success' },
  IDLE: { label: 'Ожидание', color: 'bg-elevated text-muted' },
};

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

// Компактная полоса: алерты, тогл сканирования, пауза, угроза, vision, кнопка «Обновить».
// Статус observer'а (фаза, последний цикл, ошибки) теперь живёт в отдельной плитке ObserverStatusTile.
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

  const intervalSec = stats?.current_scan_interval_seconds ?? null;
  const threatLevel = stats?.current_scan_threat_level ?? null;
  const badge = threatLevel ? THREAT_BADGE[threatLevel] : null;
  const visionMeta = getVisionRuntimeMeta(vision);

  const isEnabled = settings?.is_scanning_enabled ?? false;
  const autoEnableOn = settings?.auto_enable_recommendations ?? false;

  return (
    <div className={`panel-ops mb-md overflow-hidden ${scanning ? 'animate-scan-pulse' : ''}`}>
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
