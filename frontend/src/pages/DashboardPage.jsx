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
import { CampaignScorecard } from '../components/CampaignScorecard.jsx';
import { DrawerPanel } from '../components/DrawerPanel.jsx';
import { TaskQueuePanel } from '../components/TaskQueuePanel.jsx';
import { BudgetOverrunChart } from '../components/BudgetOverrunChart.jsx';

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

function ScanStatusBar({ settings, onToggle, onScanNow, scanning, lastScanAt }) {
  return (
    <div style={{ background: 'var(--bg-secondary)', padding: '12px 16px', marginBottom: '16px', borderRadius: '4px', display: 'flex', alignItems: 'center', gap: '12px' }}>
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
    <div style={{ marginTop: '16px' }}>
      <h3 style={{ margin: '0 0 12px 0', fontSize: '13px', fontWeight: 600, textTransform: 'uppercase', color: 'var(--text-muted)' }}>
        Таблица объявлений
      </h3>
      <div style={{ overflowX: 'auto' }}>
        <table style={{ width: '100%', fontSize: '12px', borderCollapse: 'collapse' }}>
          <thead>
            <tr style={{ borderBottom: '1px solid var(--border-color)' }}>
              <th style={{ padding: '8px', textAlign: 'left', fontWeight: 600 }}>Объявление</th>
              <th style={{ padding: '8px', textAlign: 'left', fontWeight: 600 }}>Кампания</th>
              <th style={{ padding: '8px', textAlign: 'left', fontWeight: 600 }}>Статус</th>
            </tr>
          </thead>
          <tbody>
            {incidents.slice(0, 10).map((incident) => (
              <tr key={incident.fb_ad_id} style={{ borderBottom: '1px solid rgba(0,0,0,0.05)' }}>
                <td style={{ padding: '8px' }}>{incident.ad_name}</td>
                <td style={{ padding: '8px' }}>{incident.campaign_name}</td>
                <td style={{ padding: '8px' }}>
                  <span style={{
                    display: 'inline-block',
                    padding: '2px 6px',
                    borderRadius: '3px',
                    fontSize: '11px',
                    backgroundColor: 'var(--bg-secondary)',
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

      {/* 3-колонная сетка — главный экран мониторинга */}
      <div className="dashboard-grid" style={{ display: 'grid', gridTemplateColumns: '1fr 2fr 1fr', gap: '16px', marginBottom: '16px' }}>
        {/* Левая колонна: CampaignScorecard */}
        <div className="dashboard-grid__left" style={{ background: 'var(--bg-secondary)', padding: '16px', borderRadius: '4px' }}>
          <CampaignScorecard
            stats={stats}
            performance={performance}
            spendHistory={spendHistory}
            onStateClick={(state) => onNavigate?.(`/ads?state=${state}`)}
          />
        </div>

        {/* Центральная колонна: AlertTray */}
        <div className="dashboard-grid__center" style={{ background: 'var(--bg-secondary)', padding: '16px', borderRadius: '4px' }}>
          <AlertTray
            incidents={activeIncidents}
            disableTasks={disableTasks}
            onSelectIncident={setSelectedIncident}
            onDisable={handleDisable}
          />
        </div>

        {/* Правая колонна: TaskQueuePanel */}
        <div className="dashboard-grid__right">
          <TaskQueuePanel
            disableTasks={disableTasks}
            enableTasks={enableTasks}
            enableRecs={enableRecs}
            onRetryDisable={handleRetry}
            onCreateEnableTask={handleEnableTask}
          />
        </div>
      </div>

      {/* BudgetOverrunChart */}
      {chartData?.campaign_budget_deltas?.length > 0 && (
        <BudgetOverrunChart data={chartData.campaign_budget_deltas} />
      )}

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
