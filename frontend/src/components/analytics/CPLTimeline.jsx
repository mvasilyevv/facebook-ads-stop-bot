import React, { useState, useEffect } from 'react';
import { getAIAnalysis, getHistoryTimeline } from '../../api';
import { renderMarkdown } from '../../utils/markdown';

// Форматирование даты в формат YYYY-MM-DD без сдвигов часовых поясов
const formatDateISO = (date) => {
  const yyyy = date.getFullYear();
  const mm = String(date.getMonth() + 1).padStart(2, '0');
  const dd = String(date.getDate()).padStart(2, '0');
  return `${yyyy}-${mm}-${dd}`;
};

// Форматирование даты YYYY-MM-DD в красивую русскую запись (например, "12 мая")
const formatRussianDate = (isoString) => {
  if (!isoString) return '';
  const parts = isoString.split('-');
  if (parts.length !== 3) return isoString;
  const day = parseInt(parts[2], 10);
  const monthIdx = parseInt(parts[1], 10) - 1;
  const months = [
    'января', 'февраля', 'марта', 'апреля', 'мая', 'июня',
    'июля', 'августа', 'сентября', 'октября', 'ноября', 'декабря'
  ];
  return `${day} ${months[monthIdx] || ''}`.trim();
};

/**
 * Временная шкала CPL / CPD за период с интерактивным графиком и дрилл-дауном.
 */
export default function CPLTimeline() {
  const [loading, setLoading] = useState(false);
  const [metricMode, setMetricMode] = useState('CPL'); // CPL или CPD
  const [rawData, setRawData] = useState([]);
  const [points, setPoints] = useState([]);
  const [selectedPoint, setSelectedPoint] = useState(null);
  const [aiAnalysis, setAiAnalysis] = useState('');
  const [aiLoading, setAiLoading] = useState(false);

  const fetchData = async () => {
    setLoading(true);
    try {
      const today = new Date();
      const tenDaysAgo = new Date();
      tenDaysAgo.setDate(today.getDate() - 10);
      const date_from = formatDateISO(tenDaysAgo);
      const date_to = formatDateISO(today);

      const data = await getHistoryTimeline({ date_from, date_to });
      setRawData(Array.isArray(data) ? data : []);
    } catch (err) {
      console.error(err);
    } finally {
      setLoading(false);
    }
  };

  useEffect(() => {
    fetchData();
  }, []);

  useEffect(() => {
    const mappedPoints = rawData.map((item) => {
      const formattedDate = formatRussianDate(item.date);
      if (metricMode === 'CPL') {
        const val = parseFloat(Number(item.cpl || 0).toFixed(2));
        return {
          date: formattedDate,
          value: val,
          details: `Зарегистрировано лидов: ${item.leads || 0}, Расход: ${Number(item.spend || 0).toFixed(1)}$`
        };
      } else {
        const val = parseFloat(Number(item.cost_per_deposit || 0).toFixed(2));
        return {
          date: formattedDate,
          value: val,
          details: `Депозитов: ${item.deposits || 0}, Расход: ${Number(item.spend || 0).toFixed(1)}$`
        };
      }
    });
    setPoints(mappedPoints);
    if (mappedPoints.length > 0) {
      setSelectedPoint(mappedPoints[mappedPoints.length - 1]);
    } else {
      setSelectedPoint(null);
    }
  }, [rawData, metricMode]);

  const handlePointClick = (point) => {
    setSelectedPoint(point);
  };

  const fetchAIHelp = async () => {
    setAiLoading(true);
    try {
      const snapshot = {
        metric: metricMode,
        timeline: points.map((p) => ({
          date: p.date,
          value: p.value,
          details: p.details,
        })),
      };
      const data = await getAIAnalysis('cpl_timeline', 'global', true, snapshot);
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
    <div className="panel h-full p-md">
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
              <div 
                className="mt-xs text-2xs text-text-dim leading-relaxed whitespace-pre-wrap font-sans"
                dangerouslySetInnerHTML={{ __html: renderMarkdown(aiAnalysis) }}
              />
            </div>
          )}
        </div>
      ) : null}
    </div>
  );
}
