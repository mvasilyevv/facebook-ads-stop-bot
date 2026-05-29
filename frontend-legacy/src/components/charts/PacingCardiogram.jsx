import React from 'react';

/**
 * График Pacing Cardiogram (SVG сглаженный график расхода бюджета во времени).
 */
export default function PacingCardiogram({ points = [], targetPace = 100 }) {
  if (!points || points.length === 0) {
    return (
      <div className="flex h-48 items-center justify-center rounded-md border border-border bg-surface text-xs text-text-muted">
        Нет данных для графика расхода
      </div>
    );
  }

  const maxVal = Math.max(...points.map((p) => p.value), targetPace, 10);
  const minVal = 0;
  const range = maxVal - minVal;

  // Функция перевода значений в координаты SVG (100x40)
  const getX = (index) => points.length > 1 ? (index / (points.length - 1)) * 100 : 50;
  const getY = (value) => 40 - ((value - minVal) / range) * 35;

  // Формируем сглаженный путь (Bezier curve)
  const pathD = points.map((p, idx) => {
    const x = getX(idx);
    const y = getY(p.value);
    if (idx === 0) return `M ${x} ${y}`;
    
    // Вычисляем контрольные точки для сглаживания
    const prevX = getX(idx - 1);
    const prevY = getY(points[idx - 1].value);
    const cpX1 = prevX + (x - prevX) / 2;
    const cpY1 = prevY;
    const cpX2 = prevX + (x - prevX) / 2;
    const cpY2 = y;
    
    return `C ${cpX1} ${cpY1}, ${cpX2} ${cpY2}, ${x} ${y}`;
  }).join(' ');

  // Путь для градиентной заливки
  const fillD = `${pathD} L 100 40 L 0 40 Z`;

  // Линия целевого темпа
  const targetY = getY(targetPace);

  return (
    <div className="rounded-md border border-border bg-surface p-md">
      <div className="flex items-center justify-between border-b border-border pb-sm mb-md">
        <span className="font-mono text-2xs uppercase tracking-wider text-text-dim">Pacing Cardiogram</span>
        <div className="flex items-center gap-md">
          <div className="flex items-center gap-xs">
            <span className="h-2 w-2 rounded-full bg-accent" />
            <span className="font-mono text-2xs text-text-dim">Расход</span>
          </div>
          <div className="flex items-center gap-xs">
            <span className="h-2 w-2 rounded-full bg-info" />
            <span className="font-mono text-2xs text-text-dim">Целевой лимит</span>
          </div>
        </div>
      </div>

      <div className="relative h-48 w-full">
        <svg className="h-full w-full overflow-visible" viewBox="0 0 100 40" preserveAspectRatio="none">
          <defs>
            <linearGradient id="pacingGrad" x1="0" y1="0" x2="0" y2="1">
              <stop offset="0%" stopColor="var(--accent)" stopOpacity="0.25" />
              <stop offset="100%" stopColor="var(--accent)" stopOpacity="0" />
            </linearGradient>
          </defs>

          {/* Сетка */}
          <line x1="0" y1={getY(maxVal * 0.5)} x2="100" y2={getY(maxVal * 0.5)} stroke="var(--border)" strokeWidth="0.2" strokeDasharray="1,1" />
          <line x1="0" y1={getY(maxVal * 0.25)} x2="100" y2={getY(maxVal * 0.25)} stroke="var(--border)" strokeWidth="0.2" strokeDasharray="1,1" />
          <line x1="0" y1={getY(maxVal * 0.75)} x2="100" y2={getY(maxVal * 0.75)} stroke="var(--border)" strokeWidth="0.2" strokeDasharray="1,1" />

          {/* Целевой лимит */}
          <line
            x1="0"
            y1={targetY}
            x2="100"
            y2={targetY}
            stroke="var(--info)"
            strokeWidth="0.5"
            strokeDasharray="2,2"
          />

          {/* Заливка графика */}
          <path d={fillD} fill="url(#pacingGrad)" />

          {/* Сглаженная линия */}
          <path
            d={pathD}
            fill="none"
            stroke="var(--accent)"
            strokeWidth="1"
            strokeLinecap="round"
            strokeLinejoin="round"
          />

          {/* Точки на графике */}
          {points.map((p, idx) => (
            <circle
              key={idx}
              cx={getX(idx)}
              cy={getY(p.value)}
              r="1.2"
              className="fill-surface stroke-accent"
              strokeWidth="0.5"
            />
          ))}
        </svg>

        {/* Бейджи на осях */}
        <div className="absolute left-1 top-1 rounded bg-surface-2 px-xs py-2xs font-mono text-[9px] text-text-dim border border-border">
          Макс: {maxVal.toFixed(0)}$
        </div>
        <div className="absolute right-1 bottom-1 rounded bg-surface-2 px-xs py-2xs font-mono text-[9px] text-text-dim border border-border">
          Цель: {targetPace.toFixed(0)}$
        </div>
      </div>

      <div className="mt-sm flex justify-between font-mono text-[10px] text-text-muted">
        {points.map((p, idx) => (
          <span key={idx}>{p.label}</span>
        ))}
      </div>
    </div>
  );
}
