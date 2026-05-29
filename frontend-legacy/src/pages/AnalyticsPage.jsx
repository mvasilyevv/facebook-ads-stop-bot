import React from 'react';
import AlertsHeatmap from '../components/analytics/AlertsHeatmap.jsx';
import StopReasonsDonut from '../components/analytics/StopReasonsDonut.jsx';
import CPLTimeline from '../components/analytics/CPLTimeline.jsx';
import DecisionsHistoryFeed from '../components/analytics/DecisionsHistoryFeed.jsx';
import AnalyticsKPIStrip from '../components/analytics/AnalyticsKPIStrip.jsx';
import OfferComparisonTable from '../components/analytics/OfferComparisonTable.jsx';

/**
 * Аналитика: KPI за 7 дней, сравнение офферов, причины остановок,
 * таймлайн CPL/CPD, тепловая карта алертов и журнал решений.
 */
export default function AnalyticsPage() {
  return (
    <div className="space-y-md pb-xl">
      {/* Шапка страницы */}
      <div className="flex flex-col gap-2xs">
        <h1 className="text-xl font-bold uppercase tracking-wider text-text">
          ✦ Аналитический центр
        </h1>
        <p className="text-xs text-text-dim">
          Сводка за 7 дней, сравнение офферов и разбор причин остановок — чтобы видеть, куда уходит бюджет и где он окупается.
        </p>
      </div>

      {/* KPI-стрип: быстрый снимок за 7 дней */}
      <AnalyticsKPIStrip />

      {/* Сравнение офферов: главный блок страницы */}
      <OfferComparisonTable />

      {/* Детализирующие виджеты */}
      <div className="card-grid grid-cols-1 lg:grid-cols-2">
        <CPLTimeline />
        <StopReasonsDonut />
        <AlertsHeatmap />
        <DecisionsHistoryFeed />
      </div>
    </div>
  );
}
