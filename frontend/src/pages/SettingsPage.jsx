import { useState } from 'react';

function Toggle({ on, onChange }) {
  return (
    <button
      className={`toggle-switch ${on ? 'on' : ''}`}
      onClick={() => onChange(!on)}
      type="button"
    />
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

  const [cdp, setCdp] = useState({
    endpoint_url: '',
    headless: false,
  });

  const [saved, setSaved] = useState('');

  const handleSave = (section) => {
    // TODO: вызов API
    setSaved(section);
    setTimeout(() => setSaved(''), 2000);
  };

  return (
    <div className="animate-in">
      <div className="page-header">
        <div>
          <h1 className="page-title">Настройки</h1>
          <div className="page-subtitle">Конфигурация бота, Telegram, интервалы и браузер</div>
        </div>
      </div>

      {/* Observer */}
      <div className="form-section">
        <div className="form-section-title">
          🔄 Observer — частота обновления
          {saved === 'observer' && <span className="badge badge-success" style={{ marginLeft: 8 }}>✅ Сохранено</span>}
        </div>
        <div className="form-grid">
          <div className="form-group">
            <label className="form-label">Интервал обновления (сек)</label>
            <input
              className="form-input"
              type="number"
              min="10"
              max="600"
              value={observer.interval_seconds}
              onChange={(e) => setObserver({ ...observer, interval_seconds: +e.target.value })}
            />
            <div style={{ fontSize: 11, color: 'var(--text-muted)', marginTop: 4 }}>
              Как часто бот обновляет страницу Ads Manager. Рекомендуется 60-120 сек.
            </div>
          </div>
          <div className="form-group">
            <label className="form-label">Jitter (сек)</label>
            <input
              className="form-input"
              type="number"
              min="0"
              max="60"
              value={observer.jitter_seconds}
              onChange={(e) => setObserver({ ...observer, jitter_seconds: +e.target.value })}
            />
            <div style={{ fontSize: 11, color: 'var(--text-muted)', marginTop: 4 }}>
              Случайное отклонение ± сек для имитации человека.
            </div>
          </div>
          <div className="form-group">
            <label className="form-label">Порог предупреждения (% от стопа)</label>
            <input
              className="form-input"
              type="number"
              min="50"
              max="99"
              value={observer.warning_percent_of_stop}
              onChange={(e) => setObserver({ ...observer, warning_percent_of_stop: +e.target.value })}
            />
            <div style={{ fontSize: 11, color: 'var(--text-muted)', marginTop: 4 }}>
              При 80% — предупреждение приходит когда метрика достигает 80% от стоп-порога.
            </div>
          </div>
        </div>
        <div style={{ marginTop: 16 }}>
          <button className="btn btn-primary" onClick={() => handleSave('observer')}>
            💾 Сохранить настройки Observer
          </button>
        </div>
      </div>

      {/* Telegram */}
      <div className="form-section">
        <div className="form-section-title">
          📱 Telegram — уведомления
          {saved === 'telegram' && <span className="badge badge-success" style={{ marginLeft: 8 }}>✅ Сохранено</span>}
        </div>
        <div className="form-grid">
          <div className="form-group">
            <label className="form-label">Bot Token</label>
            <input
              className="form-input"
              type="password"
              placeholder="123456:ABC-DEF1234ghIkl-..."
              value={telegram.bot_token}
              onChange={(e) => setTelegram({ ...telegram, bot_token: e.target.value })}
            />
          </div>
          <div className="form-group">
            <label className="form-label">Chat ID</label>
            <input
              className="form-input"
              type="text"
              placeholder="-1001234567890"
              value={telegram.chat_id}
              onChange={(e) => setTelegram({ ...telegram, chat_id: e.target.value })}
            />
          </div>
        </div>
        <div style={{ marginTop: 16 }}>
          <button className="btn btn-primary" onClick={() => handleSave('telegram')}>
            💾 Сохранить настройки Telegram
          </button>
        </div>
      </div>

      {/* CDP / Браузер */}
      <div className="form-section">
        <div className="form-section-title">
          🌐 Anti-detect браузер
          {saved === 'cdp' && <span className="badge badge-success" style={{ marginLeft: 8 }}>✅ Сохранено</span>}
        </div>
        <div className="form-grid">
          <div className="form-group">
            <label className="form-label">CDP Endpoint URL</label>
            <input
              className="form-input"
              type="text"
              placeholder="ws://localhost:9222/devtools/browser/..."
              value={cdp.endpoint_url}
              onChange={(e) => setCdp({ ...cdp, endpoint_url: e.target.value })}
            />
            <div style={{ fontSize: 11, color: 'var(--text-muted)', marginTop: 4 }}>
              WebSocket URL для подключения к anti-detect браузеру через CDP.
            </div>
          </div>
        </div>
        <div style={{ marginTop: 16 }}>
          <button className="btn btn-primary" onClick={() => handleSave('cdp')}>
            💾 Сохранить настройки браузера
          </button>
        </div>
      </div>
    </div>
  );
}
