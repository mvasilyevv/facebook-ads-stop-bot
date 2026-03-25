import { useState, useEffect, useCallback } from 'react';
import {
  getObserverSettings,
  updateObserverSettings,
  getTelegramSettings,
  updateTelegramSettings,
} from '../api.js';

/* Тогл-переключатель с доступностью */
function Toggle({ on, onChange, label }) {
  return (
    <button
      className={`toggle-switch ${on ? 'on' : ''}`}
      onClick={() => onChange(!on)}
      type="button"
      role="switch"
      aria-checked={on}
      aria-label={label}
    />
  );
}

/* Уведомление */
function Toast({ message, type, onClose }) {
  useEffect(() => {
    const timer = setTimeout(onClose, 3000);
    return () => clearTimeout(timer);
  }, [onClose]);
  return (
    <div className={`toast toast-${type}`} role="alert">
      {message}
    </div>
  );
}

export default function SettingsPage() {
  const [observer, setObserver] = useState({
    interval_seconds: 90,
    jitter_seconds: 10,
    warning_percent_of_stop: 80,
  });
  const [telegram, setTelegram] = useState({
    bot_token: '',
    chat_id: '',
  });
  const [loading, setLoading] = useState(true);
  const [saving, setSaving] = useState('');
  const [toast, setToast] = useState(null);

  /* Загрузка настроек с API */
  const fetchSettings = useCallback(async () => {
    try {
      const [obsData, tgData] = await Promise.all([
        getObserverSettings().catch(() => null),
        getTelegramSettings().catch(() => null),
      ]);
      if (obsData && typeof obsData === 'object') {
        setObserver({
          interval_seconds: obsData.interval_seconds ?? 90,
          jitter_seconds: obsData.jitter_seconds ?? 10,
          warning_percent_of_stop: obsData.warning_percent_of_stop ?? 80,
        });
      }
      if (tgData && typeof tgData === 'object') {
        setTelegram({
          bot_token: tgData.bot_token || '',
          chat_id: tgData.chat_id || '',
        });
      }
    } catch {
      /* Настройки не найдены — используем дефолты */
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    fetchSettings();
  }, [fetchSettings]);

  /* Сохранение настроек Observer */
  const handleSaveObserver = async () => {
    setSaving('observer');
    try {
      await updateObserverSettings(observer);
      setToast({ message: 'Настройки Observer сохранены', type: 'success' });
    } catch (err) {
      setToast({ message: err.message || 'Ошибка сохранения', type: 'error' });
    } finally {
      setSaving('');
    }
  };

  /* Сохранение настроек Telegram */
  const handleSaveTelegram = async () => {
    setSaving('telegram');
    try {
      await updateTelegramSettings(telegram);
      setToast({ message: 'Настройки Telegram сохранены', type: 'success' });
    } catch (err) {
      setToast({ message: err.message || 'Ошибка сохранения', type: 'error' });
    } finally {
      setSaving('');
    }
  };

  if (loading) {
    return (
      <div className="animate-in">
        <div className="page-header">
          <div>
            <h1 className="page-title">Настройки</h1>
            <div className="page-subtitle">Загрузка...</div>
          </div>
        </div>
        <div className="loading-state">
          <div className="spinner" />
          <div>Загрузка настроек...</div>
        </div>
      </div>
    );
  }

  return (
    <div className="animate-in">
      <div className="page-header">
        <div>
          <h1 className="page-title">Настройки</h1>
          <div className="page-subtitle">Конфигурация Observer, Telegram и браузера</div>
        </div>
      </div>

      {/* Observer — частота обновления */}
      <section aria-label="Настройки Observer" className="form-section">
        <div className="form-section-title">Observer — частота обновления</div>
        <div className="form-grid">
          <div className="form-group">
            <label className="form-label" htmlFor="obs-interval">
              Интервал обновления (сек)
            </label>
            <input
              id="obs-interval"
              className="form-input"
              type="number"
              min="10"
              max="600"
              value={observer.interval_seconds}
              onChange={(e) => setObserver({ ...observer, interval_seconds: +e.target.value })}
            />
            <div className="form-hint">
              Как часто бот обновляет страницу Ads Manager. Рекомендуется 60-120 сек.
            </div>
          </div>
          <div className="form-group">
            <label className="form-label" htmlFor="obs-jitter">
              Jitter (сек)
            </label>
            <input
              id="obs-jitter"
              className="form-input"
              type="number"
              min="0"
              max="60"
              value={observer.jitter_seconds}
              onChange={(e) => setObserver({ ...observer, jitter_seconds: +e.target.value })}
            />
            <div className="form-hint">Случайное отклонение ± сек для имитации человека.</div>
          </div>
          <div className="form-group">
            <label className="form-label" htmlFor="obs-warning">
              Порог предупреждения (% от стопа)
            </label>
            <input
              id="obs-warning"
              className="form-input"
              type="number"
              min="50"
              max="99"
              value={observer.warning_percent_of_stop}
              onChange={(e) =>
                setObserver({ ...observer, warning_percent_of_stop: +e.target.value })
              }
            />
            <div className="form-hint">
              При 80% — предупреждение приходит когда метрика достигает 80% от стоп-порога.
            </div>
          </div>
        </div>
        <div style={{ marginTop: 16 }}>
          <button
            className="btn btn-primary"
            onClick={handleSaveObserver}
            disabled={saving === 'observer'}
          >
            {saving === 'observer' ? 'Сохранение...' : 'Сохранить настройки Observer'}
          </button>
        </div>
      </section>

      {/* Telegram — уведомления */}
      <section aria-label="Настройки Telegram" className="form-section">
        <div className="form-section-title">Telegram — уведомления</div>
        <div className="form-grid">
          <div className="form-group">
            <label className="form-label" htmlFor="tg-token">
              Bot Token
            </label>
            <input
              id="tg-token"
              className="form-input"
              type="password"
              placeholder="123456:ABC-DEF1234ghIkl-..."
              value={telegram.bot_token}
              onChange={(e) => setTelegram({ ...telegram, bot_token: e.target.value })}
              autoComplete="off"
            />
          </div>
          <div className="form-group">
            <label className="form-label" htmlFor="tg-chat-id">
              Chat ID
            </label>
            <input
              id="tg-chat-id"
              className="form-input"
              type="text"
              placeholder="-1001234567890"
              value={telegram.chat_id}
              onChange={(e) => setTelegram({ ...telegram, chat_id: e.target.value })}
            />
          </div>
        </div>
        <div style={{ marginTop: 16 }}>
          <button
            className="btn btn-primary"
            onClick={handleSaveTelegram}
            disabled={saving === 'telegram'}
          >
            {saving === 'telegram' ? 'Сохранение...' : 'Сохранить настройки Telegram'}
          </button>
        </div>
      </section>

      {/* Информация о браузере */}
      <section aria-label="Настройки браузера" className="form-section">
        <div className="form-section-title">Anti-detect браузер</div>
        <div style={{ color: 'var(--text-secondary)', fontSize: 14, lineHeight: 1.6 }}>
          <p style={{ marginBottom: 8 }}>
            Подключение к Vision anti-detect браузеру настраивается через переменные окружения:
          </p>
          <ul style={{ paddingLeft: 20, display: 'flex', flexDirection: 'column', gap: 4 }}>
            <li>
              <code style={{ color: 'var(--accent-purple)' }}>VISION_X_TOKEN</code> — токен авторизации Vision API
            </li>
            <li>
              <code style={{ color: 'var(--accent-purple)' }}>VISION_PROFILE_ID</code> — ID профиля браузера
            </li>
            <li>
              <code style={{ color: 'var(--accent-purple)' }}>VISION_API_URL</code> — адрес Vision API (по умолчанию http://127.0.0.1:3030)
            </li>
          </ul>
          <p style={{ marginTop: 12, color: 'var(--text-muted)', fontSize: 13 }}>
            Убедитесь, что профиль запущен в Vision перед стартом Observer.
          </p>
        </div>
      </section>

      {/* Toast */}
      {toast && <Toast message={toast.message} type={toast.type} onClose={() => setToast(null)} />}
    </div>
  );
}
