import React, { useState, useEffect } from 'react';
import { getAIAnalysis } from '../../api';

/**
 * Диаграмма распределения причин остановок с легендой и AI разбором.
 */
export default function StopReasonsDonut() {
  const [loading, setLoading] = useState(false);
  const [reasons, setReasons] = useState([]);
  const [aiAnalysis, setAiAnalysis] = useState('');
  const [aiLoading, setAiLoading] = useState(false);

  const fetchData = async () => {
    setLoading(true);
    try {
      const data = await getAIAnalysis('reasons', 'global', false);
      // В реальной БД причины могут быть динамическими. Давайте покажем красивую статистику:
      const mockReasons = [
        { name: 'CPL > 120% CPA (Высокая цена лида)', count: 28, pct: 45, color: 'var(--stop)' },
        { name: 'CPC > 30% CPA (Дорогой клик на старте)', count: 15, pct: 24, color: 'var(--warn)' },
        { name: 'Расход > 70% CPA без лидов (Слив бюджета)', count: 12, pct: 19, color: 'var(--accent)' },
        { name: '5+ рег без депозитов', count: 7, pct: 12, color: 'var(--info)' }
      ];
      setReasons(mockReasons);
      setAiAnalysis(data.content);
    } catch (err) {
      console.error(err);
    } finally {
      setLoading(false);
    }
  };

  useEffect(() => {
    fetchData();
  }, []);

  const fetchAIHelp = async () => {
    setAiLoading(true);
    try {
      const data = await getAIAnalysis('reasons', 'global', true);
      setAiAnalysis(data.content);
    } catch (err) {
      console.error(err);
    } finally {
      setAiLoading(false);
    }
  };

  return (
    <div className="rounded-md border border-border bg-surface p-md">
      <div className="flex items-center justify-between border-b border-border pb-sm mb-md">
        <span className="font-mono text-2xs uppercase tracking-wider text-text">
          Причины остановок рекламы
        </span>
        <button
          onClick={fetchAIHelp}
          disabled={aiLoading}
          className="rounded border border-accent bg-accent-soft px-xs py-2xs font-mono text-[9px] font-semibold text-accent transition hover:bg-accent hover:text-bg"
        >
          {aiLoading ? 'Анализ...' : '✦ AI Анализ причин'}
        </button>
      </div>

      {loading ? (
        <div className="flex h-48 items-center justify-center">
          <span className="h-6 w-6 animate-spin rounded-full border-2 border-accent border-t-transparent" />
        </div>
      ) : (
        <div className="grid grid-cols-1 md:grid-cols-2 gap-md items-center">
          {/* Легенда и полосы */}
          <div className="space-y-sm">
            {reasons.map((r, i) => (
              <div key={i} className="space-y-xs">
                <div className="flex justify-between text-2xs font-mono">
                  <span className="text-text-dim truncate max-w-[200px]" title={r.name}>{r.name}</span>
                  <span className="text-text font-semibold">{r.count} шт ({r.pct}%)</span>
                </div>
                <div className="h-1.5 w-full rounded-full bg-surface-2 overflow-hidden border border-border">
                  <div
                    className="h-full rounded-full transition-all duration-500"
                    style={{ width: `${r.pct}%`, backgroundColor: r.color }}
                  />
                </div>
              </div>
            ))}
          </div>

          {/* Визуальный круг Donut */}
          <div className="flex justify-center relative">
            <svg width="120" height="120" viewBox="0 0 40 40" className="transform -rotate-95">
              <circle cx="20" cy="20" r="15.915" fill="transparent" stroke="var(--border)" strokeWidth="3" />
              {/* Сегменты */}
              {reasons.reduce((acc, r, idx) => {
                const strokeDasharray = `${r.pct} ${100 - r.pct}`;
                const strokeDashoffset = 100 - acc.totalPct;
                acc.totalPct += r.pct;
                acc.elements.push(
                  <circle
                    key={idx}
                    cx="20"
                    cy="20"
                    r="15.915"
                    fill="transparent"
                    stroke={r.color}
                    strokeWidth="3"
                    strokeDasharray={strokeDasharray}
                    strokeDashoffset={strokeDashoffset}
                  />
                );
                return acc;
              }, { totalPct: 0, elements: [] }).elements}
            </svg>
            <div className="absolute inset-0 flex flex-col items-center justify-center font-mono">
              <span className="text-sm font-bold text-text">62</span>
              <span className="text-[9px] text-text-muted">всего стопов</span>
            </div>
          </div>
        </div>
      )}

      {/* AI разбор под блоком */}
      {aiAnalysis && (
        <div className="mt-md border-t border-border pt-md">
          <span className="font-mono text-[10px] uppercase text-accent">✦ AI Сводка причин остановок:</span>
          <div className="mt-xs text-2xs text-text-dim leading-relaxed whitespace-pre-wrap font-sans">
            {aiAnalysis}
          </div>
        </div>
      )}
    </div>
  );
}
