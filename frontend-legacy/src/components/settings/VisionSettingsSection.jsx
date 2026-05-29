export function VisionSettingsSection({
  vision,
  showVisionToken,
  onToggleTokenVisibility,
  onVisionChange,
  onSave,
  onReconnect,
  onSaveColumnWidths,
  onApplyColumnWidths,
  saving,
  browserOpen,
  onToggleBrowserOpen,
}) {
  const autoRecoveryEnabled = Boolean(vision.auto_restart_on_missing_cdp);

  return (
    <section aria-label="Настройки браузера" className="panel">
      {/* Заголовок-аккордеон */}
      <button
        className="flex w-full items-center justify-between px-5 py-4 text-left"
        onClick={onToggleBrowserOpen}
        aria-expanded={browserOpen}
      >
        <span className="text-base font-semibold text-primary">Anti-detect браузер (Vision)</span>
        <span className="flex items-center gap-2 text-2xs text-muted">
          {vision.has_token && <span className="badge-success">настроен</span>}
          {browserOpen ? '▼' : '▶'}
        </span>
      </button>

      {browserOpen && (
        <div className="space-y-4 border-t border-border px-5 py-4">
          <div className="grid grid-cols-1 sm:grid-cols-2 gap-4">
            <div>
              <label className="mb-1 block text-2xs font-semibold uppercase tracking-wider text-secondary" htmlFor="vision-url">
                Vision API URL
              </label>
              <input
                id="vision-url"
                className="w-full rounded bg-elevated border border-border px-3 py-2 text-sm text-primary focus:border-accent focus:ring-1 focus:ring-accent focus:outline-none"
                type="text"
                placeholder="http://127.0.0.1:3030"
                value={vision.api_url}
                onChange={(e) => onVisionChange({ ...vision, api_url: e.target.value })}
              />
            </div>
            <div>
              <label className="mb-1 block text-2xs font-semibold uppercase tracking-wider text-secondary" htmlFor="vision-token">
                X-Token {vision.has_token && <span className="text-success">(сохранён)</span>}
              </label>
              <div className="flex gap-1">
                <input
                  id="vision-token"
                  className="flex-1 rounded bg-elevated border border-border px-3 py-2 text-sm text-primary focus:border-accent focus:ring-1 focus:ring-accent focus:outline-none"
                  type={showVisionToken ? 'text' : 'password'}
                  placeholder={vision.has_token ? '••••• (оставьте пустым)' : 'Введите X-Token'}
                  value={vision.x_token}
                  onChange={(e) => onVisionChange({ ...vision, x_token: e.target.value })}
                  autoComplete="off"
                />
                <button className="btn-ghost px-2" onClick={onToggleTokenVisibility} aria-label={showVisionToken ? 'Скрыть' : 'Показать'}>
                  {showVisionToken ? '🙈' : '👁'}
                </button>
              </div>
              <div className="mt-1 text-2xs text-muted">Токен хранится в зашифрованном виде.</div>
            </div>
          </div>

          <div className="rounded-md border border-border bg-elevated/40 px-4 py-3">
            <div className="mb-1 text-2xs font-semibold uppercase tracking-wider text-secondary">
              Автоподбор профиля
            </div>
            <div className="flex flex-wrap items-center gap-2">
              {vision.profile_id ? (
                <>
                  <span className="badge-success">выбран</span>
                  <span className="font-mono text-sm text-primary">{vision.profile_id}</span>
                </>
              ) : (
                <>
                  <span className="badge-warning">ожидает выбора</span>
                  <span className="text-sm text-secondary">
                    Сохраните X-Token: единственный профиль Vision выберется автоматически.
                  </span>
                </>
              )}
            </div>
          </div>

          <div
            className={`rounded-md border px-4 py-3 ${
              autoRecoveryEnabled
                ? 'border-success/25 bg-success/10'
                : 'border-warning/25 bg-warning/10'
            }`}
          >
            <div className="flex flex-wrap items-center justify-between gap-2">
              <div>
                <div className="text-2xs font-semibold uppercase tracking-wider text-secondary">
                  Автовосстановление CDP
                </div>
                <p className="mt-1 text-sm text-primary">
                  {autoRecoveryEnabled
                    ? 'Включено: browser-agent сам перезапустит Vision-профиль, если порт CDP не появился.'
                    : 'Выключено через .env: если Vision зависнет без CDP-порта, потребуется ручной перезапуск профиля.'}
                </p>
              </div>
              <span className={autoRecoveryEnabled ? 'badge-success' : 'badge-warning'}>
                {autoRecoveryEnabled ? 'авто' : 'ручной режим'}
              </span>
            </div>
            <p className="mt-2 text-2xs text-muted">
              {autoRecoveryEnabled
                ? 'Принудительный перезапуск ниже оставлен как запасной сценарий, а не как ежедневное действие.'
                : 'Автоматический recovery включён по умолчанию. Уберите VISION_AUTO_RESTART_ON_MISSING_CDP=false из .env и перезапустите сервисы.'}
            </p>
          </div>

          <div className="flex flex-wrap gap-2 pt-2">
            <button className="btn-primary" onClick={onSave} disabled={saving === 'vision'}>
              {saving === 'vision' ? 'Сохранение...' : 'Сохранить настройки'}
            </button>
            <button className="btn-secondary" onClick={onReconnect} disabled={saving === 'reconnect'} title="Перезапуск профиля Vision + переподключение observer">
              {saving === 'reconnect' ? 'Отправка...' : 'Принудительный перезапуск'}
            </button>
            <button
              className="btn-secondary"
              onClick={onSaveColumnWidths}
              disabled={saving === 'save-column-widths'}
              title="Сохранить текущие ручные ширины колонок Ads Manager"
            >
              {saving === 'save-column-widths' ? 'Сохранение...' : 'Сохранить ширины'}
            </button>
            <button
              className="btn-secondary"
              onClick={onApplyColumnWidths}
              disabled={saving === 'column-widths'}
              title="Применить сохранённую ширину колонок Ads Manager"
            >
              {saving === 'column-widths' ? 'Применение...' : 'Автоширина колонок'}
            </button>
            {vision.column_widths_saved_count > 0 && (
              <span className="self-center text-2xs text-muted">
                Сохранено: {vision.column_widths_saved_count} колонок
              </span>
            )}
          </div>
        </div>
      )}
    </section>
  );
}
