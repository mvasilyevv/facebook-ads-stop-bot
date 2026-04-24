import { useMemo } from 'react';
import { useQuery, useQueryClient } from '@tanstack/react-query';
import { getDashboardHealthMap } from '../api.js';
import { useRefreshOnResume } from '../hooks/useRefreshOnResume.js';
import { formatTime, timeAgo } from '../utils/timeUtils.js';

const STATUS_LABELS = {
  ACTIVE: 'Активно',
  BLOCKED: 'Застой',
  DISABLING: 'Занят',
  DONE: 'Готово',
  EMPTY: 'Пусто',
  ERROR: 'Ошибка',
  FLOWING: 'Идёт поток',
  IDLE: 'Ожидание',
  LIVE: 'Актуально',
  NORMAL: 'Норма',
  OFFLINE: 'Офлайн',
  OK: 'OK',
  ONLINE: 'Онлайн',
  PAUSED: 'Пауза',
  READY: 'Готово',
  RUNNING: 'В работе',
  STALE: 'Устарело',
  STOP: 'STOP',
  WAITING: 'Ждёт',
  WAITING_AUTHORIZATION: 'Нужна авторизация',
  WAITING_BOT_TOKEN: 'Нужен токен',
  WAITING_BROWSER: 'Ждёт браузер',
  WARNING: 'Предупреждение',
};

const TONE_STYLES = {
  success: {
    border: 'rgba(34, 197, 94, 0.28)',
    glow: 'rgba(34, 197, 94, 0.12)',
    badgeBg: 'rgba(34, 197, 94, 0.16)',
    badgeText: '#4ade80',
    line: 'rgba(34, 197, 94, 0.65)',
    lineSoft: 'rgba(34, 197, 94, 0.18)',
  },
  warning: {
    border: 'rgba(245, 158, 11, 0.28)',
    glow: 'rgba(245, 158, 11, 0.12)',
    badgeBg: 'rgba(245, 158, 11, 0.16)',
    badgeText: '#fbbf24',
    line: 'rgba(245, 158, 11, 0.7)',
    lineSoft: 'rgba(245, 158, 11, 0.18)',
  },
  danger: {
    border: 'rgba(239, 68, 68, 0.28)',
    glow: 'rgba(239, 68, 68, 0.12)',
    badgeBg: 'rgba(239, 68, 68, 0.16)',
    badgeText: '#f87171',
    line: 'rgba(239, 68, 68, 0.72)',
    lineSoft: 'rgba(239, 68, 68, 0.2)',
  },
  info: {
    border: 'rgba(56, 189, 248, 0.28)',
    glow: 'rgba(56, 189, 248, 0.12)',
    badgeBg: 'rgba(56, 189, 248, 0.16)',
    badgeText: '#38bdf8',
    line: 'rgba(56, 189, 248, 0.72)',
    lineSoft: 'rgba(56, 189, 248, 0.18)',
  },
  neutral: {
    border: 'rgba(148, 163, 184, 0.22)',
    glow: 'rgba(71, 85, 105, 0.12)',
    badgeBg: 'rgba(71, 85, 105, 0.22)',
    badgeText: '#cbd5e1',
    line: 'rgba(148, 163, 184, 0.42)',
    lineSoft: 'rgba(71, 85, 105, 0.16)',
  },
};

function getToneStyles(tone) {
  return TONE_STYLES[tone] || TONE_STYLES.neutral;
}

function getStatusLabel(status) {
  return STATUS_LABELS[status] || status || '—';
}

function summarizeHealth(nodes = []) {
  return nodes.reduce(
    (acc, node) => {
      if (node.tone === 'success') acc.success += 1;
      else if (node.tone === 'danger') acc.danger += 1;
      else if (node.tone === 'warning') acc.warning += 1;
      else if (node.tone === 'info') acc.info += 1;
      else acc.neutral += 1;
      return acc;
    },
    { success: 0, warning: 0, danger: 0, info: 0, neutral: 0 },
  );
}

function PipelineNode({ node }) {
  const tone = getToneStyles(node.tone);
  return (
    <div
      className="flex flex-col gap-2 rounded-2xl border p-4 min-w-[180px] max-w-[220px]"
      style={{
        borderColor: tone.border,
        background: `linear-gradient(135deg, ${tone.glow}, rgba(10,13,20,0.95))`,
      }}
    >
      {/* Dot + label */}
      <div className="flex items-center gap-2">
        <span
          className="h-2.5 w-2.5 rounded-full shrink-0"
          style={{ backgroundColor: tone.badgeText, boxShadow: `0 0 6px ${tone.badgeText}` }}
        />
        <span className="text-[10px] font-semibold uppercase tracking-widest text-muted truncate">
          {node.label}
        </span>
      </div>

      {/* Status badge */}
      <span
        className="self-start rounded-md px-2 py-0.5 text-[10px] font-bold uppercase"
        style={{ backgroundColor: tone.badgeBg, color: tone.badgeText }}
      >
        {getStatusLabel(node.status)}
      </span>

      {/* Headline */}
      <p className="text-xs font-medium text-primary leading-snug line-clamp-2">{node.headline}</p>

      {/* Key detail (first only) */}
      {node.details?.[0] && (
        <p className="text-[11px] text-secondary truncate">{node.details[0]}</p>
      )}

      {/* Metrics inline */}
      {node.metrics?.length > 0 && (
        <div className="flex flex-wrap gap-1.5 mt-1">
          {node.metrics.slice(0, 2).map((m) => {
            const mt = getToneStyles(m.tone);
            return (
              <div
                key={m.label}
                className="rounded-lg border px-2 py-1"
                style={{ borderColor: mt.border, backgroundColor: mt.lineSoft }}
              >
                <span className="text-[9px] text-muted uppercase">{m.label} </span>
                <span className="text-xs font-bold text-primary">{m.value}</span>
              </div>
            );
          })}
        </div>
      )}

      {/* Updated */}
      <p className="text-[10px] text-muted mt-auto">
        {node.updated_at ? timeAgo(node.updated_at) : '—'}
      </p>
    </div>
  );
}

function PipelineArrow({ tone = 'neutral' }) {
  const t = getToneStyles(tone);
  return (
    <div className="flex items-center self-center shrink-0 px-1">
      <div className="h-px w-8" style={{ backgroundColor: t.line }} />
      <svg width="8" height="12" viewBox="0 0 8 12" fill="none">
        <path d="M0 0L8 6L0 12" fill={t.line} />
      </svg>
    </div>
  );
}

function PipelineSection({ title, nodeIds, nodeMap }) {
  const nodes = nodeIds.map((id) => nodeMap[id]).filter(Boolean);
  return (
    <div className="rounded-2xl border border-border/50 bg-black/20 p-4">
      <p className="text-[11px] font-semibold uppercase tracking-widest text-muted mb-4">{title}</p>
      <div className="flex items-stretch gap-0 overflow-x-auto pb-1">
        {nodes.map((node, i) => (
          <div key={node.id} className="flex items-center">
            <PipelineNode node={node} />
            {i < nodes.length - 1 && <PipelineArrow tone={node.tone} />}
          </div>
        ))}
      </div>
    </div>
  );
}

const PIPELINE_SECTIONS = [
  { title: 'Основной pipeline', ids: ['observer', 'browser_agent', 'scan_batch', 'telegram'] },
  { title: 'Disable flow', ids: ['alerts', 'disable_queue', 'disable_worker'] },
  { title: 'Enable flow', ids: ['enable_recommendations', 'enable_queue', 'enable_worker'] },
];

function SkeletonPipeline() {
  return (
    <div className="space-y-4">
      <div className="h-28 animate-pulse rounded-2xl bg-white/5" />
      <div className="h-40 animate-pulse rounded-2xl bg-white/5" />
      <div className="h-32 animate-pulse rounded-2xl bg-white/5" />
      <div className="h-32 animate-pulse rounded-2xl bg-white/5" />
    </div>
  );
}

export default function HealthMapPage({ embedded = false }) {
  const queryClient = useQueryClient();

  const { data, isLoading, isFetching, error } = useQuery({
    queryKey: ['dashboardHealthMap'],
    queryFn: getDashboardHealthMap,
    refetchInterval: 5_000,
  });

  useRefreshOnResume(() => {
    queryClient.invalidateQueries({ queryKey: ['dashboardHealthMap'] });
  });

  const nodes = data?.nodes || [];
  const warnings = data?.warnings || [];
  const healthSummary = useMemo(() => summarizeHealth(nodes), [nodes]);
  const nodeMap = useMemo(() => Object.fromEntries(nodes.map((n) => [n.id, n])), [nodes]);

  if (isLoading && !data) {
    return <SkeletonPipeline />;
  }

  if (error) {
    return (
      <div className="rounded-2xl border border-danger/30 bg-danger-muted px-5 py-4">
        <p className="text-sm font-semibold text-danger">Не удалось загрузить health map</p>
        <p className="mt-1 text-sm text-danger/80">{error.message}</p>
      </div>
    );
  }

  return (
    <div className={embedded ? 'space-y-4' : 'space-y-md'}>
      {/* Summary панель */}
      <section
        className="overflow-hidden rounded-[28px] border border-border p-5"
        style={{
          background:
            'radial-gradient(circle at top left, rgba(56, 189, 248, 0.14), rgba(15, 18, 24, 0.94) 32%), radial-gradient(circle at bottom right, rgba(34, 197, 94, 0.08), rgba(15, 18, 24, 0) 30%), rgba(15, 18, 24, 0.96)',
          boxShadow: '0 28px 70px rgba(8, 12, 18, 0.32)',
        }}
      >
        <div className="flex flex-wrap items-start justify-between gap-4">
          <div className="max-w-3xl">
            <p className="text-[11px] font-semibold uppercase tracking-[0.22em] text-accent">
              Живая карта
            </p>
            <h1 className="mt-2 text-2xl font-semibold text-primary">
              Карта состояния контура мониторинга
            </h1>
            <p className="mt-2 max-w-2xl text-sm leading-6 text-secondary">
              Здесь видно, на каком звене сейчас идёт поток: сканирование, сбор batch,
              вычисление сигналов, постановка задач на отключение или включение и доставка
              в Telegram.
            </p>
          </div>

          <div className="grid min-w-[260px] gap-2 text-right">
            <div className="inline-flex items-center justify-end gap-2 text-xs text-secondary">
              <span
                className={`inline-flex h-2.5 w-2.5 rounded-full ${
                  isFetching ? 'bg-accent animate-pulse' : 'bg-success'
                }`}
              />
              {isFetching ? 'Идёт обновление…' : 'Данные синхронизированы'}
            </div>
            <p className="text-sm text-primary">{formatTime(data?.generated_at)}</p>
            <p className="text-xs text-muted">{timeAgo(data?.generated_at)}</p>
          </div>
        </div>

        <div className="mt-5 grid gap-3 sm:grid-cols-2 xl:grid-cols-5">
          <div className="rounded-2xl border border-success/20 bg-success/10 px-4 py-3">
            <p className="text-[11px] uppercase tracking-[0.16em] text-success/80">Стабильно</p>
            <p className="mt-1 text-2xl font-semibold text-success">{healthSummary.success}</p>
          </div>
          <div className="rounded-2xl border border-warning/20 bg-warning/10 px-4 py-3">
            <p className="text-[11px] uppercase tracking-[0.16em] text-warning/80">Требует внимания</p>
            <p className="mt-1 text-2xl font-semibold text-warning">
              {healthSummary.warning + healthSummary.info}
            </p>
          </div>
          <div className="rounded-2xl border border-danger/20 bg-danger/10 px-4 py-3">
            <p className="text-[11px] uppercase tracking-[0.16em] text-danger/80">Критично</p>
            <p className="mt-1 text-2xl font-semibold text-danger">{healthSummary.danger}</p>
          </div>
          <div className="rounded-2xl border border-border bg-white/5 px-4 py-3">
            <p className="text-[11px] uppercase tracking-[0.16em] text-muted">Узлы</p>
            <p className="mt-1 text-2xl font-semibold text-primary">{nodes.length}</p>
          </div>
          <div className="rounded-2xl border border-border bg-white/5 px-4 py-3">
            <p className="text-[11px] uppercase tracking-[0.16em] text-muted">Предупреждения</p>
            <p className="mt-1 text-2xl font-semibold text-primary">{warnings.length}</p>
          </div>
        </div>
      </section>

      {/* Warnings секция */}
      {warnings.length > 0 && (
        <section className="rounded-[24px] border border-warning/25 bg-warning/10 px-5 py-4">
          <div className="flex items-start gap-3">
            <span className="mt-1 inline-flex h-2.5 w-2.5 rounded-full bg-warning animate-pulse" />
            <div className="min-w-0">
              <p className="text-sm font-semibold text-warning">Найденные узкие места</p>
              <div className="mt-3 grid gap-2 xl:grid-cols-2">
                {warnings.map((warning) => (
                  <div
                    key={warning}
                    className="rounded-2xl border border-warning/20 bg-black/10 px-3 py-2.5 text-sm text-warning/90"
                  >
                    {warning}
                  </div>
                ))}
              </div>
            </div>
          </div>
        </section>
      )}

      {/* Pipeline лента */}
      <div className="space-y-4">
        {PIPELINE_SECTIONS.map((section) => (
          <PipelineSection
            key={section.title}
            title={section.title}
            nodeIds={section.ids}
            nodeMap={nodeMap}
          />
        ))}
      </div>
    </div>
  );
}
