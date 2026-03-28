import { useState, useEffect, useCallback, useMemo } from 'react';
import {
  getObserverSettings,
  updateObserverSettings,
  getTelegramSettings,
  setTelegramToken,
  revokeTelegram,
  getVisionSettings,
  updateVisionSettings,
  visionReconnect,
  getVisionProfiles,
  getTelegramRecipients,
  deleteTelegramRecipient,
  createInviteCode,
  getOffers,
  getOfferRules,
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

/* Хук для определения мобильного экрана */
function useIsMobile(breakpoint = 768) {
  const [isMobile, setIsMobile] = useState(
    () => typeof window !== 'undefined' && window.innerWidth <= breakpoint,
  );
  useEffect(() => {
    const mq = window.matchMedia(`(max-width: ${breakpoint}px)`);
    const handler = (e) => setIsMobile(e.matches);
    mq.addEventListener('change', handler);
    return () => mq.removeEventListener('change', handler);
  }, [breakpoint]);
  return isMobile;
}

/* Форматирование денег */
function fmt(val) {
  if (val == null || isNaN(val)) return '—';
  return `$${Number(val).toFixed(2)}`;
}

/* Округление до цента как в business-логике observer */
function roundMoney(val) {
  const num = Number(val);
  if (!Number.isFinite(num)) return 0;
  return Math.round((num + Number.EPSILON) * 100) / 100;
}

const STOP_RANGE_MARKS = [40, 60, 80, 100];
const WARNING_RANGE_MARKS = [50, 65, 80, 100];

function clampStepValue(value, min, max, step) {
  const number = Number(value);
  if (!Number.isFinite(number)) return min;
  const clamped = Math.min(max, Math.max(min, number));
  return Math.round(clamped / step) * step;
}

function PercentSlider({
  id,
  label,
  value,
  min,
  max,
  step,
  marks,
  hint,
  summary,
  onChange,
}) {
  const safeValue = clampStepValue(value, min, max, step);

  return (
    <div className="slider-field">
      <div className="slider-field__header">
        <label className="form-label slider-field__label" htmlFor={id}>
          {label}
        </label>
        <div className="slider-field__value">{safeValue}%</div>
      </div>
      <input
        id={id}
        className="slider-field__range"
        type="range"
        min={min}
        max={max}
        step={step}
        value={safeValue}
        onChange={(e) => onChange(clampStepValue(e.target.value, min, max, step))}
      />
      <div className="slider-field__scale" aria-hidden="true">
        {marks.map((mark) => (
          <span
            key={mark}
            className={`slider-field__mark ${mark === safeValue ? 'active' : ''}`}
          >
            {mark}%
          </span>
        ))}
      </div>
      <div className="slider-field__summary">{summary}</div>
      <div className="form-hint slider-field__hint">{hint}</div>
    </div>
  );
}

/* Таблица разбивки порогов предупреждения по офферам */
function WarningBreakdown({ warningPct, stopPct }) {
  const [rows, setRows] = useState([]);
  const [loading, setLoading] = useState(true);
  const [open, setOpen] = useState(false);

  useEffect(() => {
    if (!open) return;
    setLoading(true);
    getOffers()
      .then(async (offers) => {
        const active = (Array.isArray(offers) ? offers : []).filter((o) => o.is_active);
        const results = await Promise.all(
          active.map(async (offer) => {
            try {
              const rules = await getOfferRules(offer.id);
              return { offer, rules };
            } catch {
              return { offer, rules: null };
            }
          }),
        );
        setRows(results);
      })
      .catch(() => setRows([]))
      .finally(() => setLoading(false));
  }, [open]);

  const pct = Number(warningPct) || 80;
  const stopFactor = Math.min(100, Number(stopPct) || 100);

  const computed = useMemo(() =>
    rows.map(({ offer, rules }) => {
      const cpa = Number(offer.cpa_amount) || 0;
      const cpcPct = Number(rules?.cpc_percent_stop) || 2;
      const cplPct = Number(rules?.cpl_percent_stop) || 10;
      const cprPct = Number(rules?.cpr_percent_stop) || 20;
      const cpcBase = roundMoney(cpa * cpcPct / 100);
      const cplBase = roundMoney(cpa * cplPct / 100);
      const cprBase = roundMoney(cpa * cprPct / 100);
      const cpcStop = roundMoney(cpcBase * stopFactor / 100);
      const cplStop = roundMoney(cplBase * stopFactor / 100);
      const cprStop = roundMoney(cprBase * stopFactor / 100);
      return {
        code: offer.code,
        name: offer.name,
        cpa,
        cpcStop, cpcBase, cpcWarn: roundMoney(cpcStop * pct / 100), cpcPct,
        cplStop, cplBase, cplWarn: roundMoney(cplStop * pct / 100), cplPct,
        cprStop, cprBase, cprWarn: roundMoney(cprStop * pct / 100), cprPct,
        enabled: { cpc: rules?.cpc_percent_enabled !== false, cpl: rules?.cpl_percent_enabled !== false, cpr: rules?.cpr_percent_enabled !== false },
      };
    }), [rows, pct, stopFactor]);

  return (
    <div style={{ marginTop: 16 }}>
      <button
        type="button"
        onClick={() => setOpen((v) => !v)}
        style={{
          background: 'none', border: 'none', cursor: 'pointer', padding: 0,
          color: 'var(--accent-blue)', fontSize: 12, fontWeight: 600,
          display: 'flex', alignItems: 'center', gap: 6,
        }}
      >
        <span style={{ fontSize: 10 }}>{open ? '▼' : '▶'}</span>
        Разбивка порогов по офферам
      </button>
      {open && (
        <div style={{ marginTop: 6, color: 'var(--text-muted)', fontSize: 11 }}>
          Порог считается с точностью до цента, как и в observer.
        </div>
      )}

      {open && (
        <div style={{
          marginTop: 12, borderRadius: 'var(--radius-sm)',
          border: '1px solid var(--border-color)', overflow: 'hidden',
        }}>
          {loading ? (
            <div style={{ padding: '16px 20px', color: 'var(--text-muted)', fontSize: 13 }}>
              Загрузка офферов...
            </div>
          ) : computed.length === 0 ? (
            <div style={{ padding: '16px 20px', color: 'var(--text-muted)', fontSize: 13 }}>
              Нет активных офферов
            </div>
          ) : (
            <div className="table-scroll">
              <table style={{ fontSize: 12 }}>
                <thead>
                  <tr>
                    <th>Оффер</th>
                    <th>CPA</th>
                    <th>CPC стоп ({stopFactor}%)</th>
                    <th style={{ color: 'var(--accent-orange)' }}>CPC warn ({pct}%)</th>
                    <th>CPL стоп ({stopFactor}%)</th>
                    <th style={{ color: 'var(--accent-orange)' }}>CPL warn ({pct}%)</th>
                    <th>CPR стоп ({stopFactor}%)</th>
                    <th style={{ color: 'var(--accent-orange)' }}>CPR warn ({pct}%)</th>
                  </tr>
                </thead>
                <tbody>
                  {computed.map((r) => (
                    <tr key={r.code}>
                      <td>
                        <div style={{ fontWeight: 600, color: 'var(--text-primary)' }}>{r.code}</div>
                        <div style={{ color: 'var(--text-muted)', fontSize: 11 }}>{r.name}</div>
                      </td>
                      <td style={{ fontWeight: 600 }}>{fmt(r.cpa)}</td>
                      <td>
                        {r.enabled.cpc ? (
                          <><span style={{ color: 'var(--accent-red)' }}>{fmt(r.cpcStop)}</span>
                          <span style={{ color: 'var(--text-muted)', marginLeft: 4 }}>({r.cpcPct}% CPA)</span>
                          {stopFactor < 100 && (
                            <div style={{ color: 'var(--text-muted)', fontSize: 11 }}>
                              базовый {fmt(r.cpcBase)}
                            </div>
                          )}</>
                        ) : <span style={{ color: 'var(--text-muted)' }}>выкл</span>}
                      </td>
                      <td>
                        {r.enabled.cpc ? (
                          <span style={{ color: 'var(--accent-orange)' }}>{fmt(r.cpcWarn)}</span>
                        ) : '—'}
                      </td>
                      <td>
                        {r.enabled.cpl ? (
                          <><span style={{ color: 'var(--accent-red)' }}>{fmt(r.cplStop)}</span>
                          <span style={{ color: 'var(--text-muted)', marginLeft: 4 }}>({r.cplPct}% CPA)</span>
                          {stopFactor < 100 && (
                            <div style={{ color: 'var(--text-muted)', fontSize: 11 }}>
                              базовый {fmt(r.cplBase)}
                            </div>
                          )}</>
                        ) : <span style={{ color: 'var(--text-muted)' }}>выкл</span>}
                      </td>
                      <td>
                        {r.enabled.cpl ? (
                          <span style={{ color: 'var(--accent-orange)' }}>{fmt(r.cplWarn)}</span>
                        ) : '—'}
                      </td>
                      <td>
                        {r.enabled.cpr ? (
                          <><span style={{ color: 'var(--accent-red)' }}>{fmt(r.cprStop)}</span>
                          <span style={{ color: 'var(--text-muted)', marginLeft: 4 }}>({r.cprPct}% CPA)</span>
                          {stopFactor < 100 && (
                            <div style={{ color: 'var(--text-muted)', fontSize: 11 }}>
                              базовый {fmt(r.cprBase)}
                            </div>
                          )}</>
                        ) : <span style={{ color: 'var(--text-muted)' }}>выкл</span>}
                      </td>
                      <td>
                        {r.enabled.cpr ? (
                          <span style={{ color: 'var(--accent-orange)' }}>{fmt(r.cprWarn)}</span>
                        ) : '—'}
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          )}
        </div>
      )}
    </div>
  );
}

export default function SettingsPage() {
  const isMobile = useIsMobile();
  const [browserOpen, setBrowserOpen] = useState(!isMobile);

  // Observer
  const [observer, setObserver] = useState({
    interval_seconds: 90,
    jitter_seconds: 10,
    warning_percent_of_stop: 80,
    stop_percent_of_base: 100,
  });

  // Telegram
  const [telegram, setTelegram] = useState({
    bot_token: '',
    chat_id: '',
    is_authorized: false,
    bot_username: '',
    auth_code: '',
  });
  const [newToken, setNewToken] = useState('');
  const [authResult, setAuthResult] = useState(null);
  const [recipients, setRecipients] = useState([]);
  const [inviteCode, setInviteCode] = useState(null);

  // Vision
  const [vision, setVision] = useState({
    api_url: 'http://127.0.0.1:3030',
    x_token: '',
    profile_id: '',
    has_token: false,
  });
  const [visionProfiles, setVisionProfiles] = useState([]);
  const [showVisionToken, setShowVisionToken] = useState(false);
  const [profilesLoading, setProfilesLoading] = useState(false);

  const [loading, setLoading] = useState(true);
  const [saving, setSaving] = useState('');
  const [toast, setToast] = useState(null);
  const effectiveStopPercent = clampStepValue(observer.stop_percent_of_base, 5, 100, 5);
  const warningPercent = clampStepValue(observer.warning_percent_of_stop, 50, 100, 5);
  const stopShiftPercent = 100 - effectiveStopPercent;

  /* Загрузка настроек с API */
  const fetchSettings = useCallback(async () => {
    try {
      const [obsData, tgData, visionData] = await Promise.all([
        getObserverSettings().catch(() => null),
        getTelegramSettings().catch(() => null),
        getVisionSettings().catch(() => null),
      ]);
      if (obsData && typeof obsData === 'object') {
        setObserver({
          interval_seconds: obsData.interval_seconds ?? 90,
          jitter_seconds: obsData.jitter_seconds ?? 10,
          warning_percent_of_stop: obsData.warning_percent_of_stop ?? 80,
          stop_percent_of_base: obsData.stop_percent_of_base ?? 100,
        });
      }
      if (tgData && typeof tgData === 'object') {
        setTelegram({
          bot_token: tgData.bot_token || '',
          chat_id: tgData.chat_id || '',
          is_authorized: tgData.is_authorized || false,
          bot_username: tgData.bot_username || '',
          auth_code: tgData.auth_code || '',
        });
      }
      if (visionData && typeof visionData === 'object') {
        setVision({
          api_url: visionData.api_url || 'http://127.0.0.1:3030',
          x_token: '',
          profile_id: visionData.profile_id || '',
          has_token: visionData.has_token || false,
        });
      }
    } catch {
      /* Настройки не найдены — используем дефолты */
    } finally {
      setLoading(false);
    }
  }, []);

  /* Загрузка получателей */
  const fetchRecipients = useCallback(async () => {
    try {
      const data = await getTelegramRecipients();
      setRecipients(Array.isArray(data) ? data : []);
    } catch {
      /* Игнорируем — не критично */
    }
  }, []);

  useEffect(() => {
    fetchSettings();
  }, [fetchSettings]);

  useEffect(() => {
    if (telegram.is_authorized) {
      fetchRecipients();
    }
  }, [telegram.is_authorized, fetchRecipients]);

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

  /* Подключение Telegram-бота */
  const handleConnectTelegram = async () => {
    if (!newToken.trim()) return;
    setSaving('telegram');
    try {
      const result = await setTelegramToken(newToken.trim());
      setAuthResult(result);
      setNewToken('');
      setToast({ message: 'Токен проверен. Отправьте код боту.', type: 'success' });
      const tgData = await getTelegramSettings();
      setTelegram({ ...telegram, ...tgData });
    } catch (err) {
      setToast({ message: err.message || 'Невалидный токен', type: 'error' });
    } finally {
      setSaving('');
    }
  };

  /* Отключение Telegram */
  const handleRevokeTelegram = async () => {
    if (!confirm('Отключить Telegram? Уведомления перестанут приходить.')) return;
    setSaving('telegram');
    try {
      await revokeTelegram();
      setTelegram({ bot_token: '', chat_id: '', is_authorized: false, bot_username: '', auth_code: '' });
      setAuthResult(null);
      setRecipients([]);
      setToast({ message: 'Telegram отключён', type: 'success' });
    } catch (err) {
      setToast({ message: err.message || 'Ошибка', type: 'error' });
    } finally {
      setSaving('');
    }
  };

  /* Проверка статуса авторизации */
  const checkAuthStatus = useCallback(async () => {
    try {
      const tgData = await getTelegramSettings();
      setTelegram((prev) => ({ ...prev, ...tgData }));
      if (tgData.is_authorized) {
        setAuthResult(null);
        setToast({ message: 'Telegram подключён!', type: 'success' });
        fetchRecipients();
      }
    } catch { /* игнорируем */ }
  }, [fetchRecipients]);

  /* Удаление получателя */
  const handleDeleteRecipient = async (id) => {
    if (!confirm('Удалить получателя?')) return;
    try {
      await deleteTelegramRecipient(id);
      setRecipients((prev) => prev.filter((r) => r.id !== id));
      setToast({ message: 'Получатель удалён', type: 'success' });
    } catch (err) {
      setToast({ message: err.message || 'Ошибка', type: 'error' });
    }
  };

  /* Создание инвайт-кода */
  const handleCreateInvite = async () => {
    setSaving('invite');
    try {
      const result = await createInviteCode();
      setInviteCode(result);
    } catch (err) {
      setToast({ message: err.message || 'Ошибка генерации кода', type: 'error' });
    } finally {
      setSaving('');
    }
  };

  /* Сохранение Vision настроек */
  const handleSaveVision = async () => {
    setSaving('vision');
    try {
      await updateVisionSettings({
        api_url: vision.api_url,
        x_token: vision.x_token,
        profile_id: vision.profile_id,
      });
      setToast({ message: 'Vision настройки сохранены', type: 'success' });
      const visionData = await getVisionSettings();
      setVision({ ...visionData, x_token: '' });
    } catch (err) {
      setToast({ message: err.message || 'Ошибка сохранения', type: 'error' });
    } finally {
      setSaving('');
    }
  };

  /* Переподключение Vision браузера */
  const handleVisionReconnect = async () => {
    setSaving('reconnect');
    try {
      await visionReconnect();
      setToast({
        message: 'Флаг установлен — Observer переподключится в следующем цикле сканирования',
        type: 'success',
      });
    } catch (err) {
      setToast({ message: err.message || 'Ошибка', type: 'error' });
    } finally {
      setSaving('');
    }
  };

  /* Загрузка профилей Vision */
  const handleLoadProfiles = async () => {
    setProfilesLoading(true);
    try {
      const data = await getVisionProfiles();
      setVisionProfiles(Array.isArray(data) ? data : []);
      if (!data?.length) setToast({ message: 'Профили не найдены', type: 'info' });
    } catch (err) {
      setToast({ message: err.message || 'Не удалось загрузить профили', type: 'error' });
    } finally {
      setProfilesLoading(false);
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
          <div className="page-subtitle">Сканирование, пороги отключения, Telegram и браузер</div>
        </div>
      </div>

      {/* Observer — сканирование и пороги */}
      <section aria-label="Настройки Observer" className="form-section">
        <div className="form-section-title">Observer — сканирование и пороги</div>
        <div className="form-grid form-grid--observer-basics">
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
        </div>
        <div className="observer-thresholds">
          <div className="observer-thresholds__header">
            <div>
              <div className="observer-thresholds__title">Пороги отключения</div>
              <div className="observer-thresholds__subtitle">
                Сначала задаём фактический стоп, затем точку раннего предупреждения.
              </div>
            </div>
            <div className="observer-thresholds__badge">
              {stopShiftPercent > 0 ? `Раньше базового на ${stopShiftPercent}%` : 'Без смещения'}
            </div>
          </div>
          <div className="observer-thresholds__grid">
            <PercentSlider
              id="obs-stop-adjust"
              label="Фактический стоп (% от базового стопа)"
              value={effectiveStopPercent}
              min={5}
              max={100}
              step={5}
              marks={STOP_RANGE_MARKS}
              summary={
                effectiveStopPercent === 100
                  ? 'Авто-стоп срабатывает на базовом пороге.'
                  : `Авто-стоп срабатывает раньше: на ${effectiveStopPercent}% от базового порога.`
              }
              hint="Влияет только на CPA-правила отключения и может двигаться только вниз."
              onChange={(value) =>
                setObserver((prev) => ({ ...prev, stop_percent_of_base: value }))
              }
            />
            <PercentSlider
              id="obs-warning"
              label="Порог предупреждения (% от стопа)"
              value={warningPercent}
              min={50}
              max={100}
              step={5}
              marks={WARNING_RANGE_MARKS}
              summary={`Предупреждение придёт на ${warningPercent}% от фактического стопа.`}
              hint="Помогает заранее увидеть риск до реального авто-стопа."
              onChange={(value) =>
                setObserver((prev) => ({ ...prev, warning_percent_of_stop: value }))
              }
            />
          </div>
        </div>
        <WarningBreakdown
          warningPct={warningPercent}
          stopPct={effectiveStopPercent}
        />
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

        {telegram.is_authorized ? (
          <div>
            {/* Статус подключения */}
            <div style={{
              display: 'flex', alignItems: 'center', gap: 12, marginBottom: 16,
              padding: '14px 18px', borderRadius: 'var(--radius-sm)',
              background: 'rgba(74, 222, 148, 0.1)', border: '1px solid rgba(74, 222, 148, 0.3)',
            }}>
              <span style={{ fontSize: 20 }}>✅</span>
              <div>
                <div style={{ fontWeight: 600, color: 'var(--accent-green)' }}>Подключён</div>
                <div style={{ fontSize: 13, color: 'var(--text-secondary)', marginTop: 2 }}>
                  Бот: @{telegram.bot_username} &bull; Chat ID: <code>{telegram.chat_id}</code>
                </div>
              </div>
            </div>

            {/* Список получателей */}
            <div style={{ marginBottom: 20 }}>
              <div style={{ fontWeight: 600, fontSize: 14, marginBottom: 10, color: 'var(--text-primary)' }}>
                Получатели уведомлений ({recipients.length})
              </div>
              {recipients.length > 0 ? (
                <div style={{ display: 'flex', flexDirection: 'column', gap: 8, marginBottom: 12 }}>
                  {recipients.map((r) => (
                    <div key={r.id} style={{
                      display: 'flex', alignItems: 'center', justifyContent: 'space-between',
                      padding: '10px 14px', borderRadius: 'var(--radius-sm)',
                      background: 'var(--bg-secondary)', border: '1px solid var(--border-color)',
                    }}>
                      <div>
                        <div style={{ fontWeight: 500, fontSize: 14 }}>
                          {r.first_name || r.username || 'Пользователь'}
                          {r.username && (
                            <span style={{ color: 'var(--text-muted)', fontSize: 12, marginLeft: 6 }}>
                              @{r.username}
                            </span>
                          )}
                        </div>
                        <div style={{ fontSize: 11, color: 'var(--text-muted)', marginTop: 2 }}>
                          Chat ID: {r.chat_id} · {new Date(r.created_at).toLocaleDateString('ru')}
                        </div>
                      </div>
                      <button
                        className="btn btn-outline btn-sm"
                        onClick={() => handleDeleteRecipient(r.id)}
                        style={{ color: 'var(--accent-red)' }}
                        title="Удалить получателя"
                      >
                        🗑
                      </button>
                    </div>
                  ))}
                </div>
              ) : (
                <div style={{ color: 'var(--text-muted)', fontSize: 13, marginBottom: 12 }}>
                  Нет дополнительных получателей. Добавьте пользователей через инвайт-код.
                </div>
              )}

              {/* Добавить получателя */}
              {inviteCode ? (
                <div style={{
                  padding: '14px 18px', borderRadius: 'var(--radius-sm)',
                  background: 'rgba(90, 154, 255, 0.1)', border: '1px solid rgba(90, 154, 255, 0.3)',
                  marginBottom: 12,
                }}>
                  <div style={{ fontWeight: 600, marginBottom: 6, color: 'var(--accent-blue)' }}>
                    Инвайт-код создан
                  </div>
                  <div style={{ fontSize: 14, color: 'var(--text-secondary)', marginBottom: 8 }}>
                    Попросите пользователя отправить боту{' '}
                    <strong>@{inviteCode.bot_username || telegram.bot_username}</strong>:
                  </div>
                  <div style={{
                    padding: '10px 16px', borderRadius: 'var(--radius-sm)',
                    background: 'var(--bg-input)', fontFamily: 'monospace', fontSize: 18,
                    letterSpacing: 2, color: 'var(--text-primary)', marginBottom: 10,
                  }}>
                    /start {inviteCode.code}
                  </div>
                  <button
                    className="btn btn-outline btn-sm"
                    onClick={() => { setInviteCode(null); fetchRecipients(); }}
                  >
                    Готово
                  </button>
                </div>
              ) : (
                <button
                  className="btn btn-outline btn-sm"
                  onClick={handleCreateInvite}
                  disabled={saving === 'invite'}
                >
                  {saving === 'invite' ? 'Генерация...' : '+ Добавить пользователя'}
                </button>
              )}
            </div>

            <button
              className="btn btn-outline btn-sm"
              onClick={handleRevokeTelegram}
              disabled={saving === 'telegram'}
              style={{ color: 'var(--accent-red)' }}
            >
              Отключить Telegram
            </button>
          </div>
        ) : telegram.auth_code || authResult ? (
          /* Ожидание авторизации */
          <div>
            <div style={{
              padding: '18px', borderRadius: 'var(--radius-sm)',
              background: 'rgba(90, 154, 255, 0.1)', border: '1px solid rgba(90, 154, 255, 0.3)',
              marginBottom: 16,
            }}>
              <div style={{ fontWeight: 600, marginBottom: 8, color: 'var(--accent-blue)' }}>
                Ожидание подтверждения
              </div>
              <div style={{ fontSize: 14, color: 'var(--text-secondary)', lineHeight: 1.6 }}>
                Отправьте боту{' '}
                <strong>@{authResult?.bot_username || telegram.bot_username}</strong>{' '}
                команду:
              </div>
              <div style={{
                marginTop: 10, padding: '10px 16px', borderRadius: 'var(--radius-sm)',
                background: 'var(--bg-input)', fontFamily: 'monospace', fontSize: 16,
                letterSpacing: 1, color: 'var(--text-primary)',
              }}>
                /start {authResult?.auth_code || telegram.auth_code}
              </div>
            </div>
            <div style={{ display: 'flex', gap: 12 }}>
              <button className="btn btn-outline btn-sm" onClick={checkAuthStatus}>
                Проверить статус
              </button>
              <button
                className="btn btn-outline btn-sm"
                onClick={handleRevokeTelegram}
                style={{ color: 'var(--accent-red)' }}
              >
                Отмена
              </button>
            </div>
          </div>
        ) : (
          /* Не подключён */
          <div>
            <div style={{ fontSize: 14, color: 'var(--text-secondary)', marginBottom: 16, lineHeight: 1.6 }}>
              Для получения уведомлений подключите Telegram-бота.
              Создайте бота через <strong>@BotFather</strong> и вставьте полученный токен.
            </div>
            <div className="form-group" style={{ marginBottom: 16 }}>
              <label className="form-label" htmlFor="tg-token">Bot Token</label>
              <input
                id="tg-token"
                className="form-input"
                type="password"
                placeholder="123456:ABC-DEF1234ghIkl-..."
                value={newToken}
                onChange={(e) => setNewToken(e.target.value)}
                autoComplete="off"
              />
              <div className="form-hint">
                Токен из @BotFather. Хранится в зашифрованном виде.
              </div>
            </div>
            <button
              className="btn btn-primary"
              onClick={handleConnectTelegram}
              disabled={saving === 'telegram' || !newToken.trim()}
            >
              {saving === 'telegram' ? 'Проверка...' : 'Подключить бота'}
            </button>
          </div>
        )}
      </section>

      {/* Vision — anti-detect браузер */}
      <section aria-label="Настройки браузера" className="form-section">
        <div
          className="form-section-title"
          style={{ cursor: 'pointer', userSelect: 'none', justifyContent: 'space-between' }}
          onClick={() => setBrowserOpen((v) => !v)}
          role="button"
          tabIndex={0}
          aria-expanded={browserOpen}
          onKeyDown={(e) => {
            if (e.key === 'Enter' || e.key === ' ') {
              e.preventDefault();
              setBrowserOpen((v) => !v);
            }
          }}
        >
          <span>Anti-detect браузер (Vision)</span>
          <span style={{ fontSize: 14, color: 'var(--text-muted)' }}>
            {browserOpen ? 'Скрыть' : 'Показать'}
            {vision.has_token && (
              <span style={{ marginLeft: 8, color: 'var(--accent-green)', fontSize: 12 }}>
                ✓ настроен
              </span>
            )}
          </span>
        </div>
        {browserOpen && (
          <div>
            <div className="form-grid" style={{ marginBottom: 16 }}>
              <div className="form-group">
                <label className="form-label" htmlFor="vision-url">Vision API URL</label>
                <input
                  id="vision-url"
                  className="form-input"
                  type="text"
                  placeholder="http://127.0.0.1:3030"
                  value={vision.api_url}
                  onChange={(e) => setVision({ ...vision, api_url: e.target.value })}
                />
              </div>
              <div className="form-group">
                <label className="form-label" htmlFor="vision-token">
                  X-Token{' '}
                  {vision.has_token && (
                    <span style={{ color: 'var(--accent-green)', fontSize: 11 }}>(сохранён)</span>
                  )}
                </label>
                <div style={{ position: 'relative' }}>
                  <input
                    id="vision-token"
                    className="form-input"
                    type={showVisionToken ? 'text' : 'password'}
                    placeholder={
                      vision.has_token
                        ? '••••••••••• (оставьте пустым чтобы не менять)'
                        : 'Введите X-Token'
                    }
                    value={vision.x_token}
                    onChange={(e) => setVision({ ...vision, x_token: e.target.value })}
                    autoComplete="off"
                    style={{ paddingRight: 40 }}
                  />
                  <button
                    type="button"
                    onClick={() => setShowVisionToken((v) => !v)}
                    style={{
                      position: 'absolute', right: 8, top: '50%', transform: 'translateY(-50%)',
                      background: 'none', border: 'none', cursor: 'pointer',
                      color: 'var(--text-muted)', fontSize: 14,
                    }}
                  >
                    {showVisionToken ? '🙈' : '👁'}
                  </button>
                </div>
                <div className="form-hint">Токен хранится в зашифрованном виде.</div>
              </div>
            </div>

            {/* Выбор профиля */}
            <div className="form-group" style={{ marginBottom: 16 }}>
              <label className="form-label" htmlFor="vision-profile">Профиль браузера</label>
              <div style={{ display: 'flex', gap: 8 }}>
                {visionProfiles.length > 0 ? (
                  <select
                    id="vision-profile"
                    className="form-input"
                    value={vision.profile_id}
                    onChange={(e) => setVision({ ...vision, profile_id: e.target.value })}
                  >
                    <option value="">— Выберите профиль —</option>
                    {visionProfiles.map((p) => (
                      <option key={p.profile_id} value={p.profile_id}>
                        {p.name || p.profile_id}
                        {p.port ? ` (порт ${p.port})` : ''}
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
                    onChange={(e) => setVision({ ...vision, profile_id: e.target.value })}
                  />
                )}
                <button
                  className="btn btn-outline btn-sm"
                  onClick={handleLoadProfiles}
                  disabled={profilesLoading}
                  style={{ whiteSpace: 'nowrap' }}
                >
                  {profilesLoading ? '...' : 'Загрузить список'}
                </button>
              </div>
              <div className="form-hint">
                Нажмите «Загрузить список» чтобы получить доступные профили из Vision.
              </div>
            </div>

            <div style={{ display: 'flex', gap: 12, flexWrap: 'wrap' }}>
              <button
                className="btn btn-primary"
                onClick={handleSaveVision}
                disabled={saving === 'vision'}
              >
                {saving === 'vision' ? 'Сохранение...' : 'Сохранить'}
              </button>
              <button
                className="btn btn-outline"
                onClick={handleVisionReconnect}
                disabled={saving === 'reconnect'}
                title="Observer сделает disconnect + reconnect к браузеру в следующем цикле сканирования. Используйте если браузер завис или потерял соединение."
              >
                {saving === 'reconnect' ? 'Отправка...' : '🔄 Переподключить браузер'}
              </button>
              <div className="form-hint" style={{ alignSelf: 'center', marginTop: 0 }}>
                Observer выполнит reconnect в следующем цикле сканирования
              </div>
            </div>
          </div>
        )}
      </section>

      {/* Toast */}
      {toast && <Toast message={toast.message} type={toast.type} onClose={() => setToast(null)} />}
    </div>
  );
}
