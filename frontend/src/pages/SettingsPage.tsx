import { useEffect, useMemo, useState, startTransition } from "react";
import { Link } from "react-router-dom";
import { Badge } from "../components/Badge";
import { EmptyState } from "../components/EmptyState";
import { GroupedAdsBoard } from "../components/GroupedAdsBoard";
import { RuleEditor, type RuleOfferPreview } from "../components/RuleEditor";
import { SectionCard } from "../components/SectionCard";
import { useAutoRefresh } from "../hooks/useAutoRefresh";
import {
  fetchScanRuns,
  fetchServiceSettings,
  fetchSuspendedProfiles,
  loadDashboard,
  resetSuspendedProfile,
  updateServiceSettings,
  saveRule,
} from "../lib/api";
import { formatDateTime } from "../lib/format";
import type {
  RuleItem,
  ScanRunItem,
  ServiceSettings,
  ServiceSettingsResponse,
  ServiceSettingsUpdate,
  SuspendedProfileItem,
} from "../types";

type DashboardData = Awaited<ReturnType<typeof loadDashboard>>;

const FULL_SCAN_INTERVAL_OPTIONS = [30, 60, 120, 300] as const;
const RECHECK_INTERVAL_OPTIONS = [5, 10, 15, 30, 60] as const;
const CONCURRENCY_OPTIONS = [1, 2, 3, 4, 5] as const;

const DEFAULT_DRAFT: ServiceSettings = {
  auto_pause_enabled: true,
  auto_resume_enabled: false,
  observe_only_enabled: true,
  full_scan_interval_seconds: 120,
  recheck_interval_seconds: 15,
  full_scan_profile_concurrency: 2,
  action_worker_concurrency: 2,
  vision_api_token: "",
  telegram_bot_token: "",
  telegram_chat_id: "",
  vision_local_api_url: "",
  vision_cloud_api_url: "",
  updated_at: null,
};

function createDraftFromResponse(response: ServiceSettingsResponse): ServiceSettings {
  return {
    auto_pause_enabled: response.auto_pause_enabled,
    auto_resume_enabled: response.auto_resume_enabled,
    observe_only_enabled: response.observe_only_enabled,
    full_scan_interval_seconds: response.full_scan_interval_seconds,
    recheck_interval_seconds: response.recheck_interval_seconds,
    full_scan_profile_concurrency: response.full_scan_profile_concurrency,
    action_worker_concurrency: response.action_worker_concurrency,
    vision_api_token: "",
    telegram_bot_token: "",
    telegram_chat_id: response.telegram_chat_id,
    vision_local_api_url: response.vision_local_api_url,
    vision_cloud_api_url: response.vision_cloud_api_url,
    updated_at: response.updated_at,
  };
}

function buildRulePreviews(offers: DashboardData["offers"]): RuleOfferPreview[] {
  return offers
    .filter((offer) => offer.is_active && offer.current_cpa_usd != null)
    .map((offer) => ({
      offerId: offer.id,
      offerName: offer.name,
      offerCode: offer.code,
      cpaUsd: Number(offer.current_cpa_usd),
    }))
    .filter((item): item is RuleOfferPreview => Number.isFinite(item.cpaUsd));
}

function isActiveSession(status: string): boolean {
  const normalized = status.toLowerCase();
  return normalized.includes("active") || normalized.includes("running") || normalized.includes("open");
}

function scoreScanFailure(scan: ScanRunItem): number {
  if (scan.error_message) {
    return 3;
  }
  const status = scan.status.toLowerCase();
  if (status.includes("fail") || status.includes("error")) {
    return 2;
  }
  if (status.includes("skip")) {
    return 1;
  }
  return 0;
}

function buildProblemProfiles(profiles: SuspendedProfileItem[]): SuspendedProfileItem[] {
  return [...profiles].sort(
    (left, right) => new Date(right.suspended_at).getTime() - new Date(left.suspended_at).getTime(),
  );
}

function serviceSettingsToPayload(settings: ServiceSettings): ServiceSettingsUpdate {
  return {
    auto_pause_enabled: settings.auto_pause_enabled,
    auto_resume_enabled: settings.auto_resume_enabled,
    observe_only_enabled: settings.observe_only_enabled,
    full_scan_interval_seconds: settings.full_scan_interval_seconds,
    recheck_interval_seconds: settings.recheck_interval_seconds,
    full_scan_profile_concurrency: settings.full_scan_profile_concurrency,
    action_worker_concurrency: settings.action_worker_concurrency,
    telegram_chat_id: settings.telegram_chat_id.trim(),
    vision_local_api_url: settings.vision_local_api_url.trim(),
    vision_cloud_api_url: settings.vision_cloud_api_url.trim(),
    vision_api_token: settings.vision_api_token.trim() ? settings.vision_api_token.trim() : undefined,
    telegram_bot_token: settings.telegram_bot_token.trim() ? settings.telegram_bot_token.trim() : undefined,
  };
}

function formatModeLabel(settings: ServiceSettings | ServiceSettingsResponse | null): string {
  if (!settings) {
    return "нет данных";
  }
  if (settings.observe_only_enabled) {
    return "наблюдение";
  }
  if (settings.auto_pause_enabled || settings.auto_resume_enabled) {
    return "боевой";
  }
  return "только обзор";
}

function isServiceDraftDirty(
  draft: ServiceSettings,
  serviceSettings: ServiceSettingsResponse | null,
): boolean {
  if (serviceSettings == null) {
    return (
      draft.auto_pause_enabled !== DEFAULT_DRAFT.auto_pause_enabled ||
      draft.auto_resume_enabled !== DEFAULT_DRAFT.auto_resume_enabled ||
      draft.observe_only_enabled !== DEFAULT_DRAFT.observe_only_enabled ||
      draft.full_scan_interval_seconds !== DEFAULT_DRAFT.full_scan_interval_seconds ||
      draft.recheck_interval_seconds !== DEFAULT_DRAFT.recheck_interval_seconds ||
      draft.full_scan_profile_concurrency !== DEFAULT_DRAFT.full_scan_profile_concurrency ||
      draft.action_worker_concurrency !== DEFAULT_DRAFT.action_worker_concurrency ||
      draft.telegram_chat_id.trim() !== DEFAULT_DRAFT.telegram_chat_id ||
      draft.vision_local_api_url.trim() !== DEFAULT_DRAFT.vision_local_api_url ||
      draft.vision_cloud_api_url.trim() !== DEFAULT_DRAFT.vision_cloud_api_url ||
      draft.vision_api_token.trim() !== "" ||
      draft.telegram_bot_token.trim() !== ""
    );
  }

  const savedDraft = createDraftFromResponse(serviceSettings);
  return (
    draft.auto_pause_enabled !== savedDraft.auto_pause_enabled ||
    draft.auto_resume_enabled !== savedDraft.auto_resume_enabled ||
    draft.observe_only_enabled !== savedDraft.observe_only_enabled ||
    draft.full_scan_interval_seconds !== savedDraft.full_scan_interval_seconds ||
    draft.recheck_interval_seconds !== savedDraft.recheck_interval_seconds ||
    draft.full_scan_profile_concurrency !== savedDraft.full_scan_profile_concurrency ||
    draft.action_worker_concurrency !== savedDraft.action_worker_concurrency ||
    draft.telegram_chat_id.trim() !== savedDraft.telegram_chat_id.trim() ||
    draft.vision_local_api_url.trim() !== savedDraft.vision_local_api_url.trim() ||
    draft.vision_cloud_api_url.trim() !== savedDraft.vision_cloud_api_url.trim() ||
    draft.vision_api_token.trim() !== "" ||
    draft.telegram_bot_token.trim() !== ""
  );
}

export default function SettingsPage() {
  const [dashboardData, setDashboardData] = useState<DashboardData | null>(null);
  const [serviceSettings, setServiceSettings] = useState<ServiceSettingsResponse | null>(null);
  const [draft, setDraft] = useState<ServiceSettings>(DEFAULT_DRAFT);
  const [suspendedProfiles, setSuspendedProfiles] = useState<SuspendedProfileItem[]>([]);
  const [scanRuns, setScanRuns] = useState<ScanRunItem[]>([]);
  const [loading, setLoading] = useState(true);
  const [saving, setSaving] = useState(false);
  const [updatingMode, setUpdatingMode] = useState(false);
  const [resettingProfileId, setResettingProfileId] = useState<string | null>(null);
  const [message, setMessage] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);

  async function reload(silent = false) {
    if (!silent) {
      setLoading(true);
    }
    setError(null);
    const shouldSyncDraft = !silent || !isServiceDraftDirty(draft, serviceSettings);

    const [dashboardResult, serviceResult, suspendedResult, scanResult] = await Promise.allSettled([
      loadDashboard(),
      fetchServiceSettings(),
      fetchSuspendedProfiles(),
      fetchScanRuns(),
    ]);

    const errors: string[] = [];

    if (dashboardResult.status === "fulfilled") {
      startTransition(() => {
        setDashboardData(dashboardResult.value);
      });
    } else {
      errors.push(dashboardResult.reason instanceof Error ? dashboardResult.reason.message : "Не удалось загрузить обзор");
    }

    if (serviceResult.status === "fulfilled") {
      startTransition(() => {
        setServiceSettings(serviceResult.value);
        if (shouldSyncDraft) {
          setDraft(createDraftFromResponse(serviceResult.value));
        }
      });
    } else {
      errors.push(serviceResult.reason instanceof Error ? serviceResult.reason.message : "Не удалось загрузить настройки");
    }

    if (suspendedResult.status === "fulfilled") {
      startTransition(() => {
        setSuspendedProfiles(buildProblemProfiles(suspendedResult.value));
      });
    } else {
      errors.push(suspendedResult.reason instanceof Error ? suspendedResult.reason.message : "Не удалось загрузить профили");
    }

    if (scanResult.status === "fulfilled") {
      startTransition(() => {
        setScanRuns(scanResult.value);
      });
    } else {
      errors.push(scanResult.reason instanceof Error ? scanResult.reason.message : "Не удалось загрузить сканы");
    }

    startTransition(() => {
      setLoading(false);
      if (errors.length > 0) {
        setError(errors.join(" · "));
      }
    });
  }

  useEffect(() => {
    void reload();
  }, []);

  const hasUnsavedServiceSettings = useMemo(
    () => isServiceDraftDirty(draft, serviceSettings),
    [draft, serviceSettings],
  );

  useAutoRefresh(reload, {
    enabled:
      !loading &&
      !saving &&
      !updatingMode &&
      resettingProfileId == null &&
      !hasUnsavedServiceSettings,
  });

  const trackedAds = dashboardData?.ads.filter((ad) => ad.tracking_mode === "TRACKED") ?? [];
  const visibleRules = useMemo(
    () =>
      [...(dashboardData?.rules ?? [])].sort(
        (left, right) => left.priority - right.priority || new Date(right.updated_at).getTime() - new Date(left.updated_at).getTime(),
      ),
    [dashboardData?.rules],
  );
  const rulePreviews = useMemo(() => buildRulePreviews(dashboardData?.offers ?? []), [dashboardData?.offers]);
  const activeSessions = dashboardData?.sessions.filter((session) => isActiveSession(session.status)) ?? [];
  const failedScans = scanRuns.filter((scan) => scoreScanFailure(scan) > 0);
  const lastScan = scanRuns[0] ?? null;

  async function saveServiceSettings(nextPatch?: Partial<ServiceSettings>) {
    setSaving(true);
    setError(null);
    try {
      const nextDraft: ServiceSettings = {
        ...draft,
        ...(nextPatch ?? {}),
      };
      const response = await updateServiceSettings(serviceSettingsToPayload(nextDraft));
      startTransition(() => {
        setServiceSettings(response);
        setDraft(createDraftFromResponse(response));
      });
      setMessage("Настройки сервиса сохранены");
      await reload(true);
    } catch (e) {
      setError(e instanceof Error ? e.message : "Не удалось сохранить настройки");
    } finally {
      setSaving(false);
    }
  }

  async function toggleServiceFlag(
    key: "auto_pause_enabled" | "auto_resume_enabled" | "observe_only_enabled",
    value: boolean,
  ) {
    if (key === "auto_resume_enabled" && serviceSettings && !serviceSettings.auto_resume_available) {
      return;
    }
    setUpdatingMode(true);
    setError(null);
    try {
      const source = serviceSettings ?? {
        auto_pause_enabled: draft.auto_pause_enabled,
        auto_resume_enabled: draft.auto_resume_enabled,
        auto_resume_available: true,
        observe_only_enabled: draft.observe_only_enabled,
        full_scan_interval_seconds: draft.full_scan_interval_seconds,
        recheck_interval_seconds: draft.recheck_interval_seconds,
        full_scan_profile_concurrency: draft.full_scan_profile_concurrency,
        action_worker_concurrency: draft.action_worker_concurrency,
        vision_local_api_url: draft.vision_local_api_url,
        vision_cloud_api_url: draft.vision_cloud_api_url,
        telegram_chat_id: draft.telegram_chat_id,
        vision_api_token_masked: null,
        telegram_bot_token_masked: null,
        vision_api_token_configured: false,
        telegram_bot_token_configured: false,
        updated_at: draft.updated_at ?? new Date().toISOString(),
      };
      const response = await updateServiceSettings({
        auto_pause_enabled: key === "auto_pause_enabled" ? value : source.auto_pause_enabled,
        auto_resume_enabled: key === "auto_resume_enabled" ? value : source.auto_resume_enabled,
        observe_only_enabled: key === "observe_only_enabled" ? value : source.observe_only_enabled,
        full_scan_interval_seconds: source.full_scan_interval_seconds,
        recheck_interval_seconds: source.recheck_interval_seconds,
        full_scan_profile_concurrency: source.full_scan_profile_concurrency,
        action_worker_concurrency: source.action_worker_concurrency,
        vision_local_api_url: source.vision_local_api_url,
        vision_cloud_api_url: source.vision_cloud_api_url,
        telegram_chat_id: source.telegram_chat_id,
      });
      startTransition(() => {
        setServiceSettings(response);
        setDraft((current) => ({
          ...current,
          auto_pause_enabled: response.auto_pause_enabled,
          auto_resume_enabled: response.auto_resume_enabled,
          observe_only_enabled: response.observe_only_enabled,
        }));
      });
      setMessage("Режим бота обновлён");
      await reload(true);
    } catch (e) {
      setError(e instanceof Error ? e.message : "Не удалось обновить режим");
    } finally {
      setUpdatingMode(false);
    }
  }

  async function resetProfile(profileId: string) {
    setResettingProfileId(profileId);
    setError(null);
    try {
      await resetSuspendedProfile(profileId);
      setMessage(`Профиль ${profileId} снова разрешён для сканирования`);
      await reload(true);
    } catch (e) {
      setError(e instanceof Error ? e.message : "Не удалось сбросить стоп профиля");
    } finally {
      setResettingProfileId(null);
    }
  }

  async function handleRuleSave(ruleId: string, ruleCode: string, payload: Partial<RuleItem>) {
    setError(null);
    try {
      await saveRule(ruleId, payload);
      setMessage(`Правило ${ruleCode} сохранено`);
      await reload(true);
    } catch (e) {
      setError(e instanceof Error ? e.message : "Не удалось сохранить правило");
    }
  }

  if (loading) {
    return <div className="page-loading">Загрузка настроек...</div>;
  }

  const visionConfigured = serviceSettings?.vision_api_token_configured || Boolean(draft.vision_api_token.trim());
  const telegramConfigured = serviceSettings?.telegram_bot_token_configured || Boolean(draft.telegram_bot_token.trim());
  const lastUpdatedAt = serviceSettings?.updated_at ?? dashboardData?.health?.timestamp ?? lastScan?.started_at ?? null;
  const activeMode = formatModeLabel(serviceSettings ?? draft);

  return (
    <>
      <div className="page-header">
        <div>
          <h1 className="page-title">Настройки</h1>
          <p className="page-subtitle">Режимы, ключи, правила и проблемные профили в одном экране</p>
        </div>
        <div className="page-header__actions">
          {lastUpdatedAt ? <span className="section-note">Обновлено: {formatDateTime(lastUpdatedAt)}</span> : null}
          <button type="button" className="button button--ghost" onClick={() => void reload(true)}>
            Обновить
          </button>
          <Link to="/" className="button button--primary">
            На обзор
          </Link>
        </div>
      </div>

      {error ? <div className="inline-error">{error}</div> : null}
      {message ? <div className="message-banner">{message}</div> : null}

      <div className="metric-grid settings-summary-grid">
        <article className="metric-tile metric-tile--accent">
          <span>Режим</span>
          <strong>{activeMode}</strong>
          <div className="mini-row">
            <span>Автопауза</span>
            <span>{serviceSettings?.auto_pause_enabled ? "включена" : "выключена"}</span>
          </div>
        </article>
        <article className="metric-tile">
          <span>Пайплайн сканов</span>
          <strong>{draft.full_scan_interval_seconds} / {draft.recheck_interval_seconds}</strong>
          <div className="mini-row">
            <span>Параллельность</span>
            <span>{draft.full_scan_profile_concurrency} / {draft.action_worker_concurrency}</span>
          </div>
        </article>
        <article className="metric-tile">
          <span>Vision</span>
          <strong>{visionConfigured ? "настроен" : "не задан"}</strong>
          <div className="mini-row">
            <span>Локальный URL</span>
            <span>{draft.vision_local_api_url.trim() ? "есть" : "нет"}</span>
          </div>
        </article>
        <article className="metric-tile">
          <span>Telegram</span>
          <strong>{telegramConfigured ? "настроен" : "не задан"}</strong>
          <div className="mini-row">
            <span>Чат</span>
            <span>{draft.telegram_chat_id.trim() ? "есть" : "нет"}</span>
          </div>
        </article>
        <article className="metric-tile">
          <span>Проблемные профили</span>
          <strong>{suspendedProfiles.length}</strong>
          <div className="mini-row">
            <span>Неуспешные сканы</span>
            <span>{failedScans.length}</span>
          </div>
        </article>
        <article className="metric-tile">
          <span>Активные сессии</span>
          <strong>{activeSessions.length}</strong>
          <div className="mini-row">
            <span>Всего сессий</span>
            <span>{dashboardData?.sessions.length ?? 0}</span>
          </div>
        </article>
      </div>

      <SectionCard
        title="Автоматизация"
        subtitle="Переключатели режима и частота сканирования"
        actions={updatingMode ? <span className="section-note">Обновляется...</span> : null}
      >
        <div className="settings-switch-grid">
          <label className="settings-switch">
            <input
              type="checkbox"
              checked={serviceSettings?.auto_pause_enabled ?? false}
              onChange={(event) => void toggleServiceFlag("auto_pause_enabled", event.target.checked)}
              disabled={updatingMode || serviceSettings == null}
            />
            <span>
              <strong>Автопауза</strong>
              <small>Бот может ставить объявления на паузу</small>
            </span>
          </label>
          <label className="settings-switch">
            <input
              type="checkbox"
              checked={serviceSettings?.auto_resume_enabled ?? false}
              onChange={(event) => void toggleServiceFlag("auto_resume_enabled", event.target.checked)}
              disabled={updatingMode || serviceSettings == null || !serviceSettings.auto_resume_available}
            />
            <span>
              <strong>Авторезюм</strong>
              <small>
                {serviceSettings?.auto_resume_available
                  ? "Бот может возвращать объявления из паузы"
                  : "Недоступно без feature flag"}
              </small>
            </span>
          </label>
          <label className="settings-switch settings-switch--accent">
            <input
              type="checkbox"
              checked={serviceSettings?.observe_only_enabled ?? true}
              onChange={(event) => void toggleServiceFlag("observe_only_enabled", event.target.checked)}
              disabled={updatingMode || serviceSettings == null}
            />
            <span>
              <strong>Режим наблюдения</strong>
              <small>Бот считает решения, но не нажимает кнопки</small>
            </span>
          </label>
        </div>
        <div className="settings-interval-grid">
          <label className="panel-form">
            <span>Частота полного скана</span>
            <select
              className="input input--compact settings-select"
              aria-label="Частота полного скана"
              value={draft.full_scan_interval_seconds}
              onChange={(event) =>
                setDraft((current) => ({ ...current, full_scan_interval_seconds: Number(event.target.value) }))
              }
            >
              {FULL_SCAN_INTERVAL_OPTIONS.map((value) => (
                <option key={value} value={value}>
                  {value} секунд
                </option>
              ))}
            </select>
          </label>
          <label className="panel-form">
            <span>Частота быстрой перепроверки</span>
            <select
              className="input input--compact settings-select"
              aria-label="Частота быстрой перепроверки"
              value={draft.recheck_interval_seconds}
              onChange={(event) =>
                setDraft((current) => ({ ...current, recheck_interval_seconds: Number(event.target.value) }))
              }
            >
              {RECHECK_INTERVAL_OPTIONS.map((value) => (
                <option key={value} value={value}>
                  {value} секунд
                </option>
              ))}
            </select>
          </label>
          <label className="panel-form">
            <span>Параллельность полного скана</span>
            <select
              className="input input--compact settings-select"
              aria-label="Параллельность полного скана"
              value={draft.full_scan_profile_concurrency}
              onChange={(event) =>
                setDraft((current) => ({ ...current, full_scan_profile_concurrency: Number(event.target.value) }))
              }
            >
              {CONCURRENCY_OPTIONS.map((value) => (
                <option key={value} value={value}>
                  {value}
                </option>
              ))}
            </select>
          </label>
          <label className="panel-form">
            <span>Параллельность очереди действий</span>
            <select
              className="input input--compact settings-select"
              aria-label="Параллельность очереди действий"
              value={draft.action_worker_concurrency}
              onChange={(event) =>
                setDraft((current) => ({ ...current, action_worker_concurrency: Number(event.target.value) }))
              }
            >
              {CONCURRENCY_OPTIONS.map((value) => (
                <option key={value} value={value}>
                  {value}
                </option>
              ))}
            </select>
          </label>
        </div>
      </SectionCard>

      <div className="settings-columns">
        <SectionCard
          title="Интеграции"
          subtitle="Ключи и адреса сохраняются на сервере через settings API"
          actions={
            <button type="button" className="button button--primary button--small" onClick={() => void saveServiceSettings()} disabled={saving}>
              {saving ? "Сохраняем..." : "Сохранить настройки"}
            </button>
          }
        >
          <div className="form-grid settings-form-grid">
            <label className="panel-form">
              <span>Vision API ключ</span>
              <input
                className="input"
                aria-label="Vision API ключ"
                type="password"
                value={draft.vision_api_token}
                onChange={(event) => setDraft((current) => ({ ...current, vision_api_token: event.target.value }))}
                placeholder={serviceSettings?.vision_api_token_masked ?? "Введите ключ"}
              />
              <span className="section-note">
                {serviceSettings?.vision_api_token_configured ? "Секрет уже сохранён на сервере" : "Секрет ещё не задан"}
              </span>
            </label>
            <label className="panel-form">
              <span>Vision локальный URL</span>
              <input
                className="input"
                aria-label="Vision локальный URL"
                type="url"
                value={draft.vision_local_api_url}
                onChange={(event) => setDraft((current) => ({ ...current, vision_local_api_url: event.target.value }))}
                placeholder="http://127.0.0.1:..."
              />
            </label>
            <label className="panel-form">
              <span>Vision cloud URL</span>
              <input
                className="input"
                aria-label="Vision cloud URL"
                type="url"
                value={draft.vision_cloud_api_url}
                onChange={(event) => setDraft((current) => ({ ...current, vision_cloud_api_url: event.target.value }))}
                placeholder="https://..."
              />
            </label>
            <label className="panel-form">
              <span>Telegram bot token</span>
              <input
                className="input"
                aria-label="Telegram bot token"
                type="password"
                value={draft.telegram_bot_token}
                onChange={(event) => setDraft((current) => ({ ...current, telegram_bot_token: event.target.value }))}
                placeholder={serviceSettings?.telegram_bot_token_masked ?? "Введите токен"}
              />
              <span className="section-note">
                {serviceSettings?.telegram_bot_token_configured ? "Токен уже сохранён на сервере" : "Токен ещё не задан"}
              </span>
            </label>
            <label className="panel-form">
              <span>Telegram chat id</span>
              <input
                className="input"
                aria-label="Telegram chat id"
                type="text"
                value={draft.telegram_chat_id}
                onChange={(event) => setDraft((current) => ({ ...current, telegram_chat_id: event.target.value }))}
                placeholder="123456789"
              />
            </label>
          </div>
        </SectionCard>

        <SectionCard
          title="Проблемные профили"
          subtitle="Профили со стопом сканирования можно снять прямо здесь"
        >
          {suspendedProfiles.length === 0 ? (
            <EmptyState title="Проблемных профилей нет" description="Когда backend остановит профиль, он появится здесь." />
          ) : (
            <div className="problem-profiles">
              {suspendedProfiles.map((profile) => (
                <article key={profile.profile_id} className="problem-profile-card">
                  <div className="problem-profile-card__head">
                    <div>
                      <strong>{profile.display_name}</strong>
                      <div className="muted">{profile.profile_id}</div>
                    </div>
                    <Badge tone="bad">на стопе</Badge>
                  </div>
                  <div className="problem-profile-card__meta">
                    <span>Хост: {profile.browser_host_id}</span>
                    <span>Снимок: {formatDateTime(profile.suspended_at)}</span>
                  </div>
                  <p className="problem-profile-card__error">{profile.reason}</p>
                  <div className="row-actions">
                    <button
                      type="button"
                      className="button button--small button--ghost"
                      onClick={() => void resetProfile(profile.profile_id)}
                      disabled={resettingProfileId === profile.profile_id}
                    >
                      {resettingProfileId === profile.profile_id ? "Снимаем стоп..." : "Снять стоп"}
                    </button>
                  </div>
                </article>
              ))}
            </div>
          )}
        </SectionCard>
      </div>

      <SectionCard
        title="Правила"
        subtitle="Компактные карточки с быстрым редактированием процентов"
        actions={<span className="section-note">{visibleRules.length} правил</span>}
      >
        <div className="stack">
          {visibleRules.length === 0 ? (
            <EmptyState title="Правила не загружены" description="Список правил появится после ответа backend." />
          ) : (
            visibleRules.map((rule) => (
              <RuleEditor
                key={rule.id}
                rule={rule}
                offerPreviews={rulePreviews}
                onSave={(payload) => handleRuleSave(rule.id, rule.code, payload)}
              />
            ))
          )}
        </div>
      </SectionCard>

      <SectionCard
        title="Плиточный обзор"
        subtitle="Сводка по объявлениям с последним понятным действием прямо в карточке"
        actions={<Link to="/ads" className="button button--ghost button--small">Открыть объявления</Link>}
      >
        <GroupedAdsBoard
          ads={trackedAds}
          emptyTitle="Нет отслеживаемых объявлений"
          emptyDescription="Отслеживаемые объявления появятся после загрузки backend."
          compact
        />
      </SectionCard>
    </>
  );
}
