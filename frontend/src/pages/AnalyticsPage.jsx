import React from 'react';
import AlertsHeatmap from '../components/analytics/AlertsHeatmap.jsx';
import StopReasonsDonut from '../components/analytics/StopReasonsDonut.jsx';
import CPLTimeline from '../components/analytics/CPLTimeline.jsx';
import DecisionsHistoryFeed from '../components/analytics/DecisionsHistoryFeed.jsx';

/**
 * Страница Аналитики, объединяющая тепловую карту, причины остановок, таймлайн CPL и лог принятых решений.
 */
export default function AnalyticsPage() {
  return (
    <div className="space-y-md pb-xl">
      {/* Шапка страницы */}
      <div className="flex flex-col gap-xs">
        <h1 className="text-xl font-bold uppercase tracking-wider text-text">
          ✦ AI Аналитический Центр
        </h1>
        <p className="text-xs text-text-dim">
          Интеллектуальный разбор закупки трафика, выявление паттернов зацепки и автоматизированный аудит стоп-правил.
        </p>
      </div>

      {/* Grid сетка 2x2 */}
      <div className="grid grid-cols-1 gap-md lg:grid-cols-2">
        <CPLTimeline />
        <StopReasonsDonut />
        <AlertsHeatmap />
        <DecisionsHistoryFeed />
      </div>
    </div>
  );
}
