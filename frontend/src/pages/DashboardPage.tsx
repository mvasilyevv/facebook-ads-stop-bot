import { useEffect, useMemo, useState, startTransition } from "react";
import { Link } from "react-router-dom";
import { GroupedAdsBoard } from "../components/GroupedAdsBoard";
import { SectionCard } from "../components/SectionCard";
import { useAutoRefresh } from "../hooks/useAutoRefresh";
import { isAttentionAdSummary } from "../lib/helpers";
import { fetchScanRuns, fetchServiceSettings, loadDashboard } from "../lib/api";
import { formatCountdown, formatDateTime, formatRelativeStatus } from "../lib/format";
import type { ScanRunItem, ServiceSettingsResponse } from "../types";

type DashboardData = Awaited<ReturnType<typeof loadDashboard>>;

function formatModeLabel(settings: ServiceSettingsResponse | null): string {
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

function isActiveSession(status: string): boolean {
  const normalized = status.toLowerCase();
  return normalized.includes("active") || normalized.includes("running") || normalized.includes("open");
}

function resolveLastScan(scans: ScanRunItem[]): ScanRunItem | null {
  if (scans.length === 0) {
    return null;
  }

  return [...scans].sort((left, right) => {
    const leftStamp = new Date(left.finished_at ?? left.started_at).getTime();
    const rightStamp = new Date(right.finished_at ?? right.started_at).getTime();
    return rightStamp - leftStamp;
  })[0];
}

function resolveNextScanCountdown(
  scan: ScanRunItem | null,
  scanIntervalSeconds: number | null,
  nowStamp: number,
): number | null {
  if (scan == null || scanIntervalSeconds == null) {
    return null;
  }

  const anchorValue = scan.finished_at ?? scan.started_at;
  const anchorStamp = new Date(anchorValue).getTime();
  if (Number.isNaN(anchorStamp)) {
    return null;
  }

  const nextScanStamp = anchorStamp + scanIntervalSeconds * 1000;
  return Math.max(0, Math.ceil((nextScanStamp - nowStamp) / 1000));
}

function resolveNextScanLabel(
  scan: ScanRunItem | null,
  settings: ServiceSettingsResponse | null,
  nowStamp: number,
): string {
  if (!settings) {
    return "нет данных";
  }
  if (!settings.auto_pause_enabled && !settings.auto_resume_enabled) {
    return "скан выключен";
  }
  if (scan == null) {
    return "ожидаем запуск";
  }
  if (scan.status.toUpperCase() === "RUNNING") {
    return "выполняется";
  }

  const secondsLeft = resolveNextScanCountdown(scan, settings.scan_interval_seconds, nowStamp);
  if (secondsLeft == null) {
    return "нет данных";
  }
  if (secondsLeft <= 0) {
    return "ожидаем запуск";
  }
  return formatCountdown(secondsLeft);
}

export default function DashboardPage() {
  const [dashboardData, setDashboardData] = useState<DashboardData | null>(null);
  const [serviceSettings, setServiceSettings] = useState<ServiceSettingsResponse | null>(null);
  const [scanRuns, setScanRuns] = useState<ScanRunItem[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [lastLoadedAt, setLastLoadedAt] = useState<string | null>(null);
  const [nowStamp, setNowStamp] = useState(() => Date.now());

  async function reload(silent = false) {
    if (!silent) {
      setLoading(true);
    }
    setError(null);

    const [dashboardResult, settingsResult, scansResult] = await Promise.allSettled([
      loadDashboard(),
      fetchServiceSettings(),
      fetchScanRuns(),
    ]);
    const errors: string[] = [];

    if (dashboardResult.status === "fulfilled") {
      startTransition(() => {
        setDashboardData(dashboardResult.value);
        setLastLoadedAt(new Date().toISOString());
      });
    } else {
      errors.push(dashboardResult.reason instanceof Error ? dashboardResult.reason.message : "Не удалось загрузить обзор");
    }

    if (settingsResult.status === "fulfilled") {
      startTransition(() => {
        setServiceSettings(settingsResult.value);
      });
    } else {
      errors.push(settingsResult.reason instanceof Error ? settingsResult.reason.message : "Не удалось загрузить настройки");
    }

    if (scansResult.status === "fulfilled") {
      startTransition(() => {
        setScanRuns(scansResult.value);
      });
    } else {
      errors.push(scansResult.reason instanceof Error ? scansResult.reason.message : "Не удалось загрузить сканы");
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

  useAutoRefresh(reload, { enabled: !loading });

  useEffect(() => {
    const timerId = window.setInterval(() => {
      setNowStamp(Date.now());
    }, 1000);

    return () => {
      window.clearInterval(timerId);
    };
  }, []);

  const trackedAds = dashboardData?.ads.filter((ad) => ad.tracking_mode === "TRACKED") ?? [];
  const staleTrackedAds = trackedAds.filter((ad) => ad.scope_presence === "NOT_SEEN_THIS_SCAN");
  const attentionTrackedAds = trackedAds.filter((ad) => isAttentionAdSummary(ad));
  const activeSessions = dashboardData?.sessions.filter((session) => isActiveSession(session.status)) ?? [];
  const activeCampaigns = new Set(
    trackedAds
      .filter((ad) => ad.delivery_status.toUpperCase().includes("ACTIVE"))
      .map((ad) => ad.campaign_name),
  ).size;
  const modeLabel = formatModeLabel(serviceSettings);
  const lastScan = useMemo(() => resolveLastScan(scanRuns), [scanRuns]);
  const nextScanLabel = resolveNextScanLabel(lastScan, serviceSettings, nowStamp);

  if (loading) {
    return <div className="page-loading">Загрузка данных...</div>;
  }

  return (
    <>
      <div className="page-header">
        <div>
          <h1 className="page-title">Обзор системы</h1>
          <p className="page-subtitle">Краткая сводка по состоянию backend и отслеживаемым объявлениям</p>
        </div>
        <div className="page-header__actions">
          <span className="section-note">{lastLoadedAt ? `Обновлено: ${formatDateTime(lastLoadedAt)}` : ""}</span>
          <button type="button" className="button button--ghost" onClick={() => void reload(true)}>
            Обновить
          </button>
          <Link to="/settings" className="button button--primary">
            Настройки
          </Link>
        </div>
      </div>

      {error ? <div className="inline-error">{error}</div> : null}

      <div className="metric-grid dashboard-summary-grid">
        <article className="metric-tile metric-tile--accent">
          <span>Режим</span>
          <strong>{modeLabel}</strong>
          <div className="mini-row">
            <span>Автопауза</span>
            <span>{serviceSettings?.auto_pause_enabled ? "включена" : "выключена"}</span>
          </div>
        </article>
        <article className="metric-tile">
          <span>Частота</span>
          <strong>{serviceSettings ? `${serviceSettings.scan_interval_seconds} секунд` : "нет данных"}</strong>
          <div className="mini-row">
            <span>Авторезюм</span>
            <span>{serviceSettings?.auto_resume_available ? "доступен" : "недоступен"}</span>
          </div>
          <div className="mini-row">
            <span>До следующего скана</span>
            <span>{nextScanLabel}</span>
          </div>
        </article>
        <article className="metric-tile">
          <span>Отслеживаемые</span>
          <strong>{trackedAds.length}</strong>
          <div className="mini-row">
            <span>Нет в последнем скане</span>
            <span>{staleTrackedAds.length}</span>
          </div>
        </article>
        <article className="metric-tile">
          <span>Статус</span>
          <strong>{attentionTrackedAds.length > 0 ? `проверить ${attentionTrackedAds.length}` : "стабильно"}</strong>
          <div className="mini-row">
            <span>Активных кампаний</span>
            <span>{activeCampaigns}</span>
          </div>
          <div className="mini-row">
            <span>Сессии</span>
            <span>{activeSessions.length}</span>
          </div>
        </article>
      </div>

      <SectionCard
        title="Здоровье системы"
        subtitle="Краткая сводка по backend"
        actions={
          <span className="section-note">
            {dashboardData?.health?.timestamp ? `Снимок: ${formatDateTime(dashboardData.health.timestamp)}` : "Снимок отсутствует"}
          </span>
        }
      >
        <div className="metric-grid">
          <article className="metric-tile metric-tile--accent">
            <span>Сервис</span>
            <strong>{dashboardData?.health?.service ?? "API"}</strong>
          </article>
          <article className="metric-tile">
            <span>Статус</span>
            <strong>{dashboardData?.health ? formatRelativeStatus(dashboardData.health.status) : "нет ответа"}</strong>
          </article>
          <article className="metric-tile">
            <span>Окружение</span>
            <strong>{dashboardData?.health?.environment ?? "неизвестно"}</strong>
          </article>
          <article className="metric-tile">
            <span>База данных</span>
            <strong>{dashboardData?.health?.database_status ?? "нет данных"}</strong>
          </article>
        </div>
      </SectionCard>

      <SectionCard
        title="Плиточный обзор"
        subtitle="Короткая сводка по объявлениям с последним понятным действием прямо в карточке"
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
