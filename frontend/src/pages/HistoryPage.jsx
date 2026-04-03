// Страница «История заливов»
import { useState, useEffect, useCallback, lazy, Suspense } from 'react';
import { useDebouncedValue } from '../hooks/useDebouncedValue.js';
import {
  getOffers,
  getHistorySummary,
  getHistoryTimeline,
  getHistoryCampaigns,
  getHistoryEvents,
  getHistoryOffers,
} from '../api.js';
import { HistoryFilters } from '../components/history/HistoryFilters.jsx';
import { HistoryKPIStrip } from '../components/history/HistoryKPIStrip.jsx';
import { HistoryCampaignTable } from '../components/history/HistoryCampaignTable.jsx';
import { HistoryOffersTable } from '../components/history/HistoryOffersTable.jsx';
import { EventTimeline } from '../components/history/EventTimeline.jsx';
import { CampaignDetailPanel } from '../components/history/CampaignDetailPanel.jsx';

const SpendTrendChart = lazy(() => import('../components/history/SpendTrendChart.jsx'));
const MetricsTrendChart = lazy(() => import('../components/history/MetricsTrendChart.jsx'));

function ChartFallback() {
  return <div className="h-[250px] animate-pulse bg-elevated rounded" />;
}

function defaultFilters() {
  const end = new Date();
  const start = new Date();
  start.setDate(start.getDate() - 7);
  return {
    dateFrom: start.toISOString().slice(0, 10),
    dateTo: end.toISOString().slice(0, 10),
    offerCodes: [],
    campaignNames: [],
  };
}

function buildParams(filters) {
  return {
    date_from: filters.dateFrom,
    date_to: filters.dateTo,
    // Backend принимает один offer_code — передаём первый из массива
    offer_code: filters.offerCodes?.[0] || undefined,
    campaign_name: filters.campaignNames?.[0] || undefined,
  };
}

export default function HistoryPage() {
  const [filters, setFilters] = useState(defaultFilters);
  const [offers, setOffers] = useState([]);
  const [summary, setSummary] = useState(null);
  const [timeline, setTimeline] = useState([]);
  const [campaigns, setCampaigns] = useState([]);
  const [events, setEvents] = useState([]);
  const [offersStats, setOffersStats] = useState([]);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState(null);
  const [selectedCampaign, setSelectedCampaign] = useState(null);
  const [eventsPage, setEventsPage] = useState(1);

  const debouncedFilters = useDebouncedValue(filters, 400);

  // Загрузка офферов при маунте
  useEffect(() => {
    getOffers().then(setOffers).catch(() => setOffers([]));
  }, []);

  // Загрузка данных при изменении фильтров
  const loadData = useCallback(async (f) => {
    setLoading(true);
    setError(null);
    const params = buildParams(f);
    try {
      const [sumRes, tlRes, campRes, evRes, offRes] = await Promise.all([
        getHistorySummary(params).catch(() => null),
        getHistoryTimeline(params).catch(() => []),
        getHistoryCampaigns(params).catch(() => []),
        getHistoryEvents({ ...params, limit: 50 }).catch(() => []),
        getHistoryOffers(params).catch(() => []),
      ]);
      setSummary(sumRes);
      setTimeline(Array.isArray(tlRes) ? tlRes : []);
      setCampaigns(Array.isArray(campRes) ? campRes : []);
      setEvents(evRes?.items ?? (Array.isArray(evRes) ? evRes : []));
      setOffersStats(Array.isArray(offRes) ? offRes : []);
      setEventsPage(1);
    } catch (e) {
      setError(e.message);
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    void loadData(debouncedFilters);
  }, [debouncedFilters, loadData]);

  // Подгрузка событий
  const handleLoadMoreEvents = useCallback(async () => {
    const nextPage = eventsPage + 1;
    const params = buildParams(filters);
    try {
      const more = await getHistoryEvents({
        ...params,
        limit: 50,
        offset: eventsPage * 50,
      });
      const items = more?.items ?? (Array.isArray(more) ? more : []);
      if (items.length) {
        setEvents((prev) => [...prev, ...items]);
        setEventsPage(nextPage);
      }
    } catch {
      /* тихо игнорируем */
    }
  }, [filters, eventsPage]);

  const campaignNames = campaigns.map((c) => c.campaign_name);

  return (
    <div className="space-y-md">
      <h1 className="text-lg font-semibold text-primary">История заливов</h1>

      {error && (
        <div className="rounded-md bg-danger-muted border border-danger/30 px-4 py-3 text-sm text-danger">
          {error}
          <button onClick={() => setError(null)} className="ml-3 text-danger/60 hover:text-danger">✕</button>
        </div>
      )}

      <HistoryFilters
        filters={filters}
        onChange={setFilters}
        offers={offers}
        campaigns={campaignNames}
      />

      {loading && (
        <div className="py-8 text-center text-sm text-muted">Загрузка…</div>
      )}

      {!loading && (
        <>
          <HistoryKPIStrip summary={summary} />

          <div className="panel p-4">
            <HistoryOffersTable data={offersStats} />
          </div>

          <div className="grid grid-cols-1 lg:grid-cols-2 gap-md">
            <div className="panel p-4">
              <Suspense fallback={<ChartFallback />}>
                <SpendTrendChart data={timeline} />
              </Suspense>
            </div>
            <div className="panel p-4">
              <Suspense fallback={<ChartFallback />}>
                <MetricsTrendChart data={timeline} />
              </Suspense>
            </div>
          </div>

          <div className="panel p-4">
            <HistoryCampaignTable
              data={campaigns}
              onSelect={setSelectedCampaign}
            />
          </div>

          <div className="panel p-4">
            <EventTimeline
              events={events}
              onLoadMore={handleLoadMoreEvents}
            />
          </div>

          {selectedCampaign && (
            <CampaignDetailPanel
              campaignName={selectedCampaign}
              campaigns={campaigns}
              events={events}
              onClose={() => setSelectedCampaign(null)}
            />
          )}
        </>
      )}
    </div>
  );
}
