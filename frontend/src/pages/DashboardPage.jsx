import { useCallback, useEffect, useMemo, useState } from 'react';
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
import { SpendTimelineChart } from '../components/SpendTimelineChart.jsx';
import { CampaignBreakdownTable } from '../components/CampaignBreakdownTable.jsx';
import { AlertVolumeTrendline } from '../components/AlertVolumeTrendline.jsx';
import { RuleViolationRanking } from '../components/RuleViolationRanking.jsx';
import { SpendPacingBar } from '../components/SpendPacingBar.jsx';
import { TopAdsQualityTable } from '../components/TopAdsQualityTable.jsx';

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

function HeroKPIStrip({ performance }) {
  const s = performance?.summary;
  const fmt$ = (v) => (v != null ? `$${Number(v).toFixed(2)}` : '—');
  const fmtN = (v) => (v != null ? String(v) : '—');
  const fmtPct = (v) => (v != null ? `${(Number(v) * 100).toFixed(1)}%` : '—');

  const hasDeposits = Number(s?.deposits ?? 0) > 0;
  const hasSpend = Number(s?.spend ?? 0) > 0;
  const depositsColor = hasDeposits ? '#059669' : hasSpend ? '#ef4444' : '#94a3b8';

  const kpis = [
    { label: 'Расход', value: fmt$(s?.spend), color: '#0ea5e9' },
    { label: 'Лиды', value: fmtN(s?.leads), color: Number(s?.leads ?? 0) > 0 ? '#0ea5e9' : '#94a3b8' },
    { label: 'Реги', value: fmtN(s?.registrations), color: '#0f172a' },
    { label: 'Депозиты', value: fmtN(s?.deposits), color: depositsColor },
    { label: 'CPR', value: fmt$(s?.cpr), color: '#0f172a' },
    { label: 'Рег→Деп', value: fmtPct(s?.reg_to_dep_rate), color: '#0f172a' },
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
          background: '#ffffff',
          border: '1px solid #e2e8f0',
          borderRadius: '6px',
          padding: '14px 16px',
          boxShadow: '0 1px 3px rgba(0,0,0,0.07)',
        }}>
          <div style={{ fontSize: '10px', fontWeight: 600, textTransform: 'uppercase', letterSpacing: '0.1em', color: '#94a3b8', marginBottom: '6px' }}>
            {kpi.label}
          </div>
          <div style={{ fontSize: '20px', fontWeight: 700, color: kpi.color, fontFamily: 'JetBrains Mono, monospace', fontVariantNumeric: 'tabular-nums' }}>
            {kpi.value}
          </div>
        </div>
      ))}
    </div>
  );
}

function ScanStatusBar({ settings, onToggle, onScanNow, scanning, lastScanAt }) {
  return (
    <div style={{ background: 'var(--bg-card)', border: '1px solid var(--border-color)', padding: '12px 16px', marginBottom: '16px', borderRadius: '6px', display: 'flex', alignItems: 'center', gap: '12px', boxShadow: 'var(--shadow-sm)' }}>
      <span style={{ fontSize: '13px', fontWeight: 600 }}>Сканирование</span>
      <button
        onClick={onToggle}
        style={{
          padding: '4px 12px',
          fontSize: '12px',
          borderRadius: '3px',
          border: 'none',
          cursor: 'pointer',
          backgroundColor: settings?.is_scanning_enabled ? 'var(--accent-teal)' : 'var(--bg-tertiary)',
          color: settings?.is_scanning_enabled ? 'white' : 'var(--text-primary)',
        }}
      >
        {settings?.is_scanning_enabled ? '✓ Включено' : '○ Выключено'}
      </button>
      <button
        onClick={onScanNow}
        disabled={scanning}
        style={{
          padding: '4px 12px',
          fontSize: '12px',
          borderRadius: '3px',
          border: '1px solid var(--border-color)',
          backgroundColor: 'transparent',
          cursor: scanning ? 'not-allowed' : 'pointer',
          opacity: scanning ? 0.5 : 1,
        }}
      >
        {scanning ? 'Сканирование...' : 'Обновить'}
      </button>
      {lastScanAt && (
        <span style={{ marginLeft: 'auto', fontSize: '11px', color: 'var(--text-muted)' }}>
          Последний скан: {new Date(lastScanAt).toLocaleTimeString('ru-RU')}
        </span>
      )}
    </div>
  );
}

function DenseAdTable({ incidents }) {
  return (
    <div style={{ marginTop: '16px', background: 'var(--bg-card)', border: '1px solid var(--border-color)', borderRadius: '6px', overflow: 'hidden', boxShadow: 'var(--shadow-sm)' }}>
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
                  <span style={{
                    display: 'inline-block',
                    padding: '2px 7px',
                    borderRadius: '3px',
                    fontSize: '10px',
                    fontWeight: 600,
                    background: incident.current_state === 'STOP_SENT' ? 'var(--accent-crimson-dim)' :
                                incident.current_state === 'WARNING_SENT' ? 'var(--accent-gold-dim)' :
                                incident.current_state === 'EARLY_SIGNAL_SENT' ? 'var(--accent-orchid-dim)' :
                                'var(--bg-raised)',
                    color: incident.current_state === 'STOP_SENT' ? 'var(--accent-crimson)' :
                           incident.current_state === 'WARNING_SENT' ? 'var(--accent-gold)' :
                           incident.current_state === 'EARLY_SIGNAL_SENT' ? 'var(--accent-orchid)' :
                           'var(--text-muted)',
                  }}>
                    {incident.current_state}
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

  const loadData = useCallback(async () => {
    try {
      const [statsRes, incidentsRes, tasksRes, settingsRes, perfRes, enableTasksRes, enableRecsRes, chartRes, spendRes] = await Promise.all([
        getDashboardStats(),
        getDashboardIncidents({ limit: 50 }).catch(() => []),
        getDisableTasks({ limit: 50 }),
        getObserverSettings(),
        getDashboardPerformance({ period: 'today' }),
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
    try {
      await triggerScanNow();
    } finally {
      setTimeout(() => setScanning(false), 3000);
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
      />

      {/* KPI Hero Strip */}
      <HeroKPIStrip performance={performance} />

      {/* Темп расхода */}
      <SpendPacingBar performance={performance} />

      {/* 3-колонная сетка — главный экран мониторинга */}
      <div className="dashboard-grid" style={{ display: 'grid', gridTemplateColumns: '1fr 2fr 1fr', gap: '16px', marginBottom: '16px' }}>
        {/* Левая колонна: CampaignScorecard */}
        <div className="dashboard-grid__left">
          <CampaignScorecard
            stats={stats}
            performance={performance}
            spendHistory={spendHistory}
            onStateClick={(state) => onNavigate?.(`/ads?state=${state}`)}
          />
        </div>

        {/* Центральная колонна: AlertTray */}
        <div className="dashboard-grid__center" style={{ background: 'var(--bg-card)', border: '1px solid var(--border-color)', borderRadius: '6px', boxShadow: 'var(--shadow-sm)', overflow: 'hidden' }}>
          <AlertTray
            incidents={activeIncidents}
            disableTasks={disableTasks}
            onSelectIncident={setSelectedIncident}
            onDisable={handleDisable}
          />
        </div>

        {/* Правая колонна: TaskQueuePanel */}
        <div className="dashboard-grid__right" style={{ background: 'var(--bg-card)', border: '1px solid var(--border-color)', borderRadius: '6px', boxShadow: 'var(--shadow-sm)' }}>
          <TaskQueuePanel
            disableTasks={disableTasks}
            enableTasks={enableTasks}
            enableRecs={enableRecs}
            onRetryDisable={handleRetry}
            onCreateEnableTask={handleEnableTask}
          />
        </div>
      </div>

      {/* Алерты по часам + Нарушения правил */}
      {(chartData?.alerts_by_hour?.length > 0 || chartData?.rule_violations?.length > 0) && (
        <div style={{
          display: 'grid',
          gridTemplateColumns: chartData?.alerts_by_hour?.length > 0 && chartData?.rule_violations?.length > 0 ? '2fr 1fr' : '1fr',
          gap: '16px',
          marginBottom: '16px',
        }}>
          <AlertVolumeTrendline data={chartData?.alerts_by_hour ?? []} />
          <RuleViolationRanking data={chartData?.rule_violations ?? []} />
        </div>
      )}

      {/* Воронка конверсий */}
      {performance?.funnel?.length > 0 && (
        <div style={{ marginBottom: '16px' }}>
          <FunnelChart funnel={performance.funnel} />
        </div>
      )}

      {/* Spend Timeline */}
      {performance?.timeline?.length > 0 && (
        <SpendTimelineChart data={performance.timeline} />
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

      {/* Таблица объявлений ниже при скролле */}
      <DenseAdTable incidents={incidents} />

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
    <div style={{ display: 'flex', justifyContent: 'space-between', fontSize: '12px', padding: '6px', borderBottom: '1px solid rgba(0,0,0,0.05)' }}>
      <span style={{ color: 'var(--text-muted)' }}>{label}</span>
      <span style={{ fontWeight: 600, color }}>{value}</span>
    </div>
  );
}
