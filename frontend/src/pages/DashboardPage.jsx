import { useState, useEffect, useCallback } from 'react';
import { getDashboardStats, getAlertEvents, getSpendHistory } from '../api.js';

/* Вспомогательные функции рендеринга бейджей */
function stageBadge(stage) {
  if (stage === 'STOP') return <span className="badge badge-danger">🛑 STOP</span>;
  return <span className="badge badge-warning">⚠️ WARNING</span>;
}

function stateBadge(state) {
  const map = {
    STOP_SENT: { cls: 'badge-danger', text: 'Ожидает' },
    WARNING_SENT: { cls: 'badge-warning', text: 'Предупр.' },
    CLAIMED: { cls: 'badge-info', text: 'В работе' },
    DISABLED: { cls: 'badge-success', text: 'Выкл.' },
    NORMAL: { cls: 'badge-muted', text: 'Норма' },
  };
  const b = map[state] || map.NORMAL;
  return <span className={`badge ${b.cls}`}>{b.text}</span>;
}

/* Форматирование времени алерта */
function formatTime(isoStr) {
  if (!isoStr) return '—';
  try {
    const d = new Date(isoStr);
    const now = new Date();
    const diffMs = now - d;
    const diffMin = Math.floor(diffMs / 60000);
    if (diffMin < 1) return 'Только что';
    if (diffMin < 60) return `${diffMin} мин назад`;
    const diffH = Math.floor(diffMin / 60);
    if (diffH < 24) return `${diffH} ч назад`;
    return d.toLocaleDateString('ru');
  } catch {
    return '—';
  }
}

export default function DashboardPage() {
  const [stats, setStats] = useState(null);
  const [alerts, setAlerts] = useState([]);
  const [spendBars, setSpendBars] = useState([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState(null);
  const [alertFilter, setAlertFilter] = useState('all');

  /* Загрузка данных с API */
  const fetchData = useCallback(async () => {
    try {
      setError(null);
      const [statsData, alertsData, spendData] = await Promise.all([
        getDashboardStats(),
        getAlertEvents({ limit: 20 }),
        getSpendHistory({ hours: 24 }),
      ]);
      setStats(statsData);
      setAlerts(Array.isArray(alertsData) ? alertsData : []);
      /* Нормализуем данные расходов для графика */
      if (Array.isArray(spendData) && spendData.length > 0) {
        const maxSpend = Math.max(...spendData.map((s) => s.spend || 0), 1);
        setSpendBars(
          spendData.map((s) => ({
            hour: s.hour,
            spend: s.spend || 0,
            pct: Math.max(4, ((s.spend || 0) / maxSpend) * 100),
          })),
        );
      } else {
        setSpendBars([]);
      }
    } catch (err) {
      setError(err.message || 'Не удалось загрузить данные');
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    fetchData();
    /* Автообновление каждые 30 секунд */
    const timer = setInterval(fetchData, 30000);
    return () => clearInterval(timer);
  }, [fetchData]);

  /* Фильтрация алертов */
  const filteredAlerts = alerts.filter((a) => {
    if (alertFilter === 'all') return true;
    if (alertFilter === 'warning') return a.stage === 'WARNING';
    if (alertFilter === 'stop') return a.stage === 'STOP';
    return true;
  });

  /* === Состояние загрузки === */
  if (loading) {
    return (
      <div className="animate-in">
        <div className="page-header">
          <div>
            <h1 className="page-title">Dashboard</h1>
            <div className="page-subtitle">Загрузка данных...</div>
          </div>
        </div>
        <div className="stats-grid">
          {[1, 2, 3, 4].map((i) => (
            <div key={i} className="stat-card">
              <div className="skeleton" style={{ width: '60%', height: 14, marginBottom: 12 }} />
              <div className="skeleton" style={{ width: '40%', height: 32 }} />
            </div>
          ))}
        </div>
        <div className="loading-state">
          <div className="spinner" />
          <div>Загрузка дашборда...</div>
        </div>
      </div>
    );
  }

  /* === Состояние ошибки === */
  if (error && !stats) {
    return (
      <div className="animate-in">
        <div className="page-header">
          <div>
            <h1 className="page-title">Dashboard</h1>
            <div className="page-subtitle">Ошибка загрузки</div>
          </div>
        </div>
        <div className="error-state">
          <div style={{ fontSize: 48, opacity: 0.5 }}>⚠️</div>
          <div className="error-state-text">{error}</div>
          <button className="btn btn-primary" onClick={fetchData}>
            Попробовать снова
          </button>
        </div>
      </div>
    );
  }

  const s = stats || {};

  return (
    <div className="animate-in">
      <div className="page-header">
        <div>
          <h1 className="page-title">Dashboard</h1>
          <div className="page-subtitle">
            Мониторинг объявлений
            {s.last_scan_at && <> • Последний скан: {formatTime(s.last_scan_at)}</>}
          </div>
        </div>
        <div style={{ display: 'flex', gap: 8, alignItems: 'center' }}>
          <span className="badge badge-success pulse" role="status">
            ● Активен
          </span>
          <button className="btn btn-outline btn-sm" onClick={fetchData} aria-label="Обновить данные">
            🔄 Обновить
          </button>
        </div>
      </div>

      {/* Карточки статистики */}
      <section aria-label="Статистика" className="stats-grid">
        <div className="stat-card info" role="group" aria-label="Всего объявлений">
          <div className="stat-label">Всего объявлений</div>
          <div className="stat-value info">{s.total_ads_monitored ?? 0}</div>
        </div>
        <div className="stat-card warning" role="group" aria-label="Предупреждения">
          <div className="stat-label">Предупреждения</div>
          <div className="stat-value warning">{s.ads_in_warning ?? 0}</div>
        </div>
        <div className="stat-card danger" role="group" aria-label="Стоп-алерты">
          <div className="stat-label">Стоп-алерты</div>
          <div className="stat-value danger">{s.ads_in_stop ?? 0}</div>
        </div>
        <div className="stat-card success" role="group" aria-label="Выключено">
          <div className="stat-label">Выключено</div>
          <div className="stat-value success">{s.ads_disabled ?? 0}</div>
        </div>
      </section>

      {/* График расходов */}
      <section aria-label="Расход за 24 часа" className="chart-container">
        <div className="chart-title">
          Расход за 24 часа
          {s.total_spend != null && <> — ${Number(s.total_spend).toLocaleString('ru')}</>}
        </div>
        {spendBars.length > 0 ? (
          <div className="chart-canvas" role="img" aria-label="График расходов по часам">
            {spendBars.map((bar, i) => (
              <div
                key={i}
                className="chart-bar"
                style={{ height: `${bar.pct}%` }}
                title={`${bar.hour ?? i}:00 — $${bar.spend.toFixed(2)}`}
                aria-hidden="true"
              />
            ))}
          </div>
        ) : (
          <div className="empty-state" style={{ padding: '32px 20px' }}>
            <div style={{ color: 'var(--text-muted)' }}>Нет данных о расходах</div>
          </div>
        )}
      </section>

      {/* Последние алерты */}
      <section aria-label="Последние алерты" className="table-container">
        <div className="table-header">
          <div className="table-title">Последние алерты</div>
          <div className="table-filters" role="group" aria-label="Фильтры алертов">
            {[
              { key: 'all', label: 'Все' },
              { key: 'warning', label: '⚠️ Warning' },
              { key: 'stop', label: '🛑 Stop' },
            ].map((f) => (
              <button
                key={f.key}
                className={`filter-pill ${alertFilter === f.key ? 'active' : ''}`}
                onClick={() => setAlertFilter(f.key)}
                aria-pressed={alertFilter === f.key}
              >
                {f.label}
              </button>
            ))}
          </div>
        </div>
        <div className="table-scroll">
          <table>
            <thead>
              <tr>
                <th scope="col">Объявление</th>
                <th scope="col">Стадия</th>
                <th scope="col">Статус</th>
                <th scope="col">Правило</th>
                <th scope="col">Расход</th>
                <th scope="col">Время</th>
              </tr>
            </thead>
            <tbody>
              {filteredAlerts.map((a, i) => (
                <tr key={a.id || i}>
                  <td style={{ color: 'var(--text-primary)', fontWeight: 500 }}>
                    {a.ad_name || a.fb_ad_id || '—'}
                  </td>
                  <td>{stageBadge(a.stage)}</td>
                  <td>{stateBadge(a.state || a.alert_state)}</td>
                  <td>
                    <code style={{ color: 'var(--accent-purple)', fontSize: 12 }}>
                      {a.rule_name || a.rule || '—'}
                    </code>
                  </td>
                  <td>{a.spend != null ? `$${a.spend}` : '—'}</td>
                  <td style={{ color: 'var(--text-muted)' }}>{formatTime(a.created_at || a.time)}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
        {filteredAlerts.length === 0 && (
          <div className="empty-state">
            <div className="empty-state-icon">📭</div>
            <div className="empty-state-title">Нет алертов</div>
            <div>Пока не сработало ни одно стоп-правило</div>
          </div>
        )}
      </section>
    </div>
  );
}
