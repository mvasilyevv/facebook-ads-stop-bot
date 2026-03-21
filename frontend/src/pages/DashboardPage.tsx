import { useEffect, useState, startTransition } from "react";
import { Badge } from "../components/Badge";
import { EmptyState } from "../components/EmptyState";
import { SetupStepper } from "../components/SetupStepper";
import { SectionCard } from "../components/SectionCard";
import { fetchHealth, fetchAds, fetchBotMode, updateBotMode, loadDashboard } from "../lib/api";
import { formatDateTime, formatMoney, formatMetricText, formatRelativeStatus } from "../lib/format";
import { getBadgeTone, formatDeliveryStatusLabel, TRACKED_DELIVERY_STATUSES } from "../lib/helpers";
import type { HealthResponse, AdSummary, BotModeResponse } from "../types";

type BotModePatch = Partial<Pick<BotModeResponse, "auto_pause_enabled" | "auto_resume_enabled" | "observe_only_enabled">>;

function scoreSeenAt(value: string | null | undefined): number {
  if (!value) return 0;
  const stamp = new Date(value).getTime();
  return Number.isNaN(stamp) ? 0 : stamp;
}

export default function DashboardPage() {
  const [health, setHealth] = useState<HealthResponse | null>(null);
  const [ads, setAds] = useState<AdSummary[]>([]);
  const [botMode, setBotMode] = useState<BotModeResponse | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [lastLoadedAt, setLastLoadedAt] = useState<string | null>(null);
  const [updatingBotMode, setUpdatingBotMode] = useState(false);
  const [dashboardData, setDashboardData] = useState<Awaited<ReturnType<typeof loadDashboard>> | null>(null);

  async function reload(silent = false) {
    if (!silent) setLoading(true);
    setError(null);
    try {
      const [h, a, b, data] = await Promise.all([
        fetchHealth(),
        fetchAds(),
        fetchBotMode(),
        loadDashboard(),
      ]);
      startTransition(() => {
        setHealth(h);
        setAds(a);
        setBotMode(b);
        setDashboardData(data);
        setLastLoadedAt(new Date().toISOString());
        setLoading(false);
      });
    } catch (e) {
      setError(e instanceof Error ? e.message : "Ошибка загрузки");
      setLoading(false);
    }
  }

  async function updateBotModeState(patch: BotModePatch) {
    if (!botMode) return;
    setUpdatingBotMode(true);
    try {
      const updated = await updateBotMode({
        auto_pause_enabled: patch.auto_pause_enabled ?? botMode.auto_pause_enabled,
        auto_resume_enabled: patch.auto_resume_enabled ?? botMode.auto_resume_enabled,
        observe_only_enabled: patch.observe_only_enabled ?? botMode.observe_only_enabled,
      });
      startTransition(() => {
        setBotMode(updated);
      });
    } catch (e) {
      setError(e instanceof Error ? e.message : "Ошибка обновления режима");
    } finally {
      setUpdatingBotMode(false);
    }
  }

  useEffect(() => { void reload(); }, []);

  const trackedAds = ads
    .filter((ad) => ad.tracking_mode === "TRACKED")
    .sort((a, b) => scoreSeenAt(b.last_seen_at) - scoreSeenAt(a.last_seen_at));

  const deliveryBreakdown = TRACKED_DELIVERY_STATUSES.map((status) => ({
    status,
    count: trackedAds.filter((ad) => ad.delivery_status === status).length,
  }));

  if (loading) {
    return <div className="page-loading">Загрузка данных...</div>;
  }

  const actionTypesEnabled =
    botMode != null && (botMode.auto_pause_enabled || botMode.auto_resume_enabled);
  const observeOnlyEnabled = botMode?.observe_only_enabled ?? true;
  const liveAutomationEnabled = actionTypesEnabled && !observeOnlyEnabled;

  return (
    <>
      <div className="page-header">
        <div>
          <h1 className="page-title">Обзор системы</h1>
          <p className="page-subtitle">Здоровье backend и отслеживаемые объявления</p>
        </div>
        <div className="page-header__actions">
          <span className="section-note">
            {lastLoadedAt ? `Обновлено: ${formatDateTime(lastLoadedAt)}` : ""}
          </span>
          <button type="button" className="button button--primary" onClick={() => void reload(true)}>
            Обновить
          </button>
        </div>
      </div>

      {error && <div className="inline-error">{error}</div>}

      {actionTypesEnabled && observeOnlyEnabled && (
        <div className="message-banner">
          Режим наблюдения: бот только пишет, что сделал бы, но не нажимает кнопки
        </div>
      )}

      {liveAutomationEnabled && (
        <div className="message-banner">
          Бот автоматически управляет объявлениями в боевом режиме
        </div>
      )}

      {botMode && (
        <div className="bot-mode-section">
          <div className="bot-mode-toggle">
            <div>
              <strong>Режим бота</strong>
              <div className="section-note">
                {observeOnlyEnabled ? "Сейчас: режим наблюдения" : "Сейчас: боевой режим"}
              </div>
            </div>
            {updatingBotMode && <span className="section-note">Обновляется...</span>}
          </div>
          <div className="bot-mode-grid">
            <label className="bot-mode-item">
              <input
                type="checkbox"
                checked={botMode.auto_pause_enabled}
                onChange={() => void updateBotModeState({ auto_pause_enabled: !botMode.auto_pause_enabled })}
                disabled={updatingBotMode}
                className="bot-mode-checkbox"
              />
              <span className="bot-mode-item__text">
                <span className="bot-mode-item__title">Автопауза</span>
                <span className="bot-mode-item__hint">
                  Бот может сам ставить объявление на паузу
                </span>
              </span>
            </label>
            <label className="bot-mode-item">
              <input
                type="checkbox"
                checked={botMode.auto_resume_enabled}
                onChange={() => void updateBotModeState({ auto_resume_enabled: !botMode.auto_resume_enabled })}
                disabled={updatingBotMode}
                className="bot-mode-checkbox"
              />
              <span className="bot-mode-item__text">
                <span className="bot-mode-item__title">Авторезюм</span>
                <span className="bot-mode-item__hint">
                  Бот может сам возвращать объявление из паузы
                </span>
              </span>
            </label>
            <label className="bot-mode-item bot-mode-item--accent">
              <input
                type="checkbox"
                checked={botMode.observe_only_enabled}
                onChange={() => void updateBotModeState({ observe_only_enabled: !botMode.observe_only_enabled })}
                disabled={updatingBotMode}
                className="bot-mode-checkbox"
              />
              <span className="bot-mode-item__text">
                <span className="bot-mode-item__title">Режим наблюдения</span>
                <span className="bot-mode-item__hint">
                  Бот продолжает мониторить и считать решения, но не выполняет действия физически
                </span>
              </span>
            </label>
          </div>
        </div>
      )}

      {dashboardData && (
        <SetupStepper
          offers={dashboardData.offers}
          bindings={dashboardData.bindings}
          rules={dashboardData.rules}
          sessions={dashboardData.sessions}
          botMode={botMode}
          loading={loading}
        />
      )}

      <SectionCard
        title="Здоровье системы"
        subtitle="Краткая сводка по backend"
        actions={
          <span className="section-note">
            {health?.timestamp ? `Снимок: ${formatDateTime(health.timestamp)}` : "Снимок отсутствует"}
          </span>
        }
      >
        <div className="metric-grid">
          <article className="metric-tile metric-tile--accent">
            <span>Сервис</span>
            <strong>{health?.service ?? "API"}</strong>
          </article>
          <article className="metric-tile">
            <span>Статус</span>
            <strong>{health ? formatRelativeStatus(health.status) : "нет ответа"}</strong>
          </article>
          <article className="metric-tile">
            <span>Окружение</span>
            <strong>{health?.environment ?? "неизвестно"}</strong>
          </article>
          <article className="metric-tile">
            <span>База данных</span>
            <strong>{health?.database_status ?? "нет данных"}</strong>
          </article>
        </div>
      </SectionCard>

      <SectionCard
        title="Отслеживаемые объявления"
        subtitle="Сводка по объявлениям в отслеживании"
        actions={<span className="section-note">Всего: {trackedAds.length}</span>}
      >
        <div className="metric-grid">
          <article className="metric-tile metric-tile--accent">
            <span>Всего отслеживаемых</span>
            <strong>{trackedAds.length}</strong>
          </article>
          {deliveryBreakdown.map((item) => (
            <article key={item.status} className="metric-tile">
              <span>{formatDeliveryStatusLabel(item.status)}</span>
              <strong>{item.count}</strong>
            </article>
          ))}
        </div>
        <div className="table-wrap">
          <table className="data-table">
            <thead>
              <tr>
                <th>Объявление</th>
                <th>Статус</th>
                <th>Расход</th>
                <th>Клики</th>
                <th>CPC</th>
                <th>Лиды</th>
                <th>CPL</th>
                <th>Рег.</th>
                <th>CPR</th>
                <th>Деп.</th>
                <th>Снимок</th>
              </tr>
            </thead>
            <tbody>
              {trackedAds.length === 0 ? (
                <tr>
                  <td colSpan={11}>
                    <EmptyState title="Нет отслеживаемых" description="Отслеживаемые объявления появятся после загрузки." />
                  </td>
                </tr>
              ) : (
                trackedAds.map((ad) => (
                  <tr key={ad.fb_ad_id}>
                    <td>
                      <strong>{ad.ad_name}</strong>
                      <div className="muted">{ad.campaign_name} · {ad.adset_name}</div>
                      <div className="mono">{ad.fb_ad_id}</div>
                    </td>
                    <td><Badge tone={getBadgeTone(ad.delivery_status)}>{formatRelativeStatus(ad.delivery_status)}</Badge></td>
                    <td>{formatMoney(ad.spend)}</td>
                    <td>{formatMetricText(ad.clicks)}</td>
                    <td>{formatMoney(ad.cpc)}</td>
                    <td>{formatMetricText(ad.leads)}</td>
                    <td>{formatMoney(ad.cost_per_lead)}</td>
                    <td>{formatMetricText(ad.registrations)}</td>
                    <td>{formatMoney(ad.cost_per_registration)}</td>
                    <td>{formatMetricText(ad.deposits)}</td>
                    <td>{formatDateTime(ad.last_seen_at)}</td>
                  </tr>
                ))
              )}
            </tbody>
          </table>
        </div>
      </SectionCard>
    </>
  );
}
