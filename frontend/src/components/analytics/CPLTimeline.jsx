import React, { useState, useEffect } from 'react';
import { getAIAnalysis } from '../../api';

/**
 * Временная шкала CPL / CPD за период с интерактивным графиком и дрилл-дауном.
 */
export default function CPLTimeline() {
  const [loading, setLoading] = useState(false);
  const [metricMode, setMetricMode] = useState('CPL'); // CPL или CPD
  const [points, setPoints] = useState([]);
  const [selectedPoint, setSelectedPoint] = useState(null);
  const [aiAnalysis, setAiAnalysis] = useState('');
  const [aiLoading, setAiLoading] = useState(false);

  const fetchData = async () => {
    setLoading(true);
    try {
      const data = await getAIAnalysis('cpl_timeline', 'global', false);
      
      // Демонстрационные исторические данные за 8 дней
      const cplPoints = [
        { date: '12 мая', value: 4.2, details: 'Зарегистрировано: 48 лидов, CPA = 5.0$' },
        { date: '13 мая', value: 4.8, details: 'Зарегистрировано: 36 лидов, CPA = 5.0$' },
        { date: '14 мая', value: 5.5, details: 'Зарегистрировано: 28 лидов (Превышение лимита!), CPA = 5.0$' },
        { date: '15 мая', value: 3.9, details: 'Зарегистрировано: 54 лида, CPA = 5.0$' },
        { date: '16 мая', value: 3.2, details: 'Зарегистрировано: 62 лида (Отличные показатели), CPA = 5.0$' },
        { date: '17 мая', value: 4.0, details: 'Зарегистрировано: 41 лид, CPA = 5.0$' },
        { date: '18 мая', value: 4.7, details: 'Зарегистрировано: 33 лида, CPA = 5.0$' },
        { date: '19 мая', value: 4.4, details: 'Зарегистрировано: 45 лидов, CPA = 5.0$' }
      ];

      const cpdPoints = [
        { date: '12 мая', value: 22.0, details: 'Депозитов: 12, Стоимость: 264$' },
        { date: '13 мая', value: 24.5, details: 'Депозитов: 8, Стоимость: 196$' },
        { date: '14 мая', value: 31.0, details: 'Депозитов: 5 (Высокая цена депа!), Стоимость: 155$' },
        { date: '15 мая', value: 19.5, details: 'Депозитов: 16, Стоимость: 312$' },
        { date: '16 мая', value: 18.0, details: 'Депозитов: 20 (Отличный конверт), Стоимость: 360$' },
        { date: '17 мая', value: 21.0, details: 'Депозитов: 11, Стоимость: 231$' },
        { date: '18 мая', value: 26.8, details: 'Депозитов: 7, Стоимость: 187$' },
        { date: '19 мая', value: 23.5, details: 'Депозитов: 10, Стоимость: 235$' }
      ];

      setPoints(metricMode === 'CPL' ? cplPoints : cpdPoints);
      setAiAnalysis(data.content);
    } catch (err) {
      console.error(err);
    } finally {
      setLoading(false);
    }
  };

  useEffect(() => {
    fetchData();
  }, [metricMode]);

  const handlePointClick = (point) => {
    setSelectedPoint(point);
  };

  const fetchAIHelp = async () => {
    setAiLoading(true);
    try {
      const data = await getAIAnalysis('cpl_timeline', 'global', true);
      setAiAnalysis(data.content);
    } catch (err) {
      console.error(err);
    } finally {
      setAiLoading(false);
    }
  };

  const maxVal = Math.max(...points.map((p) => p.value), 10);
  const minVal = 0;
  const range = maxVal - minVal;

  const getX = (idx) => points.length > 1 ? (idx / (points.length - 1)) * 100 : 50;
  const getY = (val) => 40 - ((val - minVal) / range) * 35;

  const pathD = points.map((p, idx) => `${idx === 0 ? 'M' : 'L'} ${getX(idx)} ${getY(p.value)}`).join(' ');

  return (
    <div className="rounded-md border border-border bg-surface p-md">
      {/* Шапка */}
      <div className="flex items-center justify-between border-b border-border pb-sm mb-md">
        <div className="flex items-center gap-md">
          <span className="font-mono text-2xs uppercase tracking-wider text-text">
            Динамика стоимости CPL / CPD
          </span>
          <div className="flex rounded border border-border bg-surface-2 p-[2px]">
            <button
              onClick={() => setMetricMode('CPL')}
              className={`rounded px-xs py-2xs font-mono text-[9px] font-semibold transition ${metricMode === 'CPL' ? 'bg-accent text-bg' : 'text-text-dim hover:text-text'}`}
            >
              CPL
            </button>
            <button
              onClick={() => setMetricMode('CPD')}
              className={`rounded px-xs py-2xs font-mono text-[9px] font-semibold transition ${metricMode === 'CPD' ? 'bg-accent text-bg' : 'text-text-dim hover:text-text'}`}
            >
              CPD
            </button>
          </div>
        </div>

        <button
          onClick={fetchAIHelp}
          disabled={aiLoading}
          className="rounded border border-accent bg-accent-soft px-xs py-2xs font-mono text-[9px] font-semibold text-accent transition hover:bg-accent hover:text-bg"
        >
          {aiLoading ? 'Анализ...' : '✦ AI Анализ тренда'}
        </button>
      </div>

      {loading ? (
        <div className="flex h-48 items-center justify-center">
          <span className="h-6 w-6 animate-spin rounded-full border-2 border-accent border-t-transparent" />
        </div>
      ) : points.length > 0 ? (
        <div>
          <div className="relative h-44 w-full">
            <svg className="h-full w-full overflow-visible" viewBox="0 0 100 40" preserveAspectRatio="none">
              <line x1="0" y1="40" x2="100" y2="40" stroke="var(--border)" strokeWidth="0.5" />
              {/* Линия тренда */}
              <path
                d={pathD}
                fill="none"
                stroke="var(--accent)"
                strokeWidth="1.2"
                strokeLinecap="round"
                strokeLinejoin="round"
              />
              {/* Точки кликабельные */}
              {points.map((p, idx) => (
                <circle
                  key={idx}
                  cx={getX(idx)}
                  cy={getY(p.value)}
                  r="1.8"
                  onClick={() => handlePointClick(p)}
                  className="cursor-pointer fill-surface stroke-accent hover:r-2.5 transition-all"
                  strokeWidth="0.8"
                />
              ))}
            </svg>
          </div>

          <div className="mt-sm flex justify-between font-mono text-[9px] text-text-muted">
            {points.map((p, idx) => (
              <span key={idx}>{p.date}</span>
            ))}
          </div>

          {/* Дрилл-даун блок */}
          {selectedPoint && (
            <div className="mt-md rounded border border-border bg-surface-2 p-sm font-mono text-2xs">
              <span className="text-accent font-semibold">{selectedPoint.date}: </span>
              <span className="text-text">{metricMode} = {selectedPoint.value}$ </span>
              <span className="text-text-dim">({selectedPoint.details})</span>
            </div>
          )}

          {/* AI Анализ тренда */}
          {aiAnalysis && (
            <div className="mt-md border-t border-border pt-md">
              <span className="font-mono text-[10px] uppercase text-accent">✦ AI Тренд-Аналитика:</span>
              <div className="mt-xs text-2xs text-text-dim leading-relaxed whitespace-pre-wrap font-sans">
                {aiAnalysis}
              </div>
            </div>
          )}
        </div>
      ) : null}
    </div>
  );
}
