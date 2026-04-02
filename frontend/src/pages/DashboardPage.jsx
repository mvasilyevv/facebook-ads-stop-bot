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
import { DrawerPanel } from '../components/DrawerPanel.jsx';
import { TaskQueuePanel } from '../components/TaskQueuePanel.jsx';
import { BudgetOverrunChart } from '../components/BudgetOverrunChart.jsx';
import { CampaignBreakdownTable } from '../components/CampaignBreakdownTable.jsx';
import { RuleViolationRanking } from '../components/RuleViolationRanking.jsx';
import { TopAdsQualityTable } from '../components/TopAdsQualityTable.jsx';
import { CampaignComparativeBars } from '../components/CampaignComparativeBars.jsx';
import { SpendAlertsChart } from '../components/SpendAlertsChart.jsx';

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

function Delta({ today, yesterday, fmt = (v) => String(v), lowerIsBetter = false }) {
  if (today == null || yesterday == null) return null;
  const a = Number(today);
  const b = Number(yesterday);
  if (b === 0) return null;
  const diff = a - b;
  const pct = Math.abs((diff / b) * 100).toFixed(0);
  if (pct === '0') return null;
  const up = diff > 0;
  const good = lowerIsBetter ? !up : up;
  const color = good ? 'var(--accent-emerald)' : 'var(--accent-crimson)';
  return (
    <div style={{ fontSize: '10px', fontWeight: 600, color, marginTop: '4px', fontFamily: 'JetBrains Mono, monospace' }}>
      {up ? '↑' : '↓'} {pct}% к вчера
    </div>
  );
}

function HeroKPIStrip({ performance, performanceYesterday }) {
  const s = performance?.summary;
  const y = performanceYesterday?.summary;
  const fmt$ = (v) => (v != null ? `$${Number(v).toFixed(2)}` : '—');
  const fmtN = (v) => (v != null ? String(v) : '—');
  const fmtPct = (v) => (v != null ? `${Number(v).toFixed(1)}%` : '—');
  const fmtRoas = (v) => (v != null && Number(v) > 0 ? `${Number(v).toFixed(2)}x` : '—');

  const hasDeposits = Number(s?.deposits ?? 0) > 0;
  const hasSpend = Number(s?.spend ?? 0) > 0;
  const depositsColor = hasDeposits ? 'var(--accent-emerald)' : hasSpend ? 'var(--accent-crimson)' : 'var(--text-muted)';
  const roasVal = Number(s?.roas ?? 0);
  const roasColor = roasVal >= 3 ? 'var(--accent-emerald)' : roasVal >= 1 ? 'var(--accent-gold)' : roasVal > 0 ? 'var(--accent-crimson)' : 'var(--text-muted)';

  const kpis = [
    { label: 'Расход', value: fmt$(s?.spend), color: 'var(--accent-teal)', deltaKey: 'spend', lowerIsBetter: false },
    { label: 'Лиды', value: fmtN(s?.leads), color: Number(s?.leads ?? 0) > 0 ? 'var(--accent-teal)' : 'var(--text-muted)', deltaKey: 'leads', lowerIsBetter: false },
    { label: 'Реги', value: fmtN(s?.registrations), color: 'var(--text-primary)', deltaKey: 'registrations', lowerIsBetter: false },
    { label: 'Депозиты', value: fmtN(s?.deposits), color: depositsColor, deltaKey: 'deposits', lowerIsBetter: false },
    { label: 'Рег→Деп', value: fmtPct(s?.reg_to_dep_rate), color: 'var(--text-primary)', deltaKey: 'reg_to_dep_rate', lowerIsBetter: false },
    { label: 'ROAS', value: fmtRoas(s?.roas), color: roasColor, deltaKey: 'roas', lowerIsBetter: false },
  ];

  return (
    <div style={{
      display: 'grid',
      gridTemplateColumns: 'repeat(auto-fit, minmax(130px, 1fr))',
      gap: '12px',
      marginBottom: '16px',
    }}>
      {kpis.map((kpi) => (
        <div key={kpi.label} style={{
          background: 'var(--bg-card)',
          border: '1px solid var(--border-color)',
          borderRadius: 'var(--radius-md)',
          padding: '16px 18px',
          boxShadow: 'var(--shadow-sm)',
          transition: 'all 0.2s ease',
        }}
          onMouseEnter={e => { e.currentTarget.style.transform = 'translateY(-1px)'; e.currentTarget.style.boxShadow = 'var(--shadow-md)'; }}
          onMouseLeave={e => { e.currentTarget.style.transform = ''; e.currentTarget.style.boxShadow = 'var(--shadow-sm)'; }}
        >
          <div style={{ fontSize: '10px', fontWeight: 600, textTransform: 'uppercase', letterSpacing: '0.1em', color: 'var(--text-muted)', marginBottom: '8px' }}>
            {kpi.label}
          </div>
          <div style={{ fontSize: '22px', fontWeight: 700, color: kpi.color, fontFamily: 'Syne, sans-serif', fontVariantNumeric: 'tabular-nums', letterSpacing: '-0.01em' }}>
            {kpi.value}
          </div>
          <Delta today={s?.[kpi.deltaKey]} yesterday={y?.[kpi.deltaKey]} lowerIsBetter={kpi.lowerIsBetter} />
        </div>
      ))}
    </div>
  );
}

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

  let pillVariant = 'paused';
  let pillDot = false;
  let pillText = '';
  if (!isEnabled) {
    pillText = 'Выключено';
  } else if (observerStatus === 'WAITING_BROWSER') {
    pillVariant = 'waiting';
    pillText = 'Браузер занят';
  } else if (observerStatus === 'PAUSED') {
    pillText = observerStatusMessage ?? 'Пауза';
  } else if (isRunning) {
    pillVariant = 'active';
    pillDot = true;
    pillText = 'Сканирую...';
  }

  const showCountdown = isEnabled && !isRunning && secsLeft !== null && secsLeft > 0;
  const showLastScan = isEnabled && !isRunning && !showCountdown && lastScanAt;

  return (
    <div className="glass-panel" style={{ padding: '10px 18px', marginBottom: '16px', display: 'flex', alignItems: 'center', gap: '14px', flexWrap: 'wrap' }}>
      <label className="toggle-pill" title={isEnabled ? 'Выключить сканирование' : 'Включить сканирование'}>
        <input type="checkbox" checked={isEnabled} onChange={onToggle} />
        <span className="toggle-pill__track">
          <span className="toggle-pill__thumb" />
        </span>
      </label>

      <span style={{ fontSize: '11px', fontWeight: 700, color: 'var(--text-secondary)', letterSpacing: '0.08em', textTransform: 'uppercase' }}>
        Сканирование
      </span>

      {pillText && (
        <span className={`status-pill status-pill--${pillVariant}`}>
          {pillDot && <span className="status-pill__dot status-pill__dot--pulse" />}
          {pillText}
        </span>
      )}

      {showCountdown && (
        <span style={{ fontSize: '11px', fontFamily: "'JetBrains Mono', monospace", fontVariantNumeric: 'tabular-nums', color: 'var(--text-muted)' }}>
          через {secsLeft}с
        </span>
      )}
      {showLastScan && (
        <span style={{ fontSize: '11px', fontFamily: "'JetBrains Mono', monospace", fontVariantNumeric: 'tabular-nums', color: 'var(--text-muted)' }}>
          {new Date(lastScanAt).toLocaleTimeString('ru-RU')}
        </span>
      )}

      <button
        className="scan-refresh-btn"
        onClick={onScanNow}
        disabled={scanning}
        title="Принудительно запустить скан"
        style={{ marginLeft: 'auto' }}
      >
        <span className={`scan-refresh-btn__icon${scanning ? ' scan-refresh-btn__icon--spin' : ''}`}>↻</span>
        {scanning ? 'Сканирую' : 'Обновить'}
      </button>
    </div>
  );
}

function DenseAdTable({ incidents }) {
  return (
    <div style={{ marginTop: '16px', background: 'var(--bg-card)', border: '1px solid var(--border-color)', borderRadius: 'var(--radius-md)', overflow: 'hidden', boxShadow: 'var(--shadow-sm)' }}>
      <div style={{ padding: '12px 16px', borderBottom: '1px solid var(--border-color)' }}>
        <h3 style={{ margin: 0, fontSize: '13px', fontWeight: 700, textTransform: 'uppercase', color: 'var(--text-muted)', letterSpacing: '0.06em' }}>
          Активные инциденты
        </h3>
      </div>
      <div style={{ overflowX: 'auto' }}>
        <table style={{ width: '100%', fontSize: '12px', borderCollapse: 'collapse' }}>
          <thead>
            <tr style={{ background: 'var(--bg-raised)', borderBottom: '1px solid var(--border-color)' }}>
              <th style={{ padding: '8px 12px', textAlign: 'left', fontWeight: 600, fontSize: '11px', textTransform: 'uppercase', color: 'var(--text-muted)' }}>Объявление</th>
              <th style={{ padding: '8px 12px', textAlign: 'left', fontWeight: 600, fontSize: '11px', textTransform: 'uppercase', color: 'var(--text-muted)' }}>Кампания</th>
              <th style={{ padding: '8px 12px', textAlign: 'left', fontWeight: 600, fontSize: '11px', textTransform: 'uppercase', color: 'var(--text-muted)' }}>Статус</th>
            </tr>
          </thead>
          <tbody>
            {incidents.slice(0, 10).map((incident) => (
              <tr key={incident.fb_ad_id} style={{ borderBottom: '1px solid var(--border-dim)' }}>
                <td style={{ padding: '9px 12px', color: 'var(--text-primary)' }}>{incident.ad_name}</td>
                <td style={{ padding: '9px 12px', color: 'var(--text-secondary)', fontSize: '11px' }}>{incident.campaign_name}</td>
                <td style={{ padding: '9px 12px' }}>
                  <span className={`status-pill status-pill--${{
                    STOP_SENT: 'stop', WARNING_SENT: 'warning', EARLY_SIGNAL_SENT: 'signal', CLAIMED: 'paused',
                  }[incident.current_state] || 'paused'}`}>
                    {ALERT_STATE_LABELS[incident.current_state] || incident.current_state}
                  </span>
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </div>
  );
}

export default function DashboardPage({ onNavigate }) {
  const [stats, setStats] = useState(null);
  const [incidents, setIncidents] = useState([]);
  const [disableTasks, setDisableTasks] = useState([]);
  const [settings, setSettings] = useState(null);
  const [performance, setPerformance] = useState(null);
  const [error, setError] = useState(null);
  const [toggling, setToggling] = useState(false);
  const [scanning, setScanning] = useState(false);
  const [selectedIncident, setSelectedIncident] = useState(null);
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
    } catch (_) {}
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
      setSettings((current) => ({ ...current, is_scanning_enabled: !current.is_scanning_enabled }));
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
      // Ждём реального завершения скана: last_scan_at должен обновиться
      const deadline = Date.now() + 120_000;
      const poll = async () => {
        if (Date.now() > deadline) { setScanning(false); return; }
        try {
          const fresh = await getDashboardStats();
          if (fresh?.last_scan_at && fresh.last_scan_at !== scanStartedAt) {
            setStats(fresh);
            setScanning(false);
            return;
          }
        } catch (_) {}
        setTimeout(poll, 4000);
      };
      setTimeout(poll, 4000);
    } catch (e) {
      setScanning(false);
      setError(`Ошибка запуска скана: ${e.message}`);
    }
  };

  const handleRestart = async () => {
    try {
      await restartObserver();
      setTimeout(loadData, 5000);
    } catch (e) {
      setError(`Не удалось перезапустить: ${e.message}`);
    }
  };

  const handleDisable = async (fbAdId, adName) => {
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
  const normalCount = (stats?.total_ads_monitored ?? 0) - (stats?.ads_in_early_signal ?? 0) - (stats?.ads_in_warning ?? 0) - (stats?.ads_in_stop ?? 0) - (stats?.ads_claimed ?? 0) - (stats?.ads_disabled ?? 0);

  return (
    <div className="dashboard-page" style={{ padding: '16px' }}>
      {error && (
        <div style={{ background: 'var(--accent-crimson)', color: 'white', padding: '12px', marginBottom: '16px', borderRadius: '4px' }}>
          ⚠ {error}
        </div>
      )}

      <ScanStatusBar
        settings={settings}
        onToggle={handleToggle}
        onScanNow={handleScanNow}
        scanning={scanning}
        lastScanAt={stats?.last_scan_at}
        observerStatus={stats?.observer_status}
        observerStatusMessage={stats?.observer_status_message}
      />

      {/* KPI Hero Strip */}
      <HeroKPIStrip performance={performance} performanceYesterday={performanceYesterday} />

      {/* Основная секция: AlertTray (2/3) + TaskQueuePanel (1/3) */}
      <div className="main-content-row" style={{ display: 'flex', gap: '16px', alignItems: 'flex-start', marginBottom: '16px' }}>
        {/* AlertTray — 2/3 ширины */}
        <div className="alert-tray-col" style={{ flex: 2, minWidth: 0, background: 'var(--bg-card)', border: '1px solid var(--border-color)', borderRadius: 'var(--radius-md)', boxShadow: 'var(--shadow-sm)', overflow: 'hidden' }}>
          <AlertTray
            incidents={activeIncidents}
            disableTasks={disableTasks}
            onSelectIncident={setSelectedIncident}
            onDisable={handleDisable}
            settings={settings}
            lastScanAt={stats?.last_scan_at}
            onEnableScanning={handleToggle}
          />
        </div>

        {/* TaskQueuePanel — 1/3 ширины */}
        <div className="task-queue-col" style={{ flex: 1, minWidth: 0, background: 'var(--bg-card)', border: '1px solid var(--border-color)', borderRadius: 'var(--radius-md)', boxShadow: 'var(--shadow-sm)' }}>
          <TaskQueuePanel
            disableTasks={disableTasks}
            enableTasks={enableTasks}
            enableRecs={enableRecs}
            onRetryDisable={handleRetry}
            onCreateEnableTask={handleEnableTask}
          />
        </div>
      </div>

      {/* Нижняя секция: CampaignScorecard + графики в полную ширину */}
      <div style={{ marginBottom: '16px' }}>
        <CampaignScorecard
          stats={stats}
          performance={performance}
          spendHistory={spendHistory}
          onStateClick={(state) => onNavigate?.(`/ads?state=${state}`)}
        />
      </div>

      {/* Объединённый чарт: расход + алерты по часам */}
      {(performance?.timeline?.length > 0 || chartData?.alerts_by_hour?.length > 0) && (
        <SpendAlertsChart
          spendData={performance?.timeline ?? []}
          alertsData={chartData?.alerts_by_hour ?? []}
        />
      )}

      {/* Нарушения правил */}
      {chartData?.rule_violations?.length > 0 && (
        <div style={{ marginBottom: '16px' }}>
          <RuleViolationRanking data={chartData.rule_violations} />
        </div>
      )}

      {/* Воронка конверсий */}
      {performance?.funnel?.length > 0 && (
        <div style={{ marginBottom: '16px' }}>
          <FunnelChart funnel={performance.funnel} />
        </div>
      )}

      {/* Сравнение кампаний: расход vs депозиты */}
      {performance?.campaigns?.length > 0 && (
        <div style={{ marginBottom: '16px' }}>
          <CampaignComparativeBars data={performance.campaigns} />
        </div>
      )}

      {/* BudgetOverrunChart + CampaignBreakdownTable в 2 колонки */}
      {(chartData?.campaign_budget_deltas?.length > 0 || performance?.campaigns?.length > 0) && (
        <div style={{ display: 'grid', gridTemplateColumns: chartData?.campaign_budget_deltas?.length > 0 && performance?.campaigns?.length > 0 ? '1fr 1fr' : '1fr', gap: '16px', marginBottom: '16px' }}>
          {chartData?.campaign_budget_deltas?.length > 0 && (
            <BudgetOverrunChart data={chartData.campaign_budget_deltas} />
          )}
          {performance?.campaigns?.length > 0 && (
            <CampaignBreakdownTable data={performance.campaigns} />
          )}
        </div>
      )}

      {/* Топ объявления по расходу */}
      <TopAdsQualityTable data={chartData?.top_ads_by_spend ?? []} />

      {/* DrawerPanel для деталей инцидента */}
      {selectedIncident && (
        <DrawerPanel
          ad={{
            fb_ad_id: selectedIncident.fb_ad_id,
            name: selectedIncident.ad_name,
            state: selectedIncident.current_state,
            campaign_name: selectedIncident.campaign_name,
          }}
          incident={selectedIncident}
          disableTask={null}
          onClose={() => setSelectedIncident(null)}
          onDisable={handleDisable}
          onRetry={handleRetry}
        />
      )}
    </div>
  );
}

function StatItem({ label, value, color = 'var(--text-primary)' }) {
  return (
    <div style={{ display: 'flex', justifyContent: 'space-between', fontSize: '12px', padding: '6px', borderBottom: '1px solid var(--border-dim)' }}>
      <span style={{ color: 'var(--text-muted)' }}>{label}</span>
      <span style={{ fontWeight: 600, color }}>{value}</span>
    </div>
  );
}
