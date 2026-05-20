import React from 'react';

/**
 * Плитка KPI с левым акцентным баром, моноширинными числами и мини-графиком (sparkline).
 */
export default function KPIPlate({ title, value, unit = '', trend = null, status = 'default' }) {
  // Определяем цвет левой границы на основе статуса
  const statusColors = {
    default: 'border-accent',
    ok: 'border-ok',
    warn: 'border-warn',
    stop: 'border-stop',
    info: 'border-info',
  };

  const borderClass = statusColors[status] || 'border-accent';

  return (
    <div className={`relative overflow-hidden rounded-md border border-border bg-surface p-md pl-lg border-l-4 ${borderClass} transition-all hover:border-r hover:shadow-[0_0_12px_rgba(255,107,0,0.06)]`}>
      <span className="font-mono text-2xs uppercase tracking-wider text-text-dim">
        {title}
      </span>
      <div className="mt-xs flex items-baseline gap-xs">
        <span className="font-mono text-xl font-semibold tracking-tight text-text">
          {value}
        </span>
        {unit && (
          <span className="font-mono text-xs text-text-muted">{unit}</span>
        )}
      </div>

      {trend && trend.length > 0 && (
        <div className="absolute right-md bottom-xs h-10 w-24">
          <svg className="h-full w-full overflow-visible" viewBox="0 0 100 40">
            <defs>
              <linearGradient id="sparklineGrad" x1="0" y1="0" x2="0" y2="1">
                <stop offset="0%" stopColor="var(--accent)" stopOpacity="0.25" />
                <stop offset="100%" stopColor="var(--accent)" stopOpacity="0" />
              </linearGradient>
            </defs>
            {/* Градиентная заливка */}
            <path
              d={`M 0 40 ${trend.map((val, idx) => `L ${trend.length > 1 ? (idx / (trend.length - 1)) * 100 : 50} ${40 - (val * 35)}`).join(' ')} L 100 40 Z`}
              fill="url(#sparklineGrad)"
            />
            {/* Линия */}
            <path
              d={trend.map((val, idx) => `${idx === 0 ? 'M' : 'L'} ${trend.length > 1 ? (idx / (trend.length - 1)) * 100 : 50} ${40 - (val * 35)}`).join(' ')}
              fill="none"
              stroke="var(--accent)"
              strokeWidth="1.5"
              strokeLinecap="round"
              strokeLinejoin="round"
            />
          </svg>
        </div>
      )}
    </div>
  );
}
