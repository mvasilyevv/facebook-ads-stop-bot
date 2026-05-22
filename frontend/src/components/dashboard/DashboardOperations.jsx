import { AlertTray } from '../AlertTray.jsx';
import { TaskQueuePanel } from '../TaskQueuePanel.jsx';
import { DashboardCommandBar } from './DashboardCommandBar.jsx';

/** Верхняя операционная зона: command bar + лента инцидентов + очередь задач */
export function DashboardOperations({
  alertTrayRef,
  stats,
  settings,
  vision,
  scanning,
  onToggle,
  onResume,
  onScanNow,
  onAutoEnableToggle,
  onStopClick,
  onWarningClick,
  activeIncidents,
  disableTasks,
  onSelectIncident,
  onDisable,
  onEnableScanning,
  enableTasks,
  enableRecs,
  onRetryDisable,
  onCancelDisable,
  onCreateEnableTask,
}) {
  return (
    <section className="space-y-md" aria-label="Оперативный контроль">
      <h2 className="font-display text-sm text-secondary">
        Оперативный контроль
      </h2>

      <DashboardCommandBar
        stats={stats}
        settings={settings}
        vision={vision}
        scanning={scanning}
        onToggle={onToggle}
        onResume={onResume}
        onScanNow={onScanNow}
        onAutoEnableToggle={onAutoEnableToggle}
        onStopClick={onStopClick}
        onWarningClick={onWarningClick}
      />

      <div className="card-grid grid-cols-1 lg:grid-cols-[1fr_0.54fr]">
        <div ref={alertTrayRef} className="panel-ops h-full min-w-0 overflow-hidden scroll-mt-4">
          <AlertTray
            incidents={activeIncidents}
            disableTasks={disableTasks}
            onSelectIncident={onSelectIncident}
            onDisable={onDisable}
            settings={settings}
            lastScanAt={stats?.last_scan_at}
            onEnableScanning={onEnableScanning}
          />
        </div>

        <div className="panel-ops h-full min-w-0 overflow-hidden">
          <TaskQueuePanel
            disableTasks={disableTasks}
            enableTasks={enableTasks}
            enableRecs={enableRecs}
            onRetryDisable={onRetryDisable}
            onCancelDisable={onCancelDisable}
            onCreateEnableTask={onCreateEnableTask}
          />
        </div>
      </div>
    </section>
  );
}
