// Slide-over панель деталей кампании
import { useState, useMemo } from 'react';
import { EventTimeline } from './EventTimeline.jsx';
import { AdDetailPanel } from './AdDetailPanel.jsx';

import { fmt$, fmtN, fmtRoas } from '../../utils/formatters.js';
import { formatTime } from '../../utils/timeUtils.js';

function PanelKPI({ label, value }) {
  return (
    <div className="flex flex-col gap-0.5">
      <span className="text-2xs uppercase tracking-wider text-secondary">{label}</span>
      <span className="font-mono text-sm font-semibold text-primary">{value}</span>
    </div>
  );
}

function CampaignKPIs({ campaign }) {
  if (!campaign) return null;
  return (
    <div className="grid grid-cols-3 gap-3 mb-4">
      <PanelKPI label="Расход" value={fmt$(campaign.spend)} />
      <PanelKPI label="Лиды" value={fmtN(campaign.leads)} />
      <PanelKPI label="Реги" value={fmtN(campaign.registrations)} />
      <PanelKPI label="Депозиты" value={fmtN(campaign.deposits)} />
      <PanelKPI label="CPL" value={fmt$(campaign.cpl)} />
      <PanelKPI label="ROAS" value={fmtRoas(campaign.roas)} />
    </div>
  );
}

function extractUniqueAds(events) {
  const map = new Map();
  for (const e of events) {
    if (!e.fb_ad_id) continue;
    if (!map.has(e.fb_ad_id)) {
      map.set(e.fb_ad_id, {
        fb_ad_id: e.fb_ad_id,
        ad_name: e.ad_name || e.fb_ad_id,
        event_type: e.event_type,
        created_at: e.created_at,
      });
    } else {
      const existing = map.get(e.fb_ad_id);
      if (e.created_at > existing.created_at) {
        existing.event_type = e.event_type;
        existing.created_at = e.created_at;
      }
    }
  }
  return Array.from(map.values());
}

function AdsTable({ ads, onSelect }) {
  if (!ads.length) return null;
  return (
    <div className="mb-4">
      <h3 className="mb-2 text-2xs font-bold uppercase tracking-widest text-muted">
        Объявления
      </h3>
      <div className="overflow-x-auto">
        <table className="w-full text-sm">
          <thead>
            <tr className="border-b border-border bg-elevated/50">
              <th className="text-left px-3 py-2">Объявление</th>
              <th className="text-left px-3 py-2">Тип события</th>
              <th className="text-right px-3 py-2">Последнее</th>
            </tr>
          </thead>
          <tbody>
            {ads.map((ad) => (
              <tr
                key={ad.fb_ad_id}
                className="tr-hover border-b border-border cursor-pointer"
                onClick={() => onSelect(ad.fb_ad_id)}
              >
                <td className="px-3 py-2 max-w-[200px] truncate" title={ad.ad_name}>
                  {ad.ad_name}
                </td>
                <td className="px-3 py-2 text-secondary">{ad.event_type}</td>
                <td className="px-3 py-2 text-right text-2xs text-muted font-mono">
                  {formatTime(ad.created_at)}
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </div>
  );
}

export function CampaignDetailPanel({ campaignName, campaigns, events, onClose }) {
  const [selectedAd, setSelectedAd] = useState(null);

  const campaign = useMemo(
    () => campaigns?.find((c) => c.campaign_name === campaignName),
    [campaigns, campaignName],
  );

  const filteredEvents = useMemo(
    () => (events || []).filter((e) => e.campaign_name === campaignName),
    [events, campaignName],
  );

  const uniqueAds = useMemo(
    () => extractUniqueAds(filteredEvents),
    [filteredEvents],
  );

  if (!campaignName) return null;

  if (selectedAd) {
    return (
      <AdDetailPanel
        fbAdId={selectedAd}
        onClose={() => setSelectedAd(null)}
      />
    );
  }

  return (
    <>
      <div
        className="fixed inset-0 z-40 bg-black/60 animate-fade-in"
        onClick={onClose}
      />
      <div className="fixed inset-y-0 right-0 z-50 w-full max-w-lg bg-surface border-l border-border overflow-y-auto p-5">
        <button className="btn-ghost mb-4 text-sm" onClick={onClose}>
          ← Назад
        </button>
        <h2 className="text-sm font-semibold text-primary mb-4 truncate" title={campaignName}>
          {campaignName}
        </h2>
        <CampaignKPIs campaign={campaign} />
        <AdsTable ads={uniqueAds} onSelect={setSelectedAd} />
        {filteredEvents.length > 0
          ? <EventTimeline events={filteredEvents} />
          : <div className="text-sm text-muted py-2">Нет событий</div>
        }
      </div>
    </>
  );
}
