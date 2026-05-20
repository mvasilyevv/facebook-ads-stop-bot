import React, { useState, useEffect } from 'react';
import { getAIAnalysis } from '../../api';
import { renderMarkdown } from '../../utils/markdown';

/**
 * Лента принятых ботом решений с фильтрацией по офферу/правилу и AI сводкой.
 */
export default function DecisionsHistoryFeed() {
  const [loading, setLoading] = useState(false);
  const [filterOffer, setFilterOffer] = useState('ALL');
  const [events, setEvents] = useState([]);
  const [aiAnalysis, setAiAnalysis] = useState('');
  const [aiLoading, setAiLoading] = useState(false);

  const fetchData = async () => {
    setLoading(true);
    try {
      const mockEvents = [
        { id: 1, type: 'stop', offer: 'Casino_CZ', rule: 'CPL > 120%', adId: 'fb_ad_9921', time: '14:25:12', date: '19 мая', status: 'SUCCESS' },
        { id: 2, type: 'stop', offer: 'Bet_PL', rule: 'CPC > 30%', adId: 'fb_ad_4021', time: '11:15:02', date: '19.05', status: 'SUCCESS' },
        { id: 3, type: 'enable_recommendation', offer: 'Casino_CZ', rule: 'CPL Normal', adId: 'fb_ad_8830', time: '09:05:43', date: '19 мая', status: 'CLAIMED' },
        { id: 4, type: 'stop', offer: 'Casino_CZ', rule: 'Spend > 70% CPA', adId: 'fb_ad_1223', time: '18:43:00', date: '18.05', status: 'SUCCESS' },
        { id: 5, type: 'stop', offer: 'Poker_BR', rule: '5+ Regs No Dep', adId: 'fb_ad_7720', time: '15:20:10', date: '18 мая', status: 'SUCCESS' }
      ];
      setEvents(mockEvents);
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
      const data = await getAIAnalysis('history', 'global', true);
      setAiAnalysis(data.content);
    } catch (err) {
      console.error(err);
    } finally {
      setAiLoading(false);
    }
  };

  const filteredEvents = filterOffer === 'ALL'
    ? events
    : events.filter((e) => e.offer === filterOffer);

  return (
    <div className="rounded-md border border-border bg-surface p-md">
      {/* Шапка */}
      <div className="flex items-center justify-between border-b border-border pb-sm mb-md">
        <div className="flex items-center gap-md">
          <span className="font-mono text-2xs uppercase tracking-wider text-text">
            История принятых решений
          </span>
          {/* Фильтр */}
          <select
            value={filterOffer}
            onChange={(e) => setFilterOffer(e.target.value)}
            className="rounded border border-border bg-surface-2 px-xs py-2xs font-mono text-[10px] text-text-dim focus:border-accent outline-none"
          >
            <option value="ALL">Все офферы</option>
            <option value="Casino_CZ">Casino_CZ</option>
            <option value="Bet_PL">Bet_PL</option>
            <option value="Poker_BR">Poker_BR</option>
          </select>
        </div>

        <button
          onClick={fetchAIHelp}
          disabled={aiLoading}
          className="rounded border border-accent bg-accent-soft px-xs py-2xs font-mono text-[9px] font-semibold text-accent transition hover:bg-accent hover:text-bg"
        >
          {aiLoading ? 'Анализ...' : '✦ AI Анализ действий'}
        </button>
      </div>

      {loading ? (
        <div className="flex h-48 items-center justify-center">
          <span className="h-6 w-6 animate-spin rounded-full border-2 border-accent border-t-transparent" />
        </div>
      ) : (
        <div className="space-y-xs">
          {filteredEvents.map((ev) => (
            <div
              key={ev.id}
              className="flex items-center justify-between rounded border border-border/40 bg-surface-2/40 px-sm py-xs font-mono text-2xs transition hover:border-border hover:bg-surface-2"
            >
              <div className="flex items-center gap-sm">
                <span className={`h-1.5 w-1.5 rounded-full ${ev.type === 'stop' ? 'bg-stop' : 'bg-ok'}`} />
                <span className="text-text font-medium">[{ev.type.toUpperCase()}]</span>
                <span className="text-text-dim">Оффер: <span className="text-text">{ev.offer}</span></span>
                <span className="text-text-muted">|</span>
                <span className="text-text-dim">Правило: <span className="text-text">{ev.rule}</span></span>
                <span className="text-text-muted">|</span>
                <span className="text-text-muted">Ad: {ev.adId}</span>
              </div>

              <div className="flex items-center gap-md">
                <span className="text-text-muted">{ev.date} {ev.time}</span>
                <span className={`rounded px-2xs py-[1px] text-[9px] font-semibold ${ev.status === 'SUCCESS' ? 'bg-ok/10 text-ok' : 'bg-info/10 text-info'}`}>
                  {ev.status}
                </span>
              </div>
            </div>
          ))}

          {filteredEvents.length === 0 && (
            <div className="flex h-24 items-center justify-center text-text-muted font-mono text-2xs">
              Нет событий для отображения
            </div>
          )}
        </div>
      )}

      {/* AI разбор под лентой */}
      {aiAnalysis && (
        <div className="mt-md border-t border-border pt-md">
          <span className="font-mono text-[10px] uppercase text-accent">✦ AI Сводка действий:</span>
          <div
            className="mt-xs text-2xs text-text-dim leading-relaxed font-sans"
            dangerouslySetInnerHTML={{ __html: renderMarkdown(aiAnalysis) }}
          />
        </div>
      )}
    </div>
  );
}
