import { useState, useEffect, useCallback } from 'react';
import { getAdSnapshots } from '../api.js';

const FILTERS = [
  { key: 'all', label: 'Все' },
  { key: 'NORMAL', label: 'Активные' },
  { key: 'WARNING_SENT', label: '⚠️ Warning' },
  { key: 'STOP_SENT', label: '🛑 Stop' },
  { key: 'DISABLED', label: '✅ Выключено' },
];

/* Бейдж статуса алерта */
function alertStateBadge(state) {
  const map = {
    STOP_SENT: { cls: 'badge-danger', text: '🛑 Стоп' },
    WARNING_SENT: { cls: 'badge-warning', text: '⚠️ Предупр.' },
    CLAIMED: { cls: 'badge-info', text: '🔄 В работе' },
    DISABLED: { cls: 'badge-success', text: '✅ Выкл.' },
    NORMAL: { cls: 'badge-muted', text: 'Норма' },
  };
  const b = map[state] || map.NORMAL;
  return <span className={`badge ${b.cls}`}>{b.text}</span>;
}

export default function AdsPage() {
  const [ads, setAds] = useState([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState(null);
  const [activeFilter, setActiveFilter] = useState('all');

  /* Загрузка объявлений из API */
  const fetchAds = useCallback(async () => {
    try {
      setError(null);
      const params = {};
      if (activeFilter !== 'all') {
        params.alert_state = activeFilter;
      }
      const data = await getAdSnapshots(params);
      setAds(Array.isArray(data) ? data : []);
    } catch (err) {
      setError(err.message || 'Не удалось загрузить объявления');
    } finally {
      setLoading(false);
    }
  }, [activeFilter]);

  useEffect(() => {
    setLoading(true);
    fetchAds();
  }, [fetchAds]);

  /* Автообновление каждые 30 секунд */
  useEffect(() => {
    const timer = setInterval(fetchAds, 30000);
    return () => clearInterval(timer);
  }, [fetchAds]);

  return (
    <div className="animate-in">
      <div className="page-header">
        <div>
          <h1 className="page-title">Объявления</h1>
          <div className="page-subtitle">
            Все объявления под наблюдением • {ads.length} шт.
          </div>
        </div>
        <button className="btn btn-outline btn-sm" onClick={fetchAds} aria-label="Обновить список">
          🔄 Обновить
        </button>
      </div>

      <section aria-label="Таблица объявлений" className="table-container">
        <div className="table-header">
          <div className="table-title">Объявления ({ads.length})</div>
          <div className="table-filters" role="group" aria-label="Фильтры по статусу">
            {FILTERS.map((f) => (
              <button
                key={f.key}
                className={`filter-pill ${activeFilter === f.key ? 'active' : ''}`}
                onClick={() => setActiveFilter(f.key)}
                aria-pressed={activeFilter === f.key}
              >
                {f.label}
              </button>
            ))}
          </div>
        </div>

        {/* Состояние загрузки */}
        {loading && (
          <div className="loading-state">
            <div className="spinner" />
            <div>Загрузка объявлений...</div>
          </div>
        )}

        {/* Ошибка */}
        {error && !loading && (
          <div className="error-state">
            <div className="error-state-text">{error}</div>
            <button className="btn btn-outline btn-sm" onClick={fetchAds}>
              Повторить
            </button>
          </div>
        )}

        {/* Таблица с данными */}
        {!loading && !error && (
          <div className="table-scroll">
            <table>
              <thead>
                <tr>
                  <th scope="col">Объявление</th>
                  <th scope="col">Кампания</th>
                  <th scope="col">Оффер</th>
                  <th scope="col">Статус</th>
                  <th scope="col">Расход</th>
                  <th scope="col">CPC</th>
                  <th scope="col">Клики</th>
                  <th scope="col">Лиды</th>
                  <th scope="col">Реги</th>
                  <th scope="col">Депы</th>
                  <th scope="col">Правила</th>
                </tr>
              </thead>
              <tbody>
                {ads.map((ad, i) => (
                  <tr key={ad.id || ad.fb_ad_id || i}>
                    <td>
                      <div style={{ fontWeight: 500, color: 'var(--text-primary)' }}>
                        {ad.ad_name || '—'}
                      </div>
                      <div style={{ fontSize: 11, color: 'var(--text-muted)' }}>
                        ID: {ad.fb_ad_id || '—'}
                      </div>
                    </td>
                    <td>{ad.campaign_name || ad.campaign || '—'}</td>
                    <td>
                      <code style={{ color: 'var(--accent-purple)', fontSize: 12 }}>
                        {ad.offer_code || ad.offer || '—'}
                      </code>
                    </td>
                    <td>{alertStateBadge(ad.alert_state)}</td>
                    <td style={{ fontWeight: 600 }}>
                      {ad.spend != null ? `$${Number(ad.spend).toFixed(2)}` : '—'}
                    </td>
                    <td>{ad.cpc != null ? `$${Number(ad.cpc).toFixed(2)}` : '—'}</td>
                    <td>{ad.clicks ?? 0}</td>
                    <td>{ad.leads ?? 0}</td>
                    <td>{ad.registrations ?? ad.regs ?? 0}</td>
                    <td>{ad.deposits ?? ad.deps ?? 0}</td>
                    <td>
                      {ad.triggered_rules && ad.triggered_rules.length > 0 ? (
                        ad.triggered_rules.map((r, ri) => (
                          <span key={ri} className="badge badge-danger" style={{ marginRight: 4, fontSize: 10 }}>
                            {r}
                          </span>
                        ))
                      ) : ad.rules && ad.rules.length > 0 ? (
                        ad.rules.map((r, ri) => (
                          <span key={ri} className="badge badge-danger" style={{ marginRight: 4, fontSize: 10 }}>
                            {r}
                          </span>
                        ))
                      ) : (
                        <span style={{ color: 'var(--text-muted)' }}>—</span>
                      )}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}

        {/* Пустое состояние */}
        {!loading && !error && ads.length === 0 && (
          <div className="empty-state">
            <div className="empty-state-icon">📭</div>
            <div className="empty-state-title">Нет объявлений</div>
            <div>
              {activeFilter !== 'all'
                ? 'По выбранному фильтру объявлений не найдено'
                : 'Данные появятся после первого сканирования Ads Manager'}
            </div>
          </div>
        )}
      </section>
    </div>
  );
}
