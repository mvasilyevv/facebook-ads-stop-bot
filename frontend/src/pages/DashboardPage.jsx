import { useCallback, useEffect, useMemo, useState } from 'react';
import { ALERT_STATE_LABELS } from '../constants/alertStates.js';
import {
  getDashboardStats,
  getDashboardIncidents,
  getDisableTasks,
  getObserverSettings,
  toggleScanning,
  triggerScanNow,
  restartObserver,
  retryDisableTask,
  getDashboardPerformance,
  getEnableTasks,
  getEnableRecommendations,
  getChartData,
  getSpendHistory,
  createDisableTask,
  createEnableTaskFromRecommendation,
} from '../api.js';
import { useAsyncPolling } from '../hooks/useAsyncPolling.js';
import { useRefreshOnResume } from '../hooks/useRefreshOnResume.js';
import { AlertTray } from '../components/AlertTray.jsx';
import { CampaignScorecard, FunnelChart } from '../components/CampaignScorecard.jsx';

import { TaskQueuePanel } from '../components/TaskQueuePanel.jsx';
import { BudgetOverrunChart } from '../components/BudgetOverrunChart.jsx';
import { CampaignBreakdownTable } from '../components/CampaignBreakdownTable.jsx';
import { RuleViolationRanking } from '../components/RuleViolationRanking.jsx';
import { TopAdsQualityTable } from '../components/TopAdsQualityTable.jsx';
import { CampaignComparativeBars } from '../components/CampaignComparativeBars.jsx';
import { SpendAlertsChart } from '../components/SpendAlertsChart.jsx';

/* === Вспомогательные компоненты === */

function normalizeIncidentList(payload) {
  if (!Array.isArray(payload)) return [];
  return payload.map((item) => ({
    fb_ad_id: item.fb_ad_id,
    ad_name: item.ad_name,
    campaign_name: item.campaign_name,
    current_state: item.current_state,
    current_stage: item.current_stage,
    reason_title: item.reason_title,
    matched_rule_codes: item.matched_rule_codes || [],
    last_activity_at: item.last_activity_at,
    has_active_disable_task: item.has_active_disable_task,
  }));
}

/** Дельта: ▲/▼ к вчера */
function Delta({ today, yesterday, lowerIsBetter = false }) {
  if (today == null || yesterday == null) return null;
  const a = Number(today);
  const b = Number(yesterday);
  if (b === 0) return null;
  const diff = a - b;
  const pct = Math.abs((diff / b) * 100).toFixed(0);
  if (pct === '0') return null;
  const up = diff > 0;
  const good = lowerIsBetter ? !up : up;
  const color = good ? 'text-success' : 'text-danger';
  return (
    <span className={`font-mono text-2xs font-semibold ${color}`}>
      {up ? '↑' : '↓'} {pct}%
    </span>
  );
}

/** Hero-баннер алертов — полная ширина, первое что видит медиабаер */
function AlertBanner({ stats }) {
  const stopCount = stats?.ads_in_stop ?? 0;
  const warnCount = stats?.ads_in_warning ?? 0;
  const earlyCount = stats?.ads_in_early_signal ?? 0;

  if (stopCount === 0 && warnCount === 0 && earlyCount === 0) {
    return (
      <div className="panel flex items-center gap-3 px-4 py-3 mb-md border-success/30 bg-success-muted">
        <span className="status-dot bg-success animate-pulse-dot" />
        <span className="text-sm font-medium text-success">Все объявления в норме</span>
      </div>
    );
  }

  return (
    <div className="panel flex items-center gap-4 px-4 py-3 mb-md">
      {stopCount > 0 && (
        <div className="flex items-center gap-2">
          <span className="status-dot bg-danger animate-pulse-dot" />
          <span className="font-mono text-2xl text-danger">{stopCount}</span>
          <span className="text-2xs uppercase tracking-wider text-danger/70">СТОП</span>
        </div>
      )}
      {warnCount > 0 && (
        <div className="flex items-center gap-2">
          <span className="status-dot bg-warning animate-pulse-dot" />
          <span className="font-mono text-2xl text-warning">{warnCount}</span>
          <span className="text-2xs uppercase tracking-wider text-warning/70">WARNING</span>
        </div>
      )}
      {earlyCount > 0 && (
        <div className="flex items-center gap-2">
          <span className="status-dot bg-early" />
          <span className="font-mono text-xl text-early">{earlyCount}</span>
          <span className="text-2xs uppercase tracking-wider text-early/70">РАННИЙ</span>
        </div>
      )}
    </div>
  );
}

/** Полоса статуса сканирования */
function ScanStatusBar({ settings, onToggle, onScanNow, scanning, lastScanAt, observerStatus, observerStatusMessage }) {
  const [secsLeft, setSecsLeft] = useState(null);

  useEffect(() => {
    if (!lastScanAt || !settings?.interval_seconds) { setSecsLeft(null); return; }
    const nextScanAt = new Date(lastScanAt).getTime() + settings.interval_seconds * 1000;
    const tick = () => setSecsLeft(Math.max(0, Math.round((nextScanAt - Date.now()) / 1000)));
    tick();
    const id = setInterval(tick, 1000);
    return () => clearInterval(id);
  }, [lastScanAt, settings?.interval_seconds]);

  const isEnabled = settings?.is_scanning_enabled ?? false;
  const isRunning = observerStatus === 'RUNNING' || scanning;

  let statusText = '';
  let statusColor = 'text-muted';
  let showDot = false;
  if (!isEnabled) {
    statusText = 'Выключено';
  } else if (observerStatus === 'WAITING_BROWSER') {
    statusText = 'Браузер занят';
    statusColor = 'text-warning';
  } else if (observerStatus === 'DISABLING') {
    statusText = 'Отключаем объявления…';
    statusColor = 'text-warning';
    showDot = true;
  } else if (observerStatus === 'PAUSED') {
    statusText = observerStatusMessage ?? 'Пауза';
    statusColor = 'text-warning';
  } else if (isRunning) {
    statusText = 'Сканирую…';
    statusColor = 'text-success';
    showDot = true;
  } else if (isEnabled) {
    statusText = 'Ожидание';
    statusColor = 'text-secondary';
  }

  const showCountdown = isEnabled && !isRunning && secsLeft !== null && secsLeft > 0;
  const showLastScan = isEnabled && !isRunning && !showCountdown && lastScanAt;

  return (
    <div className="panel flex items-center gap-3 px-4 py-2.5 mb-md flex-wrap">
      {/* Тогл сканирования */}
      <button
        onClick={onToggle}
        className="toggle-track"
        data-active={isEnabled}
        role="switch"
        aria-checked={isEnabled}
        aria-label={isEnabled ? 'Выключить сканирование' : 'Включить сканирование'}
      >
        <span className="toggle-knob" data-active={isEnabled} />
      </button>

      <span className="text-2xs font-bold uppercase tracking-widest text-secondary">
        Скан
      </span>

      {statusText && (
        <span className={`flex items-center gap-1.5 text-2xs font-medium ${statusColor}`}>
          {showDot && <span className="status-dot bg-success animate-pulse-dot" />}
          {statusText}
        </span>
      )}

      {showCountdown && (
        <span className="font-mono text-sm font-semibold text-secondary">
          {Math.floor(secsLeft / 60)}:{String(secsLeft % 60).padStart(2, '0')}
        </span>
      )}
      {showLastScan && (
        <span className="font-mono text-2xs text-muted">
          {new Date(lastScanAt).toLocaleTimeString('ru-RU')}
        </span>
      )}

      <button
        className="btn-ghost ml-auto flex items-center gap-1.5"
        onClick={onScanNow}
        disabled={scanning}
      >
        <span className={scanning ? 'animate-spin' : ''}>↻</span>
        {scanning ? 'Сканирую' : 'Обновить'}
      </button>
    </div>
  );
}

/** KPI-полоса */
function HeroKPIStrip({ performance, performanceYesterday }) {
  const s = performance?.summary;
  const y = performanceYesterday?.summary;
  const fmt$ = (v) => (v != null ? `$${Number(v).toFixed(2)}` : '—');
  const fmtN = (v) => (v != null ? String(v) : '—');
  const fmtPct = (v) => (v != null ? `${Number(v).toFixed(1)}%` : '—');
  const fmtRoas = (v) => (v != null && Number(v) > 0 ? `${Number(v).toFixed(2)}x` : '—');

  const hasDeposits = Number(s?.deposits ?? 0) > 0;
  const hasSpend = Number(s?.spend ?? 0) > 0;
  const roasVal = Number(s?.roas ?? 0);

  const kpis = [
    { label: 'Расход', value: fmt$(s?.spend), key: 'spend', color: 'text-accent' },
    { label: 'Лиды', value: fmtN(s?.leads), key: 'leads', color: Number(s?.leads ?? 0) > 0 ? 'text-accent' : 'text-muted' },
    { label: 'Реги', value: fmtN(s?.registrations), key: 'registrations', color: 'text-primary' },
    { label: 'Депозиты', value: fmtN(s?.deposits), key: 'deposits', color: hasDeposits ? 'text-success' : hasSpend ? 'text-danger' : 'text-muted' },
    { label: 'Рег→Деп', value: fmtPct(s?.reg_to_dep_rate), key: 'reg_to_dep_rate', color: 'text-primary' },
    { label: 'ROAS', value: fmtRoas(s?.roas), key: 'roas', color: roasVal >= 3 ? 'text-success' : roasVal >= 1 ? 'text-warning' : roasVal > 0 ? 'text-danger' : 'text-muted' },
  ];

  return (
    <div className="grid grid-cols-2 sm:grid-cols-3 lg:grid-cols-6 gap-3 mb-md">
      {kpis.map((kpi) => (
        <div key={kpi.label} className="kpi-card group hover:border-border-hover transition-all">
          <span className="kpi-label">{kpi.label}</span>
          <span className={`kpi-value ${kpi.color}`}>{kpi.value}</span>
          <Delta today={s?.[kpi.key]} yesterday={y?.[kpi.key]} />
        </div>
      ))}
    </div>
  );
}

/* === Основная страница === */

export default function DashboardPage({ onNavigate }) {
  const [stats, setStats] = useState(null);
  const [incidents, setIncidents] = useState([]);
  const [disableTasks, setDisableTasks] = useState([]);
  const [settings, setSettings] = useState(null);
  const [performance, setPerformance] = useState(null);
  const [error, setError] = useState(null);
  const [toggling, setToggling] = useState(false);
  const [scanning, setScanning] = useState(false);

  const [enableTasks, setEnableTasks] = useState([]);
  const [enableRecs, setEnableRecs] = useState([]);
  const [chartData, setChartData] = useState(null);
  const [spendHistory, setSpendHistory] = useState([]);
  const [performanceYesterday, setPerformanceYesterday] = useState(null);

  const loadData = useCallback(async () => {
    try {
      const [statsRes, incidentsRes, tasksRes, settingsRes, perfRes, perfYestRes, enableTasksRes, enableRecsRes, chartRes, spendRes] = await Promise.all([
        getDashboardStats(),
        getDashboardIncidents({ limit: 50 }).catch(() => []),
        getDisableTasks({ limit: 50 }),
        getObserverSettings(),
        getDashboardPerformance({ period: 'today' }),
        getDashboardPerformance({ period: 'yesterday' }).catch(() => null),
        getEnableTasks({ limit: 20 }).catch(() => []),
        getEnableRecommendations({ limit: 10 }).catch(() => []),
        getChartData({ period: 'today' }).catch(() => null),
        getSpendHistory({ hours: 24 }).catch(() => []),
      ]);
      setStats(statsRes);
      setIncidents(normalizeIncidentList(incidentsRes));
      setDisableTasks(tasksRes);
      setSettings(settingsRes);
      setPerformance(perfRes);
      setPerformanceYesterday(perfYestRes);
      setEnableTasks(enableTasksRes);
      setEnableRecs(enableRecsRes);
      setChartData(chartRes);
      setSpendHistory(spendRes);
      setError(null);
    } catch (e) {
      setError(e.message);
    }
  }, []);

  const loadRealtimeStatus = useCallback(async () => {
    try {
      const [tasksRes, statsRes, incidentsRes] = await Promise.all([
        getDisableTasks({ limit: 50 }),
        getDashboardStats(),
        getDashboardIncidents({ limit: 50 }).catch(() => []),
      ]);
      setDisableTasks(tasksRes);
      setStats(statsRes);
      setIncidents(normalizeIncidentList(incidentsRes));
    } catch (_) {
      /* тихо игнорируем ошибки realtime-поллинга */
    }
  }, []);

  useEffect(() => {
    void loadData();
  }, [loadData]);

  useAsyncPolling(async () => { await loadData(); }, { enabled: true, intervalMs: 30000 });
  useAsyncPolling(async () => { await loadRealtimeStatus(); }, { enabled: true, intervalMs: 5000 });
  useRefreshOnResume(() => { void loadData(); });

  const handleToggle = async () => {
    if (toggling || !settings) return;
    setToggling(true);
    try {
      await toggleScanning(!settings.is_scanning_enabled);
      setSettings((cur) => ({ ...cur, is_scanning_enabled: !cur.is_scanning_enabled }));
    } finally {
      setToggling(false);
    }
  };

  const handleScanNow = async () => {
    if (scanning) return;
    setScanning(true);
    const scanStartedAt = stats?.last_scan_at ?? null;
    try {
      await triggerScanNow();
      const deadline = Date.now() + 120_000;
      let timerId = null;
      const poll = async () => {
        if (Date.now() > deadline) { setScanning(false); return; }
        try {
          const fresh = await getDashboardStats();
          if (fresh?.last_scan_at && fresh.last_scan_at !== scanStartedAt) {
            setStats(fresh);
            setScanning(false);
            return;
          }
        } catch (_) {
          /* ожидаем */
        }
        timerId = setTimeout(poll, 4000);
      };
      timerId = setTimeout(poll, 4000);
      // Cleanup при размонтировании — сохраняем ссылку для очистки
      return () => { if (timerId) clearTimeout(timerId); };
    } catch (e) {
      setScanning(false);
      setError(`Ошибка запуска скана: ${e.message}`);
    }
  };

  const handleDisable = async (fbAdId) => {
    try {
      await createDisableTask(fbAdId);
      const tasksRes = await getDisableTasks({ limit: 50 });
      setDisableTasks(tasksRes);
    } catch (e) {
      setError(`Ошибка отключения: ${e.message}`);
    }
  };

  const handleEnableTask = async (recId) => {
    try {
      await createEnableTaskFromRecommendation(recId);
      const [recsRes, enableTasksRes] = await Promise.all([
        getEnableRecommendations({ limit: 10 }),
        getEnableTasks({ limit: 20 }),
      ]);
      setEnableRecs(recsRes);
      setEnableTasks(enableTasksRes);
    } catch (e) {
      setError(`Ошибка включения: ${e.message}`);
    }
  };

  const handleRetry = async (taskId) => {
    try {
      await retryDisableTask(taskId);
      const tasksRes = await getDisableTasks({ limit: 50 });
      setDisableTasks(tasksRes);
    } catch (e) {
      setError(`Ошибка повтора: ${e.message}`);
    }
  };

  const activeIncidents = incidents.filter((i) => i.current_state !== 'NORMAL');

  return (
    <div className="space-y-md">
      {/* Ошибка */}
      {error && (
        <div className="rounded-md bg-danger-muted border border-danger/30 px-4 py-3 text-sm text-danger">
          {error}
          <button onClick={() => setError(null)} className="ml-3 text-danger/60 hover:text-danger">✕</button>
        </div>
      )}

      {/* 1. Hero-баннер алертов — ПЕРВОЕ, что видит медиабаер */}
      <AlertBanner stats={stats} />

      {/* 2. Статус сканирования */}
      <ScanStatusBar
        settings={settings}
        onToggle={handleToggle}
        onScanNow={handleScanNow}
        scanning={scanning}
        lastScanAt={stats?.last_scan_at}
        observerStatus={stats?.observer_status}
        observerStatusMessage={stats?.observer_status_message}
      />

      {/* 3. KPI-полоса */}
      <HeroKPIStrip performance={performance} performanceYesterday={performanceYesterday} />

      {/* 4. Двухколоночная компоновка: лево 65% / право 35% */}
      <div className="grid grid-cols-1 lg:grid-cols-[1fr_0.54fr] gap-md items-start">

        {/* === ЛЕВАЯ КОЛОНКА === */}
        <div className="space-y-md min-w-0">

          {/* Лента инцидентов */}
          <div className="panel overflow-hidden">
            <AlertTray
              incidents={activeIncidents}
              disableTasks={disableTasks}
              onSelectIncident={() => {}}
              onDisable={handleDisable}
              settings={settings}
              lastScanAt={stats?.last_scan_at}
              onEnableScanning={handleToggle}
            />
          </div>

          {/* Чарт: расход + алерты */}
          <div className="panel p-4">
            <SpendAlertsChart
              spendData={performance?.timeline ?? []}
              alertsData={chartData?.alerts_by_hour ?? []}
            />
          </div>

          {/* CPR по кампаниям */}
          <div className="panel p-4">
            <CampaignComparativeBars data={performance?.campaigns ?? []} />
          </div>

          {/* Бюджет + Кампании */}
          <div className="grid grid-cols-1 xl:grid-cols-2 gap-md">
            <div className="panel p-4">
              <BudgetOverrunChart data={chartData?.campaign_budget_deltas ?? []} />
            </div>
            <div className="panel p-4">
              <CampaignBreakdownTable data={performance?.campaigns ?? []} />
            </div>
          </div>
        </div>

        {/* === ПРАВАЯ КОЛОНКА === */}
        <div className="space-y-md min-w-0">

          {/* Очередь задач */}
          <div className="panel">
            <TaskQueuePanel
              disableTasks={disableTasks}
              enableTasks={enableTasks}
              enableRecs={enableRecs}
              onRetryDisable={handleRetry}
              onCreateEnableTask={handleEnableTask}
            />
          </div>

          {/* Scorecard состояний */}
          <div className="panel p-4">
            <CampaignScorecard
              stats={stats}
              performance={performance}
              spendHistory={spendHistory}
              onStateClick={(state) => onNavigate?.(`/ads?state=${state}`)}
            />
          </div>

          {/* Нарушения правил */}
          <div className="panel p-4">
            <RuleViolationRanking data={chartData?.rule_violations ?? []} />
          </div>

          {/* Воронка */}
          <div className="panel p-4">
            <FunnelChart funnel={performance?.funnel ?? []} />
          </div>

          {/* Топ объявления */}
          <div className="panel p-4">
            <TopAdsQualityTable data={chartData?.top_ads_by_spend ?? []} />
          </div>
        </div>
      </div>

    </div>
  );
}
