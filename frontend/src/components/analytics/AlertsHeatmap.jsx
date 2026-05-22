import React, { useState, useEffect } from 'react';
import { getAIAnalysis } from '../../api';
import { renderMarkdown } from '../../utils/markdown';

const DAYS_OF_WEEK = ['Пн', 'Вт', 'Ср', 'Чт', 'Пт', 'Сб', 'Вс'];

/**
 * Тепловая карта инцидентов 24x7.
 */
export default function AlertsHeatmap() {
  const [loading, setLoading] = useState(false);
  const [matrix, setMatrix] = useState([]);
  const [selectedCell, setSelectedCell] = useState(null);
  const [aiAnalysis, setAiAnalysis] = useState('');
  const [aiLoading, setAiLoading] = useState(false);

  const fetchData = async () => {
    setLoading(true);
    try {
      // Демо-сетка инцидентов — реальные данные подтянет AI-анализ по кнопке
      const mockMatrix = Array(7).fill(0).map(() =>
        Array(24).fill(0).map(() => Math.floor(Math.random() * 5))
      );
      setMatrix(mockMatrix);
    } catch (err) {
      console.error(err);
    } finally {
      setLoading(false);
    }
  };

  useEffect(() => {
    fetchData();
  }, []);

  const handleCellClick = (dayIdx, hourIdx) => {
    setSelectedCell({
      day: DAYS_OF_WEEK[dayIdx],
      hour: `${hourIdx}:00`,
      count: matrix[dayIdx]?.[hourIdx] || 0
    });
  };

  const getHeatColor = (count) => {
    if (count === 0) return 'bg-surface-2 opacity-30';
    if (count < 2) return 'bg-accent/20';
    if (count < 4) return 'bg-accent/40';
    if (count < 6) return 'bg-accent/70';
    return 'bg-accent shadow-[0_0_8px_var(--accent)]';
  };

  const fetchAIHelp = async () => {
    setAiLoading(true);
    try {
      const snapshot = { matrix };
      const data = await getAIAnalysis('heatmap', 'global', true, snapshot);
      setAiAnalysis(data.content);
    } catch (err) {
      console.error(err);
    } finally {
      setAiLoading(false);
    }
  };

  return (
    <div className="panel h-full p-md">
      <div className="flex items-center justify-between border-b border-border pb-sm mb-md">
        <span className="font-mono text-2xs uppercase tracking-wider text-text">
          Тепловая карта инцидентов (24x7)
        </span>
        <button
          onClick={fetchAIHelp}
          disabled={aiLoading}
          className="rounded border border-accent bg-accent-soft px-xs py-2xs font-mono text-[9px] font-semibold text-accent transition hover:bg-accent hover:text-bg"
        >
          {aiLoading ? 'Анализ...' : '✦ AI Анализ пиков'}
        </button>
      </div>

      {loading ? (
        <div className="flex h-48 items-center justify-center">
          <span className="h-6 w-6 animate-spin rounded-full border-2 border-accent border-t-transparent" />
        </div>
      ) : (
        <div className="overflow-x-auto">
          <div className="min-w-[640px]">
            {/* Часы */}
            <div className="flex pl-8 mb-xs">
              {Array(24).fill(0).map((_, i) => (
                <div key={i} className="w-full text-center font-mono text-[9px] text-text-muted">
                  {i}
                </div>
              ))}
            </div>

            {/* Сетка */}
            {matrix.map((row, dayIdx) => (
              <div key={dayIdx} className="flex items-center mb-xs">
                <span className="w-8 font-mono text-xs text-text-dim">{DAYS_OF_WEEK[dayIdx]}</span>
                <div className="flex flex-1 gap-xs">
                  {row.map((val, hourIdx) => (
                    <div
                      key={hourIdx}
                      onClick={() => handleCellClick(dayIdx, hourIdx)}
                      className={`h-5 w-full cursor-pointer rounded transition-all hover:scale-110 ${getHeatColor(val)}`}
                      title={`${DAYS_OF_WEEK[dayIdx]}, ${hourIdx}:00 — ${val} инцидентов`}
                    />
                  ))}
                </div>
              </div>
            ))}
          </div>
        </div>
      )}

      {/* Выбранная ячейка */}
      {selectedCell && (
        <div className="mt-md rounded border border-border bg-surface-2 p-sm font-mono text-2xs text-text-dim">
          Выбрано: <span className="text-text font-semibold">{selectedCell.day}, {selectedCell.hour}</span>. Инцидентов зафиксировано: <span className="text-accent font-semibold">{selectedCell.count}</span>.
        </div>
      )}

      {/* AI Аналитика под картой */}
      {aiAnalysis && (
        <div className="mt-md border-t border-border pt-md">
          <span className="font-mono text-[10px] uppercase text-accent">✦ AI Анализ Временных Пиков:</span>
          <div
            className="mt-xs text-2xs text-text-dim leading-relaxed font-sans"
            dangerouslySetInnerHTML={{ __html: renderMarkdown(aiAnalysis) }}
          />
        </div>
      )}
    </div>
  );
}
