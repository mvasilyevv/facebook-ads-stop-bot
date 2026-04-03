export function VisionSettingsSection({ vision, visionProfiles, showVisionToken, onToggleTokenVisibility, onVisionChange, onLoadProfiles, profilesLoading, onSave, onReconnect, saving, browserOpen, onToggleBrowserOpen }) {
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

          <div>
            <label className="mb-1 block text-2xs font-semibold uppercase tracking-wider text-secondary" htmlFor="vision-profile">
              Профиль браузера
            </label>
            <div className="flex gap-2">
              {visionProfiles.length > 0 ? (
                <select
                  id="vision-profile"
                  className="flex-1 rounded bg-elevated border border-border px-3 py-2 text-sm text-primary"
                  value={vision.profile_id}
                  onChange={(e) => onVisionChange({ ...vision, profile_id: e.target.value })}
                >
                  <option value="">— Выберите профиль —</option>
                  {visionProfiles.map((p) => (
                    <option key={p.profile_id} value={p.profile_id}>
                      {p.name || p.profile_id}{p.port ? ` (порт ${p.port})` : ''}
                    </option>
                  ))}
                </select>
              ) : (
                <input
                  id="vision-profile"
                  className="flex-1 rounded bg-elevated border border-border px-3 py-2 text-sm text-primary focus:border-accent focus:ring-1 focus:ring-accent focus:outline-none"
                  type="text"
                  placeholder="ID профиля Vision"
                  value={vision.profile_id}
                  onChange={(e) => onVisionChange({ ...vision, profile_id: e.target.value })}
                />
              )}
              <button className="btn-secondary whitespace-nowrap" onClick={onLoadProfiles} disabled={profilesLoading}>
                {profilesLoading ? '...' : 'Загрузить список'}
              </button>
            </div>
          </div>

          <div className="flex flex-wrap gap-2 pt-2">
            <button className="btn-primary" onClick={onSave} disabled={saving === 'vision'}>
              {saving === 'vision' ? 'Сохранение...' : 'Сохранить'}
            </button>
            <button className="btn-secondary" onClick={onReconnect} disabled={saving === 'reconnect'} title="Перезапуск профиля Vision + переподключение observer">
              {saving === 'reconnect' ? 'Отправка...' : 'Переподключить браузер'}
            </button>
          </div>
        </div>
      )}
    </section>
  );
}
