export function VisionSettingsSection({
  vision,
  visionProfiles,
  showVisionToken,
  onToggleTokenVisibility,
  onVisionChange,
  onLoadProfiles,
  profilesLoading,
  onSave,
  onReconnect,
  saving,
  browserOpen,
  onToggleBrowserOpen,
}) {
  return (
    <section aria-label="Настройки браузера" className="form-section">
      <div
        className="form-section-title settings-section-toggle"
        onClick={onToggleBrowserOpen}
        role="button"
        tabIndex={0}
        aria-expanded={browserOpen}
        onKeyDown={(event) => {
          if (event.key === 'Enter' || event.key === ' ') {
            event.preventDefault();
            onToggleBrowserOpen();
          }
        }}
      >
        <span>Anti-detect браузер (Vision)</span>
        <span className="settings-section-toggle__meta">
          {browserOpen ? 'Скрыть' : 'Показать'}
          {vision.has_token && (
            <span className="settings-section-toggle__status">✓ настроен</span>
          )}
        </span>
      </div>

      {browserOpen && (
        <div className="settings-stack settings-stack--lg">
          <div className="form-grid settings-form-grid">
            <div className="form-group">
              <label className="form-label" htmlFor="vision-url">
                Vision API URL
              </label>
              <input
                id="vision-url"
                className="form-input"
                type="text"
                placeholder="http://127.0.0.1:3030"
                value={vision.api_url}
                onChange={(event) => onVisionChange({ ...vision, api_url: event.target.value })}
              />
            </div>

            <div className="form-group">
              <label className="form-label" htmlFor="vision-token">
                X-Token{' '}
                {vision.has_token && (
                  <span className="settings-inline-success">(сохранён)</span>
                )}
              </label>
              <div className="settings-token-field">
                <input
                  id="vision-token"
                  className="form-input settings-token-field__input"
                  type={showVisionToken ? 'text' : 'password'}
                  placeholder={
                    vision.has_token
                      ? '••••••••••• (оставьте пустым, чтобы не менять)'
                      : 'Введите X-Token'
                  }
                  value={vision.x_token}
                  onChange={(event) => onVisionChange({ ...vision, x_token: event.target.value })}
                  autoComplete="off"
                />
                <button
                  type="button"
                  className="settings-token-field__toggle"
                  onClick={onToggleTokenVisibility}
                  aria-label={showVisionToken ? 'Скрыть токен' : 'Показать токен'}
                >
                  {showVisionToken ? '🙈' : '👁'}
                </button>
              </div>
              <div className="form-hint">Токен хранится в зашифрованном виде.</div>
            </div>
          </div>

          <div className="form-group settings-form-block">
            <label className="form-label" htmlFor="vision-profile">
              Профиль браузера
            </label>
            <div className="settings-field-row">
              {visionProfiles.length > 0 ? (
                <select
                  id="vision-profile"
                  className="form-input"
                  value={vision.profile_id}
                  onChange={(event) => onVisionChange({ ...vision, profile_id: event.target.value })}
                >
                  <option value="">— Выберите профиль —</option>
                  {visionProfiles.map((profile) => (
                    <option key={profile.profile_id} value={profile.profile_id}>
                      {profile.name || profile.profile_id}
                      {profile.port ? ` (порт ${profile.port})` : ''}
                    </option>
                  ))}
                </select>
              ) : (
                <input
                  id="vision-profile"
                  className="form-input"
                  type="text"
                  placeholder="ID профиля Vision"
                  value={vision.profile_id}
                  onChange={(event) => onVisionChange({ ...vision, profile_id: event.target.value })}
                />
              )}
              <button
                className="btn btn-outline btn-sm settings-nowrap"
                onClick={onLoadProfiles}
                disabled={profilesLoading}
              >
                {profilesLoading ? '...' : 'Загрузить список'}
              </button>
            </div>
            <div className="form-hint">
              Нажмите «Загрузить список», чтобы получить доступные профили из Vision.
            </div>
          </div>

          <div className="settings-actions">
            <button className="btn btn-primary" onClick={onSave} disabled={saving === 'vision'}>
              {saving === 'vision' ? 'Сохранение...' : 'Сохранить'}
            </button>
            <button
              className="btn btn-outline"
              onClick={onReconnect}
              disabled={saving === 'reconnect'}
              title="Сразу перезапускает профиль Vision и запускает автоматическое переподключение observer. Используйте, если браузер завис или потерял CDP-порт."
            >
              {saving === 'reconnect' ? 'Отправка...' : 'Переподключить браузер'}
            </button>
            <div className="form-hint settings-note">
              Профиль Vision будет перезапущен сразу, без ожидания следующего скана.
            </div>
          </div>
        </div>
      )}
    </section>
  );
}
