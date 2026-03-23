import { useEffect, useMemo, useState, startTransition } from "react";
import { Link } from "react-router-dom";
import { DecisionJournal } from "../components/DecisionJournal";
import { EmptyState } from "../components/EmptyState";
import { GroupedAdsBoard } from "../components/GroupedAdsBoard";
import { SectionCard } from "../components/SectionCard";
import { TrendStrip } from "../components/TrendStrip";
import { useAutoRefresh } from "../hooks/useAutoRefresh";
import {
  fetchAds,
  fetchActionJobs,
  fetchDecisions,
  fetchHealth,
  fetchProfileLaunchDashboard,
  fetchScanRuns,
  fetchServiceSettings,
  fetchWatchlist,
} from "../lib/api";
import { formatCountdown, formatDateTime, formatMoney } from "../lib/format";
import { isAttentionAdSummary } from "../lib/helpers";
import type {
  AdSummary,
  DecisionItem,
  HealthResponse,
  ProfileLaunchDashboard,
  ActionJobItem,
  ScanRunItem,
  ServiceSettingsResponse,
  WatchlistItem,
} from "../types";
import { useOperatorScope } from "../context/OperatorScopeContext";

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

function resolveLatestScanByKind(scans: ScanRunItem[], pipelineKind: ScanRunItem["pipeline_kind"]) {
  const filtered = scans.filter((scan) => scan.pipeline_kind === pipelineKind);
  return resolveLastScan(filtered);
}

function resolveReactionLagSeconds(
  watchlist: WatchlistItem[],
  actionJobs: ActionJobItem[],
  nowStamp: number,
): number | null {
  if (watchlist.length === 0 && actionJobs.length === 0) {
    return null;
  }
  const oldestWatchStamp = watchlist.reduce((oldest, entry) => {
    const stamp = new Date(entry.next_check_at).getTime();
    if (Number.isNaN(stamp)) {
      return oldest;
    }
    return oldest == null || stamp < oldest ? stamp : oldest;
  }, null as number | null);
  const oldestJobStamp = actionJobs.reduce((oldest, job) => {
    const stamp = new Date(job.next_attempt_at).getTime();
    if (Number.isNaN(stamp)) {
      return oldest;
    }
    return oldest == null || stamp < oldest ? stamp : oldest;
  }, null as number | null);
  const anchorStamp = Math.min(
    oldestWatchStamp ?? Number.POSITIVE_INFINITY,
    oldestJobStamp ?? Number.POSITIVE_INFINITY,
  );
  if (!Number.isFinite(anchorStamp)) {
    return null;
  }
  return Math.max(0, Math.ceil((nowStamp - anchorStamp) / 1000));
}

function resolveNextScanLabel(
  scan: ScanRunItem | null,
  settings: ServiceSettingsResponse | null,
  nowStamp: number,
  isArchive: boolean,
): string {
  if (isArchive) {
    return "архив";
  }
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
  const secondsLeft = resolveNextScanCountdown(scan, settings.full_scan_interval_seconds, nowStamp);
  if (secondsLeft == null) {
    return "нет данных";
  }
  if (secondsLeft <= 0) {
    return "ожидаем запуск";
  }
  return formatCountdown(secondsLeft);
}

function formatDelta(current: string | number, previous: string | number | null | undefined): string {
  const currentValue = Number(current ?? 0);
  const previousValue = Number(previous ?? 0);
  if (!Number.isFinite(currentValue) || !Number.isFinite(previousValue) || previous == null) {
    return "нет сравнения";
  }
  const delta = currentValue - previousValue;
  if (Math.abs(delta) < 0.0001) {
    return "без изменений";
  }
  return delta > 0 ? `+${delta.toFixed(2)}` : delta.toFixed(2);
}

function formatRiskBandLabel(value: WatchlistItem["risk_band"]): string {
  switch (value) {
    case "SAFE":
      return "без риска";
    case "WATCH":
      return "наблюдение";
    case "STOP":
      return "стоп";
    default:
      return String(value).toLowerCase();
  }
}

function formatActionStatusLabel(value: ActionJobItem["status"]): string {
  switch (value) {
    case "QUEUED":
      return "в очереди";
    case "RUNNING":
      return "в работе";
    case "RETRYING":
      return "повтор";
    case "SUCCEEDED":
      return "выполнено";
    case "FAILED":
      return "ошибка";
    case "CANCELLED":
      return "отменено";
    default:
      return String(value).toLowerCase();
  }
}

export default function DashboardPage() {
  const scope = useOperatorScope();
  const [health, setHealth] = useState<HealthResponse | null>(null);
  const [ads, setAds] = useState<AdSummary[]>([]);
  const [decisions, setDecisions] = useState<DecisionItem[]>([]);
  const [scanRuns, setScanRuns] = useState<ScanRunItem[]>([]);
  const [watchlist, setWatchlist] = useState<WatchlistItem[]>([]);
  const [actionJobs, setActionJobs] = useState<ActionJobItem[]>([]);
  const [serviceSettings, setServiceSettings] = useState<ServiceSettingsResponse | null>(null);
  const [launchDashboard, setLaunchDashboard] = useState<ProfileLaunchDashboard | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [message, setMessage] = useState<string | null>(null);
  const [lastLoadedAt, setLastLoadedAt] = useState<string | null>(null);
  const [nowStamp, setNowStamp] = useState(() => Date.now());

  const profileId = scope?.selectedProfileId ?? null;
  const launchId = scope?.selectedLaunchId ?? null;
  const selectedLaunch = scope?.selectedLaunch ?? null;
  const isArchiveLaunch = selectedLaunch != null && !selectedLaunch.is_active;

  async function reload(silent = false) {
    if (!silent) {
      setLoading(true);
    }
    setError(null);
    const filters =
      profileId && launchId
        ? {
            profileId,
            profileLaunchId: launchId,
          }
        : undefined;
    const requests = [
      fetchHealth(),
      fetchAds(filters),
      fetchDecisions(filters),
      fetchScanRuns(filters),
      fetchWatchlist(filters),
      fetchActionJobs(filters),
      fetchServiceSettings(),
      launchId ? fetchProfileLaunchDashboard(launchId) : Promise.resolve(null),
    ] as const;
    const [
      healthResult,
      adsResult,
      decisionsResult,
      scanRunsResult,
      watchlistResult,
      actionJobsResult,
      settingsResult,
      launchResult,
    ] =
      await Promise.allSettled(requests);

    const errors: string[] = [];

    if (healthResult.status === "fulfilled") {
      setHealth(healthResult.value);
    } else {
      errors.push(healthResult.reason instanceof Error ? healthResult.reason.message : "Не удалось загрузить health");
    }
    if (adsResult.status === "fulfilled") {
      startTransition(() => {
        setAds(adsResult.value);
      });
    } else {
      errors.push(adsResult.reason instanceof Error ? adsResult.reason.message : "Не удалось загрузить объявления");
    }
    if (decisionsResult.status === "fulfilled") {
      setDecisions(decisionsResult.value);
    } else {
      errors.push(decisionsResult.reason instanceof Error ? decisionsResult.reason.message : "Не удалось загрузить решения");
    }
    if (scanRunsResult.status === "fulfilled") {
      setScanRuns(scanRunsResult.value);
    } else {
      errors.push(scanRunsResult.reason instanceof Error ? scanRunsResult.reason.message : "Не удалось загрузить сканы");
    }
    if (watchlistResult.status === "fulfilled") {
      setWatchlist(watchlistResult.value);
    } else {
      errors.push(watchlistResult.reason instanceof Error ? watchlistResult.reason.message : "Не удалось загрузить watchlist");
    }
    if (actionJobsResult.status === "fulfilled") {
      setActionJobs(actionJobsResult.value);
    } else {
      errors.push(actionJobsResult.reason instanceof Error ? actionJobsResult.reason.message : "Не удалось загрузить очередь действий");
    }
    if (settingsResult.status === "fulfilled") {
      setServiceSettings(settingsResult.value);
    } else {
      errors.push(settingsResult.reason instanceof Error ? settingsResult.reason.message : "Не удалось загрузить настройки");
    }
    if (launchResult.status === "fulfilled") {
      setLaunchDashboard(launchResult.value);
    } else {
      errors.push(launchResult.reason instanceof Error ? launchResult.reason.message : "Не удалось загрузить сводку запуска");
    }

    startTransition(() => {
      setLastLoadedAt(new Date().toISOString());
      setLoading(false);
      setError(errors.length > 0 ? errors.join(" · ") : null);
    });
  }

  useEffect(() => {
    void reload();
  }, [profileId, launchId]);

  useAutoRefresh(reload, { enabled: !loading });

  useEffect(() => {
    const timerId = window.setInterval(() => {
      setNowStamp(Date.now());
    }, 1000);
    return () => window.clearInterval(timerId);
  }, []);

  const trackedAds = ads.filter((ad) => ad.tracking_mode === "TRACKED");
  const attentionTrackedAds = trackedAds.filter((ad) => isAttentionAdSummary(ad));
  const lastScan = useMemo(() => resolveLastScan(scanRuns), [scanRuns]);
  const lastFullScan = useMemo(() => resolveLatestScanByKind(scanRuns, "FULL_SCAN"), [scanRuns]);
  const lastRecheckScan = useMemo(
    () => resolveLatestScanByKind(scanRuns, "TARGETED_RECHECK"),
    [scanRuns],
  );
  const reactionLagSeconds = useMemo(
    () => resolveReactionLagSeconds(watchlist, actionJobs, nowStamp),
    [actionJobs, nowStamp, watchlist],
  );
  const nextScanLabel = resolveNextScanLabel(lastScan, serviceSettings, nowStamp, isArchiveLaunch);

  async function handleCreateLaunch() {
    if (!scope) {
      return;
    }
    try {
      const response = await scope.createLaunch();
      setMessage(response.message);
      await reload(true);
    } catch (err) {
      setError(err instanceof Error ? err.message : "Не удалось создать новый запуск");
    }
  }

  if (loading) {
    return <div className="page-loading">Загрузка данных...</div>;
  }

  if (scope && !profileId) {
    return (
      <EmptyState
        title="Профиль не выбран"
        description="Выберите профиль в верхней панели, чтобы открыть активный запуск и его историю."
      />
    );
  }

  return (
    <>
      <div className="page-header">
        <div>
          <h1 className="page-title">Обзор запуска</h1>
          <p className="page-subtitle">
            {selectedLaunch
              ? `${selectedLaunch.name}${isArchiveLaunch ? " · архивный просмотр" : ""}`
              : "Краткая сводка по текущему запуску и его динамике"}
          </p>
        </div>
        <div className="page-header__actions">
          <span className="section-note">{lastLoadedAt ? `Обновлено: ${formatDateTime(lastLoadedAt)}` : ""}</span>
          <button type="button" className="button button--ghost" onClick={() => void reload(true)}>
            Обновить
          </button>
          {scope ? (
            <button type="button" className="button button--primary" onClick={() => void handleCreateLaunch()}>
              Новый запуск
            </button>
          ) : null}
          <Link to="/ads" className="button button--ghost">
            Открыть объявления
          </Link>
        </div>
      </div>

      {message ? <div className="message-banner">{message}</div> : null}
      {error ? <div className="inline-error">{error}</div> : null}
      {isArchiveLaunch ? <div className="message-banner">Открыт архивный запуск. Действия отключены, доступен только просмотр.</div> : null}

      <div className="metric-grid dashboard-summary-grid">
        <article className="metric-tile metric-tile--accent">
          <span>Режим</span>
          <strong>{formatModeLabel(serviceSettings)}</strong>
          <div className="mini-row">
            <span>Полный скан</span>
            <span>{nextScanLabel}</span>
          </div>
        </article>
        <article className="metric-tile">
          <span>Объявления запуска</span>
          <strong>{launchDashboard?.current.total_ads ?? trackedAds.length}</strong>
          <div className="mini-row">
            <span>Требуют внимания</span>
            <span>{launchDashboard?.current.attention_ads ?? attentionTrackedAds.length}</span>
          </div>
        </article>
        <article className="metric-tile">
          <span>Быстрый стоп</span>
          <strong>{watchlist.length}</strong>
          <div className="mini-row">
            <span>Очередь действий</span>
            <span>{actionJobs.length}</span>
          </div>
        </article>
        <article className="metric-tile">
          <span>Задержка реакции</span>
          <strong>{reactionLagSeconds != null ? formatCountdown(reactionLagSeconds) : "нет очереди"}</strong>
          <div className="mini-row">
            <span>Последняя перепроверка</span>
            <span>{lastRecheckScan ? formatDateTime(lastRecheckScan.finished_at ?? lastRecheckScan.started_at) : "—"}</span>
          </div>
        </article>
        <article className="metric-tile">
          <span>Расход</span>
          <strong>{formatMoney(launchDashboard?.current.spend_total ?? 0)}</strong>
          <div className="mini-row">
            <span>К прошлому запуску</span>
            <span>{formatDelta(launchDashboard?.current.spend_total ?? 0, launchDashboard?.previous?.spend_total)}</span>
          </div>
        </article>
        <article className="metric-tile">
          <span>Сканов</span>
          <strong>{launchDashboard?.current.scans_count ?? scanRuns.length}</strong>
          <div className="mini-row">
            <span>Последний полный скан</span>
            <span>{lastFullScan ? formatDateTime(lastFullScan.finished_at ?? lastFullScan.started_at) : "—"}</span>
          </div>
        </article>
      </div>

        <SectionCard
        title="Сводка запуска"
        subtitle={
          launchDashboard?.previous_launch
            ? `Сравнение с ${launchDashboard.previous_launch.name}`
            : "Первый запуск профиля или архив ещё не накоплен"
        }
        actions={
          <span className="section-note">
            {health?.timestamp ? `Системный статус: ${formatDateTime(health.timestamp)}` : "Системный статус недоступен"}
          </span>
        }
      >
        <div className="metric-grid launch-compare-grid">
          <article className="metric-tile">
            <span>Активно</span>
            <strong>{launchDashboard?.current.active_ads ?? 0}</strong>
            <div className="mini-row">
              <span>На паузе</span>
              <span>{launchDashboard?.current.paused_ads ?? 0}</span>
            </div>
          </article>
          <article className="metric-tile">
            <span>Проблемные объявления</span>
            <strong>{launchDashboard?.current.attention_ads ?? 0}</strong>
            <div className="mini-row">
              <span>В прошлом запуске</span>
              <span>{launchDashboard?.previous?.attention_ads ?? "—"}</span>
            </div>
          </article>
          <article className="metric-tile">
            <span>Расход прошлого запуска</span>
            <strong>{formatMoney(launchDashboard?.previous?.spend_total ?? null)}</strong>
            <div className="mini-row">
              <span>Текущий запуск</span>
              <span>{formatMoney(launchDashboard?.current.spend_total ?? null)}</span>
            </div>
          </article>
        </div>
      </SectionCard>

      <SectionCard title="Тренды запуска" subtitle="Короткая динамика по сканам текущего периода">
        <div className="trend-grid">
          <TrendStrip title="Расход" points={launchDashboard?.spend_series ?? []} />
          <TrendStrip title="Проблемные решения" points={launchDashboard?.attention_series ?? []} />
          <TrendStrip title="Автодействия" points={launchDashboard?.action_series ?? []} />
        </div>
      </SectionCard>

      <SectionCard
        title="Быстрый стоп"
        subtitle="Список рискованных объявлений и очередь действий"
      >
        <div className="metric-grid launch-compare-grid">
          <article className="metric-tile">
            <span>Список наблюдения</span>
            <strong>{watchlist.length}</strong>
            <div className="mini-row">
              <span>STOP</span>
              <span>{watchlist.filter((item) => item.risk_band === "STOP").length}</span>
            </div>
          </article>
          <article className="metric-tile">
            <span>Очередь действий</span>
            <strong>{actionJobs.length}</strong>
            <div className="mini-row">
              <span>В работе</span>
              <span>{actionJobs.filter((item) => item.status === "RUNNING").length}</span>
            </div>
          </article>
        </div>
        {watchlist.length === 0 && actionJobs.length === 0 ? (
          <EmptyState
            title="Быстрый стоп пуст"
            description="Когда появятся рискованные объявления, здесь будут видны список наблюдения и очередь действий."
          />
        ) : (
          <div className="scan-summary scan-summary--stacked">
            {watchlist.slice(0, 5).map((item) => (
              <div key={item.id} className="scan-summary__item">
                <span className="mono">{item.fb_ad_id}</span>
                <strong>{formatRiskBandLabel(item.risk_band)}</strong>
                <span>{item.watch_reason || "Без причины"}</span>
              </div>
            ))}
            {actionJobs.slice(0, 5).map((job) => (
              <div key={job.id} className="scan-summary__item">
                <span className="mono">{job.fb_ad_id}</span>
                <strong>{formatActionStatusLabel(job.status)}</strong>
                <span>{job.last_error || "Без ошибок"}</span>
              </div>
            ))}
          </div>
        )}
      </SectionCard>

      <SectionCard
        title={`Объявления запуска (${trackedAds.length})`}
        subtitle="По умолчанию показан выбранный запуск истории"
      >
        {trackedAds.length === 0 ? (
          <EmptyState
            title="В запуске пока нет объявлений"
            description="После первого скана текущего запуска здесь появятся объявления и их карточки."
          />
        ) : (
          <GroupedAdsBoard
            ads={trackedAds}
            emptyTitle="В запуске пока нет объявлений"
            emptyDescription="После первого скана текущего запуска здесь появятся объявления и их карточки."
            compact
          />
        )}
      </SectionCard>

      <SectionCard title="Последние решения" subtitle="Журнал решений внутри выбранного запуска">
        <DecisionJournal
          decisions={decisions}
          emptyTitle="Решений пока нет"
          emptyDescription="После первого скана текущего запуска здесь появится журнал решений."
          compact
          maxItems={8}
        />
      </SectionCard>
    </>
  );
}
