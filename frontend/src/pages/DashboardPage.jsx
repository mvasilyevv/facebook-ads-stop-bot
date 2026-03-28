import { useEffect, useRef, useState } from 'react';
import {
  BarChart,
  Bar,
  XAxis,
  YAxis,
  Tooltip,
  ResponsiveContainer,
  ComposedChart,
  Line,
  CartesianGrid,
} from 'recharts';
import {
  getDashboardStats,
  getAlertEvents,
  getDisableTasks,
  getObserverSettings,
  toggleScanning,
  triggerScanNow,
  getChartData,
  restartObserver,
  retryDisableTask,
  getDashboardPerformance,
} from '../api.js';

const ACTIVE_DISABLE_STATUSES = new Set(['PENDING', 'RUNNING', 'RETRYING']);
const STALE_DISABLE_TASK_MS = 5 * 60 * 1000;
const PERFORMANCE_PERIODS = [
  { value: 'today', label: 'Сегодня' },
  { value: '7d', label: '7 дней' },
  { value: '30d', label: '1 месяц' },
];
const SORTABLE_CAMPAIGN_COLUMNS = new Set(['spend', 'deposits', 'spend_per_dep', 'reg_to_dep_rate']);

function formatTime(isoStr) {
  if (!isoStr) return '—';
  const d = new Date(isoStr);
  return d.toLocaleTimeString('ru-RU', { hour: '2-digit', minute: '2-digit', second: '2-digit' });
}

function timeAgo(isoStr) {
  if (!isoStr) return '—';
  const diff = Math.floor((Date.now() - new Date(isoStr)) / 1000);
  if (diff < 60) return `${diff}с назад`;
  if (diff < 3600) return `${Math.floor(diff / 60)}м назад`;
  return `${Math.floor(diff / 3600)}ч назад`;
}

function formatNextRetry(isoStr) {
  if (!isoStr) return '';
  const diff = Math.ceil((new Date(isoStr) - Date.now()) / 1000);
  if (diff <= 0) return 'сейчас';
  if (diff < 60) return `через ${diff}с`;
  return `через ${Math.floor(diff / 60)}м`;
}

function formatMoney(value, digits = 2) {
  if (value == null) return '—';
  return `$${Number(value).toFixed(digits)}`;
}

function formatCount(value) {
  if (value == null) return '—';
  return Number(value).toLocaleString('ru-RU');
}

function formatPercent(value, digits = 1) {
  if (value == null) return '—';
  return `${Number(value).toFixed(digits)}%`;
}

function ruleLabel(code) {
  const map = {
    cpc_stop: 'Дорогой клик',
    cpl_stop: 'Дорогой лид',
    cpr_stop: 'Дорогая рега',
    regs_no_dep_stop: 'Реги без депозитов',
    spend_no_dep_range: 'Расход без депа',
    spend_with_dep_range: 'Расход с депозитом',
  };
  return map[code] || code;
}

function performancePeriodLabel(period) {
  return PERFORMANCE_PERIODS.find((item) => item.value === period)?.label || period;
}

function chartGranularityLabel(period) {
  return period === 'today' ? 'По часам' : 'По дням';
}

function isDisableTaskStale(task) {
  if (!task?.created_at || task.status !== 'RUNNING') return false;
  return Date.now() - new Date(task.created_at).getTime() >= STALE_DISABLE_TASK_MS;
}

function getTaskHealth(tasks) {
  const source = tasks || [];
  const active = source.filter((task) => ACTIVE_DISABLE_STATUSES.has(task.status));
  const running = active.filter((task) => task.status === 'RUNNING');
  const retrying = source.filter((task) => task.status === 'RETRYING');
  const failed = source.filter((task) => task.status === 'FAILED');

  return {
    activeCount: active.length,
    retryingCount: retrying.length,
    failedCount: failed.length,
    staleCount: running.filter(isDisableTaskStale).length,
  };
}

function sortCampaigns(rows, sortState) {
  const source = [...(rows || [])];
  const direction = sortState.direction === 'asc' ? 1 : -1;
  return source.sort((a, b) => {
    const left = a?.[sortState.key];
    const right = b?.[sortState.key];
    const leftNull = left == null;
    const rightNull = right == null;

    if (leftNull && rightNull) {
      return a.campaign.localeCompare(b.campaign, 'ru');
    }
    if (leftNull) return 1;
    if (rightNull) return -1;
    if (left === right) {
      return a.campaign.localeCompare(b.campaign, 'ru');
    }
    return left > right ? direction : -direction;
  });
}

function ChartTooltip({ active, payload, label, formatter }) {
  if (!active || !payload?.length) return null;
  return (
    <div className="chart-tooltip">
      <div className="chart-tooltip__label">{label}</div>
      {payload.map((item) => (
        <div key={item.dataKey} className="chart-tooltip__row" style={{ color: item.color }}>
          <span>{item.name}</span>
          <strong>{formatter ? formatter(item.dataKey, item.value) : item.value}</strong>
        </div>
      ))}
    </div>
  );
}

function SectionHeader({ title, hint, actions }) {
  return (
    <div className="dashboard-section__header">
      <div className="dashboard-section__title-block">
        <h3 className="dashboard-section__title">{title}</h3>
        {hint && <p className="dashboard-section__subtitle">{hint}</p>}
      </div>
      {actions ? <div className="dashboard-section__actions">{actions}</div> : null}
    </div>
  );
}

function PeriodSwitch({ value, onChange }) {
  return (
    <div className="period-switch" role="tablist" aria-label="Период performance">
      {PERFORMANCE_PERIODS.map((option) => (
        <button
          key={option.value}
          type="button"
          className={`period-switch__button ${value === option.value ? 'period-switch__button--active' : ''}`}
          onClick={() => onChange(option.value)}
        >
          {option.label}
        </button>
      ))}
    </div>
  );
}

function AnalyticsScopeSection({ period, onPeriodChange }) {
  return (
    <div className="dashboard-section dashboard-section--scope">
      <SectionHeader
        title="Период аналитики"
        hint="Один временной фильтр применяется к сводке, воронке, динамике и операционной аналитике."
        actions={<PeriodSwitch value={period} onChange={onPeriodChange} />}
      />
    </div>
  );
}

function ScanStatusBar({ settings, onToggle, onScanNow, scanning, lastScanAt, onRestart }) {
  const [elapsed, setElapsed] = useState(null);
  const timerRef = useRef(null);

  useEffect(() => {
    if (!lastScanAt) {
      setElapsed(null);
      return;
    }
    const tick = () => setElapsed(Math.floor((Date.now() - new Date(lastScanAt)) / 1000));
    tick();
    timerRef.current = setInterval(tick, 1000);
    return () => clearInterval(timerRef.current);
  }, [lastScanAt]);

  const isActive = settings?.is_scanning_enabled;
  const avgInterval = (settings?.interval_seconds || 90) + Math.floor((settings?.jitter_seconds || 10) / 2);
  const remaining = elapsed !== null ? Math.max(0, avgInterval - elapsed) : null;
  const isStuck = isActive && elapsed !== null && elapsed > avgInterval * 3;

  return (
    <div className={`scan-status-bar ${isActive && !isStuck ? 'scan-status-bar--active' : isStuck ? 'scan-status-bar--stuck' : 'scan-status-bar--paused'}`}>
      <div className="scan-status-bar__left">
        <span className={`scan-dot ${isActive && !isStuck ? 'scan-dot--active' : isStuck ? 'scan-dot--stuck' : ''}`} />
        <span className="scan-status-bar__label">
          {isStuck
            ? 'Воркер не отвечает'
            : isActive
            ? 'Сканирование активно'
            : 'Сканирование остановлено'}
        </span>
        {isActive && !isStuck && remaining !== null && (
          <span className="scan-status-bar__timer">
            Следующий скан: <strong>{remaining > 0 ? `${remaining}с` : 'сейчас...'}</strong>
          </span>
        )}
        {isStuck && elapsed !== null && (
          <span className="scan-status-bar__stuck-hint">
            Последний скан {Math.floor(elapsed / 60)}м назад — перезапустите воркер
          </span>
        )}
        {lastScanAt && <span className="scan-status-bar__last">Последний: {formatTime(lastScanAt)}</span>}
      </div>
      <div className="scan-status-bar__actions">
        {isActive && !isStuck && (
          <button
            className={`scan-now-btn ${scanning ? 'scan-now-btn--active' : ''}`}
            onClick={onScanNow}
            disabled={scanning}
            title="Запустить сканирование немедленно"
          >
            {scanning ? '⏳ Запрошено...' : '▶ Сейчас'}
          </button>
        )}
        {isStuck && (
          <button className="restart-btn" onClick={onRestart} title="Перезапустить observer worker">
            🔄 Перезапустить воркер
          </button>
        )}
        <button
          className={`scan-toggle-btn ${isActive ? 'scan-toggle-btn--stop' : 'scan-toggle-btn--start'}`}
          onClick={onToggle}
        >
          {isActive ? 'Остановить' : 'Запустить'}
        </button>
      </div>
    </div>
  );
}

function StatCard({ value, label, icon, variant, hint, onClick }) {
  const className = `stat-card stat-card--${variant || 'default'} ${onClick ? 'stat-card--clickable' : ''}`;
  const content = (
    <>
      <span className="stat-card__icon">{icon}</span>
      <span className="stat-card__value">{value ?? '—'}</span>
      <span className="stat-card__label">{label}</span>
      {hint ? <span className="stat-card__hint">{hint}</span> : null}
    </>
  );

  if (onClick) {
    return (
      <button className={className} onClick={onClick} type="button">
        {content}
      </button>
    );
  }
  return <div className={className}>{content}</div>;
}

function DisableTasksSection({ tasks, onRetry }) {
  const active = (tasks || [])
    .filter((task) => ACTIVE_DISABLE_STATUSES.has(task.status) || task.status === 'FAILED')
    .sort((a, b) => {
      const order = { RUNNING: 0, RETRYING: 1, PENDING: 2, FAILED: 3 };
      const stateDiff = (order[a.status] ?? 99) - (order[b.status] ?? 99);
      if (stateDiff !== 0) return stateDiff;
      return new Date(b.created_at) - new Date(a.created_at);
    });

  if (active.length === 0) return null;

  return (
    <div className="dashboard-section">
      <SectionHeader
        title="Очередь отключений"
        hint="RUNNING, RETRYING и ошибки остаются рядом с первым экраном"
        actions={<span className="badge badge--warning">{active.length}</span>}
      />
      <div className="disable-tasks-list">
        {active.map((task) => (
          <div
            key={task.id}
            className={`disable-task-row disable-task-row--${task.status.toLowerCase()} ${isDisableTaskStale(task) ? 'disable-task-row--stale' : ''}`}
          >
            <div className="disable-task-row__name">{task.ad_name}</div>
            <div className="disable-task-row__info">
              {task.status === 'RUNNING' && (
                <span className={`task-status ${isDisableTaskStale(task) ? 'task-status--stale' : 'task-status--running'}`}>
                  {isDisableTaskStale(task) ? '⚠️ Зависло в браузере' : '🔄 Выключаем в браузере'}
                  <span className="task-status__retry"> · {timeAgo(task.created_at)}</span>
                </span>
              )}
              {task.status === 'PENDING' && (
                <span className="task-status task-status--pending">
                  ⏳ В очереди (попытка {task.attempt_count + 1})
                </span>
              )}
              {task.status === 'RETRYING' && (
                <span className="task-status task-status--retrying">
                  🔁 Повтор {task.attempt_count}/10
                  {task.next_retry_at && (
                    <span className="task-status__retry"> · {formatNextRetry(task.next_retry_at)}</span>
                  )}
                </span>
              )}
              {task.status === 'FAILED' && (
                <span className="task-status task-status--failed">
                  ❌ {task.last_error || 'неизвестно'}
                </span>
              )}
            </div>
            {(task.status === 'RETRYING' || task.status === 'FAILED') && (
              <button
                className="task-retry-btn"
                onClick={() => onRetry(task.id)}
                title="Повторить немедленно"
              >
                ↺ Сейчас
              </button>
            )}
          </div>
        ))}
      </div>
    </div>
  );
}

function LatestCriticalEventCard({ latestAlert, onNavigate }) {
  const latestIsStop = latestAlert?.stage === 'STOP';
  const rules = latestAlert?.matched_rule_codes || [];
  const metrics = latestAlert?.metrics_json || {};

  return (
    <div className="dashboard-section dashboard-section--compact">
      <SectionHeader
        title="Последнее событие"
        hint="Последний warning или stop с причиной и быстрым переходом к объявлению"
      />
      <div className={`latest-event ${latestIsStop ? 'latest-event--stop' : 'latest-event--warning'}`}>
        {!latestAlert ? (
          <div className="latest-event__empty">Критичных событий пока нет</div>
        ) : (
          <>
            <div className="latest-event__top">
              <div>
                <div className="latest-event__status">
                  {latestIsStop ? 'Стоп-алерт' : 'Предупреждение'} · {timeAgo(latestAlert.created_at)}
                </div>
                <div className="latest-event__name">{latestAlert.ad_name}</div>
              </div>
              <div className="latest-event__time">{formatTime(latestAlert.created_at)}</div>
            </div>
            <div className="latest-event__meta">ID: {latestAlert.fb_ad_id}</div>
            <div className="latest-event__reason">
              <span>Причина</span>
              <strong>{rules.length > 0 ? rules.map(ruleLabel).join(' · ') : 'Не указана'}</strong>
            </div>
            <div className="latest-event__metrics">
              <div className="latest-event__metric">
                <span>Spend</span>
                <strong>{formatMoney(metrics.spend)}</strong>
              </div>
              <div className="latest-event__metric">
                <span>Реги</span>
                <strong>{formatCount(metrics.registrations)}</strong>
              </div>
              <div className="latest-event__metric">
                <span>Депозиты</span>
                <strong>{formatCount(metrics.deposits)}</strong>
              </div>
            </div>
            <div className="latest-event__actions">
              <button
                type="button"
                className="latest-event__action"
                onClick={() => onNavigate('/ads?view=all&state=CLAIMED')}
              >
                RUN
              </button>
              <button
                type="button"
                className="latest-event__action latest-event__action--ghost"
                onClick={() => onNavigate(latestIsStop ? '/ads?state=STOP_SENT' : '/ads?state=WARNING_SENT')}
              >
                Ads
              </button>
            </div>
          </>
        )}
      </div>
    </div>
  );
}

function PerformanceSummarySection({ summary, period }) {
  return (
    <div className="dashboard-section">
      <SectionHeader
        title="Сводка эффективности"
        hint={`Период ${performancePeriodLabel(period)}. Без ROI: только расход, cost-метрики и текущая воронка.`}
      />
      <div className="stat-cards-grid stat-cards-grid--performance">
        <StatCard
          value={formatMoney(summary?.spend)}
          label="Расход"
          icon="SPD"
          hint={`${formatCount(summary?.clicks)} кликов`}
        />
        <StatCard
          value={formatMoney(summary?.cpc, 4)}
          label="CPC"
          icon="CPC"
          hint={summary?.clicks ? `${formatCount(summary.clicks)} кликов` : 'Без кликов'}
        />
        <StatCard
          value={formatMoney(summary?.cpl, 4)}
          label="CPL"
          icon="CPL"
          hint={summary?.leads ? `${formatCount(summary.leads)} лидов` : 'Лидов нет'}
        />
        <StatCard
          value={formatMoney(summary?.cpr, 4)}
          label="CPR"
          icon="CPR"
          hint={summary?.registrations ? `${formatCount(summary.registrations)} регов` : 'Регов нет'}
        />
        <StatCard
          value={formatMoney(summary?.spend_per_dep, 4)}
          label="Расход / деп"
          icon="DEP"
          hint={summary?.deposits ? `${formatCount(summary.deposits)} депозитов` : 'Депозитов нет'}
        />
        <StatCard
          value={formatPercent(summary?.reg_to_dep_rate)}
          label="Reg → Dep"
          icon="R2D"
          hint={summary?.registrations ? `${formatCount(summary.registrations)} регов в базе` : 'Нет базы для расчёта'}
        />
      </div>
    </div>
  );
}

function TotalFunnelSection({ funnel, period }) {
  const steps = funnel || [];
  const maxCount = steps.reduce((max, step) => Math.max(max, step.count || 0), 0) || 1;

  return (
    <div className="dashboard-section">
      <SectionHeader
        title="Общая воронка"
        hint={`Клики, лиды, реги и депозиты за ${performancePeriodLabel(period).toLowerCase()}`}
      />
      <div className="funnel-overview">
        {steps.length === 0 ? (
          <div className="dashboard-chart-empty">Нет данных для воронки</div>
        ) : (
          steps.map((step, index) => (
            <div key={step.key} className="funnel-stage">
              <div className="funnel-stage__head">
                <span className="funnel-stage__label">{step.label}</span>
                <strong className="funnel-stage__value">{formatCount(step.count)}</strong>
              </div>
              <div className="funnel-stage__bar">
                <span
                  className="funnel-stage__fill"
                  style={{ width: `${Math.max(12, (Number(step.count || 0) / maxCount) * 100)}%` }}
                />
              </div>
              <div className="funnel-stage__foot">
                {index === 0 ? 'Базовый шаг' : `${formatPercent(step.conversion_rate)} от прошлого шага`}
              </div>
            </div>
          ))
        )}
      </div>
    </div>
  );
}

function PerformanceTimelineChart({ data, period }) {
  const hasData = (data || []).some((point) => Number(point.spend) > 0 || point.registrations > 0 || point.deposits > 0);

  function TimelineTooltip({ active, payload, label }) {
    return (
      <ChartTooltip
        active={active}
        payload={payload}
        label={label}
        formatter={(key, value) => {
          if (key === 'spend') return formatMoney(value);
          return formatCount(value);
        }}
      />
    );
  }

  return (
    <div className="dashboard-section">
      <SectionHeader
        title="Динамика расхода и конверсий"
        hint={`Точки по времени последнего наблюдения за ${performancePeriodLabel(period).toLowerCase()}`}
      />
      <div className="dashboard-chart-card dashboard-chart-card--embedded">
        {!hasData ? (
          <div className="dashboard-chart-empty">Нет данных для таймлайна</div>
        ) : (
          <ResponsiveContainer width="100%" height={260}>
            <ComposedChart data={data} margin={{ top: 8, right: 24, left: -18, bottom: 8 }}>
              <CartesianGrid stroke="rgba(122,130,160,0.14)" vertical={false} />
              <XAxis
                dataKey="label"
                tick={{ fill: '#545c80', fontSize: 11 }}
                tickLine={false}
                axisLine={false}
                minTickGap={16}
              />
              <YAxis
                yAxisId="spend"
                tick={{ fill: '#3a4065', fontSize: 11 }}
                tickLine={false}
                axisLine={false}
                tickFormatter={(value) => `$${value}`}
              />
              <YAxis
                yAxisId="count"
                orientation="right"
                tick={{ fill: '#3a4065', fontSize: 11 }}
                tickLine={false}
                axisLine={false}
                allowDecimals={false}
              />
              <Tooltip content={<TimelineTooltip />} cursor={{ fill: 'rgba(77,136,255,0.06)' }} />
              <Bar
                yAxisId="spend"
                dataKey="spend"
                name="Расход"
                fill="#4d88ff"
                radius={[4, 4, 0, 0]}
                maxBarSize={28}
                fillOpacity={0.7}
              />
              <Line
                yAxisId="count"
                type="monotone"
                dataKey="registrations"
                name="Реги"
                stroke="#ff9a20"
                strokeWidth={2}
                dot={{ fill: '#ff9a20', r: 3, strokeWidth: 0 }}
              />
              <Line
                yAxisId="count"
                type="monotone"
                dataKey="deposits"
                name="Депозиты"
                stroke="#00e896"
                strokeWidth={2}
                dot={{ fill: '#00e896', r: 4, strokeWidth: 0 }}
              />
            </ComposedChart>
          </ResponsiveContainer>
        )}
      </div>
    </div>
  );
}

function CampaignFunnelTable({ rows, sortState, onSort }) {
  const sortMark = (key) => {
    if (sortState.key !== key) return '↕';
    return sortState.direction === 'asc' ? '↑' : '↓';
  };

  const renderHeaderCell = (key, label) => {
    if (!SORTABLE_CAMPAIGN_COLUMNS.has(key)) {
      return <span>{label}</span>;
    }
    return (
      <button type="button" className="campaign-table__sort" onClick={() => onSort(key)}>
        {label} <span>{sortMark(key)}</span>
      </button>
    );
  };

  return (
    <div className="dashboard-section">
      <SectionHeader
        title="Кампании по воронке"
        hint="Сравнение кампаний по расходу, cost-метрикам и конверсиям"
      />
      {!rows?.length ? (
        <div className="dashboard-chart-empty">Кампаний для сравнения пока нет</div>
      ) : (
        <div className="campaign-table-wrap">
          <table className="campaign-table">
            <thead>
              <tr>
                <th>{renderHeaderCell('campaign', 'Кампания')}</th>
                <th>{renderHeaderCell('spend', 'Расход')}</th>
                <th>Клики</th>
                <th>Лиды</th>
                <th>Реги</th>
                <th>{renderHeaderCell('deposits', 'Депы')}</th>
                <th>CPC</th>
                <th>CPL</th>
                <th>CPR</th>
                <th>{renderHeaderCell('spend_per_dep', 'Расход / деп')}</th>
                <th>Клик → лид</th>
                <th>Лид → рег</th>
                <th>{renderHeaderCell('reg_to_dep_rate', 'Рег → деп')}</th>
              </tr>
            </thead>
            <tbody>
              {rows.map((row) => (
                <tr key={row.campaign}>
                  <td className="campaign-table__campaign">{row.campaign}</td>
                  <td>{formatMoney(row.spend)}</td>
                  <td>{formatCount(row.clicks)}</td>
                  <td>{formatCount(row.leads)}</td>
                  <td>{formatCount(row.registrations)}</td>
                  <td>{formatCount(row.deposits)}</td>
                  <td>{formatMoney(row.cpc, 4)}</td>
                  <td>{formatMoney(row.cpl, 4)}</td>
                  <td>{formatMoney(row.cpr, 4)}</td>
                  <td>{formatMoney(row.spend_per_dep, 4)}</td>
                  <td>{formatPercent(row.click_to_lead_rate)}</td>
                  <td>{formatPercent(row.lead_to_reg_rate)}</td>
                  <td>{formatPercent(row.reg_to_dep_rate)}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
    </div>
  );
}

const CHART_COLORS = {
  warning: '#ff9a20',
  stop: '#ff2b50',
  normal: '#00e896',
  blue: '#4d88ff',
};

function AlertActivityChart({ data, period }) {
  const hasData = data?.some((item) => item.warning > 0 || item.stop > 0);
  return (
    <div className="dashboard-chart-card">
      <div className="dashboard-chart-card__header">
        <span className="dashboard-chart-card__title">Активность алертов</span>
        <span className="dashboard-chart-card__hint">
          {performancePeriodLabel(period)} · {chartGranularityLabel(period).toLowerCase()}
        </span>
      </div>
      {!hasData ? (
        <div className="dashboard-chart-empty">Алертов за выбранный период нет</div>
      ) : (
        <ResponsiveContainer width="100%" height={180}>
          <BarChart data={data} margin={{ top: 4, right: 8, left: -20, bottom: 0 }}>
            <XAxis
              dataKey="hour"
              tick={{ fill: '#3a4065', fontSize: 11 }}
              tickLine={false}
              axisLine={false}
              interval="preserveStartEnd"
              minTickGap={18}
            />
            <YAxis
              tick={{ fill: '#3a4065', fontSize: 11 }}
              tickLine={false}
              axisLine={false}
              allowDecimals={false}
            />
            <Tooltip content={<ChartTooltip />} cursor={{ fill: 'rgba(77,136,255,0.06)' }} />
            <Bar dataKey="warning" name="Предупреждение" fill={CHART_COLORS.warning} radius={[3, 3, 0, 0]} maxBarSize={20} />
            <Bar dataKey="stop" name="Стоп" fill={CHART_COLORS.stop} radius={[3, 3, 0, 0]} maxBarSize={20} />
          </BarChart>
        </ResponsiveContainer>
      )}
    </div>
  );
}

function RuleViolationsChart({ data, period }) {
  const hasData = data?.length > 0;
  return (
    <div className="dashboard-chart-card">
      <div className="dashboard-chart-card__header">
        <span className="dashboard-chart-card__title">Нарушения правил</span>
        <span className="dashboard-chart-card__hint">
          {performancePeriodLabel(period)} · срабатываний
        </span>
      </div>
      {!hasData ? (
        <div className="dashboard-chart-empty">Нарушений за выбранный период нет</div>
      ) : (
        <ResponsiveContainer width="100%" height={180}>
          <BarChart data={data} layout="vertical" margin={{ top: 4, right: 16, left: 8, bottom: 0 }}>
            <XAxis
              type="number"
              tick={{ fill: '#3a4065', fontSize: 11 }}
              tickLine={false}
              axisLine={false}
              allowDecimals={false}
            />
            <YAxis
              type="category"
              dataKey="rule"
              tick={{ fill: '#545c80', fontSize: 12 }}
              tickLine={false}
              axisLine={false}
              width={90}
            />
            <Tooltip content={<ChartTooltip />} cursor={{ fill: 'rgba(77,136,255,0.06)' }} />
            <Bar dataKey="count" name="Кол-во" fill={CHART_COLORS.blue} radius={[0, 3, 3, 0]} maxBarSize={18} />
          </BarChart>
        </ResponsiveContainer>
      )}
    </div>
  );
}

function TopCampaignsBySpendChart({ data, period }) {
  const normalizedData = (data || []).map((item) => ({
    ...item,
    spend: Number(item?.spend || 0),
  }));
  const hasData = normalizedData.length > 0;

  function TopCampaignsTooltip({ active, payload }) {
    if (!active || !payload?.length) return null;
    const item = payload[0]?.payload;
    return (
      <div className="chart-tooltip">
        <div className="chart-tooltip__label" style={{ maxWidth: 220, whiteSpace: 'normal', marginBottom: 4 }}>
          {item?.campaign_full}
        </div>
        <div className="chart-tooltip__row" style={{ color: CHART_COLORS.blue }}>
          <span>Расход</span><strong>{formatMoney(item?.spend)}</strong>
        </div>
        <div className="chart-tooltip__row" style={{ color: '#a0a8c8' }}>
          <span>Лиды</span><strong>{formatCount(item?.leads)}</strong>
        </div>
        <div className="chart-tooltip__row" style={{ color: CHART_COLORS.warning }}>
          <span>Реги</span><strong>{formatCount(item?.registrations)}</strong>
        </div>
        <div className="chart-tooltip__row" style={{ color: CHART_COLORS.normal }}>
          <span>Депозиты</span><strong>{formatCount(item?.deposits)}</strong>
        </div>
      </div>
    );
  }

  return (
    <div className="dashboard-chart-card">
      <div className="dashboard-chart-card__header">
        <span className="dashboard-chart-card__title">Топ кампаний по расходу</span>
        <span className="dashboard-chart-card__hint">
          {performancePeriodLabel(period)} · расход и результат
        </span>
      </div>
      {!hasData ? (
        <div className="dashboard-chart-empty">Нет кампаний с расходом за выбранный период</div>
      ) : (
        <ResponsiveContainer width="100%" height={180}>
          <BarChart data={normalizedData} layout="vertical" margin={{ top: 4, right: 48, left: 8, bottom: 0 }}>
            <XAxis
              type="number"
              tick={{ fill: '#3a4065', fontSize: 11 }}
              tickLine={false}
              axisLine={false}
              tickFormatter={(value) => `$${value}`}
            />
            <YAxis
              type="category"
              dataKey="campaign"
              tick={{ fill: '#545c80', fontSize: 10 }}
              tickLine={false}
              axisLine={false}
              width={140}
            />
            <Tooltip content={<TopCampaignsTooltip />} cursor={{ fill: 'rgba(77,136,255,0.06)' }} />
            <Bar dataKey="spend" name="Расход" radius={[0, 3, 3, 0]} maxBarSize={16} fill={CHART_COLORS.blue} fillOpacity={0.85} />
          </BarChart>
        </ResponsiveContainer>
      )}
    </div>
  );
}

function TopAdsBySpendChart({ data }) {
  const normalizedData = (data || []).map((item) => ({
    ...item,
    spend: Number(item?.spend || 0),
    cpc: item?.cpc != null ? Number(item.cpc) : null,
  }));
  const hasData = normalizedData.length > 0;

  function TopAdsTooltip({ active, payload }) {
    if (!active || !payload?.length) return null;
    const item = payload[0]?.payload;
    return (
      <div className="chart-tooltip">
        <div className="chart-tooltip__label" style={{ maxWidth: 220, whiteSpace: 'normal', marginBottom: 4 }}>
          {item?.state_icon} {item?.name_full}
        </div>
        {item?.adset_name ? (
          <div className="chart-tooltip__subtle" style={{ maxWidth: 220, whiteSpace: 'normal', marginBottom: 6 }}>
            Адсет: {item.adset_name}
          </div>
        ) : null}
        <div className="chart-tooltip__row" style={{ color: CHART_COLORS.blue }}>
          <span>Расход</span><strong>{formatMoney(item?.spend)}</strong>
        </div>
        {item?.cpc != null && (
          <div className="chart-tooltip__row" style={{ color: CHART_COLORS.warning }}>
            <span>CPC</span><strong>{formatMoney(item?.cpc, 2)}</strong>
          </div>
        )}
        <div className="chart-tooltip__row" style={{ color: '#a0a8c8' }}>
          <span>Лиды</span><strong>{formatCount(item?.leads)}</strong>
        </div>
        <div className="chart-tooltip__row" style={{ color: CHART_COLORS.normal }}>
          <span>Депозиты</span><strong>{formatCount(item?.deposits)}</strong>
        </div>
      </div>
    );
  }

  return (
    <div className="dashboard-chart-card">
      <div className="dashboard-chart-card__header">
        <span className="dashboard-chart-card__title">Топ объявлений по расходу</span>
        <span className="dashboard-chart-card__hint">Живой срез · с расходом</span>
      </div>
      {!hasData ? (
        <div className="dashboard-chart-empty">Нет объявлений с расходом в текущем срезе</div>
      ) : (
        <ResponsiveContainer width="100%" height={180}>
          <BarChart data={normalizedData} layout="vertical" margin={{ top: 4, right: 48, left: 8, bottom: 0 }}>
            <XAxis
              type="number"
              tick={{ fill: '#3a4065', fontSize: 11 }}
              tickLine={false}
              axisLine={false}
              tickFormatter={(value) => `$${value}`}
            />
            <YAxis
              type="category"
              dataKey="label"
              tick={{ fill: '#545c80', fontSize: 10 }}
              tickLine={false}
              axisLine={false}
              width={170}
            />
            <Tooltip content={<TopAdsTooltip />} cursor={{ fill: 'rgba(77,136,255,0.06)' }} />
            <Bar dataKey="spend" name="Расход" radius={[0, 3, 3, 0]} maxBarSize={16} fill={CHART_COLORS.blue} fillOpacity={0.85} />
          </BarChart>
        </ResponsiveContainer>
      )}
    </div>
  );
}

export default function DashboardPage({ onNavigate }) {
  const navigate = onNavigate || (() => {});
  const [stats, setStats] = useState(null);
  const [alerts, setAlerts] = useState([]);
  const [tasks, setTasks] = useState([]);
  const [settings, setSettings] = useState(null);
  const [chartData, setChartData] = useState(null);
  const [performance, setPerformance] = useState(null);
  const [period, setPeriod] = useState('today');
  const [campaignSort, setCampaignSort] = useState({ key: 'spend', direction: 'desc' });
  const [toggling, setToggling] = useState(false);
  const [scanning, setScanning] = useState(false);
  const [error, setError] = useState(null);

  const loadData = async () => {
    try {
      const [statsResponse, alertsResponse, tasksResponse, settingsResponse, chartResponse, performanceResponse] = await Promise.all([
        getDashboardStats(),
        getAlertEvents({ limit: 1 }),
        getDisableTasks({ limit: 20 }),
        getObserverSettings(),
        getChartData({ period }),
        getDashboardPerformance({ period }),
      ]);
      setStats(statsResponse);
      setAlerts(alertsResponse);
      setTasks(tasksResponse);
      setSettings(settingsResponse);
      setChartData(chartResponse);
      setPerformance(performanceResponse);
      setError(null);
    } catch (e) {
      setError(e.message);
    }
  };

  useEffect(() => {
    loadData();
    const id = setInterval(loadData, 30000);
    return () => clearInterval(id);
  }, [period]);

  useEffect(() => {
    const id = setInterval(async () => {
      try {
        const [tasksResponse, statsResponse, settingsResponse] = await Promise.all([
          getDisableTasks({ limit: 20 }),
          getDashboardStats(),
          getObserverSettings(),
        ]);
        setTasks(tasksResponse);
        setStats(statsResponse);
        setSettings(settingsResponse);
      } catch (_) {}
    }, 5000);
    return () => clearInterval(id);
  }, []);

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

  const handleRetryDisable = async (taskId) => {
    try {
      await retryDisableTask(taskId);
      const tasksResponse = await getDisableTasks({ limit: 20 });
      setTasks(tasksResponse);
    } catch (e) {
      setError(`Не удалось поставить в очередь: ${e.message}`);
    }
  };

  const handleRestart = async () => {
    try {
      await restartObserver();
      setTimeout(loadData, 5000);
    } catch (e) {
      setError(`Не удалось перезапустить воркер: ${e.message}`);
    }
  };

  const handleSort = (key) => {
    if (!SORTABLE_CAMPAIGN_COLUMNS.has(key)) return;
    setCampaignSort((current) => (
      current.key === key
        ? { key, direction: current.direction === 'desc' ? 'asc' : 'desc' }
        : { key, direction: 'desc' }
    ));
  };

  const latestAlert = alerts[0] || null;
  const health = getTaskHealth(tasks);
  const campaignRows = sortCampaigns(performance?.campaigns || [], campaignSort);

  return (
    <div className="dashboard-page">
      {error && <div className="error-banner">⚠ {error}</div>}

      <ScanStatusBar
        settings={settings}
        onToggle={handleToggle}
        onScanNow={handleScanNow}
        onRestart={handleRestart}
        scanning={scanning}
        lastScanAt={stats?.last_scan_at}
      />

      <div className="dashboard-primary-grid">
        <div className="dashboard-section">
          <SectionHeader
            title="Операционный контроль"
            hint="Сканирование, подтверждение OFF и текущее здоровье очереди"
          />
          <div className="stat-cards-grid stat-cards-grid--ops">
            <StatCard
              value={formatCount(stats?.ads_claimed ?? 0)}
              label="OFF не подтверждён"
              icon="OFF"
              variant={(stats?.ads_claimed ?? 0) > 0 ? 'stop' : 'default'}
              hint="Клик был, но observer ещё не увидел OFF"
              onClick={() => navigate('/ads?view=all&state=CLAIMED')}
            />
            <StatCard
              value={formatCount(health.activeCount)}
              label="Активная очередь"
              icon="RUN"
              variant={health.activeCount > 0 ? 'warning' : 'default'}
              hint="Задачи в статусах PENDING, RUNNING и RETRYING"
              onClick={() => navigate('/ads?view=all&state=CLAIMED')}
            />
            <StatCard
              value={formatCount(health.staleCount)}
              label="Зависли > 5м"
              icon="STL"
              variant={health.staleCount > 0 ? 'stop' : 'default'}
              hint="RUNNING без подтверждения OFF дольше 5 минут"
            />
            <StatCard
              value={formatCount(stats?.ads_disabled_today ?? 0)}
              label="Выключено сегодня"
              icon="DAY"
              variant="muted"
              hint="Подтверждённые выключения в текущей скан-сессии"
              onClick={() => navigate('/ads?state=DISABLED')}
            />
          </div>
        </div>

        <LatestCriticalEventCard latestAlert={latestAlert} onNavigate={navigate} />
      </div>

      <DisableTasksSection tasks={tasks} onRetry={handleRetryDisable} />

      <AnalyticsScopeSection period={period} onPeriodChange={setPeriod} />

      <PerformanceSummarySection
        summary={performance?.summary}
        period={period}
      />

      <div className="dashboard-performance-grid">
        <TotalFunnelSection funnel={performance?.funnel} period={period} />
        <PerformanceTimelineChart data={performance?.timeline} period={period} />
      </div>

      <CampaignFunnelTable rows={campaignRows} sortState={campaignSort} onSort={handleSort} />

      <div className="dashboard-section">
        <SectionHeader
          title="Операционная аналитика"
          hint={`Диагностика алертов, правил и расхода за ${performancePeriodLabel(period).toLowerCase()}`}
        />
        <div className="dashboard-charts-grid dashboard-charts-grid--ops">
          <AlertActivityChart data={chartData?.alerts_by_hour} period={period} />
          <RuleViolationsChart data={chartData?.rule_violations} period={period} />
          <TopCampaignsBySpendChart data={performance?.campaigns?.slice(0, 8) || []} period={period} />
          <TopAdsBySpendChart data={chartData?.top_ads_by_spend} />
        </div>
      </div>
    </div>
  );
}
