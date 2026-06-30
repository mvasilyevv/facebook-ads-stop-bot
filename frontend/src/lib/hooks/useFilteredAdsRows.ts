/**
 * useFilteredAdsRows — клиентская фильтрация/сортировка строк AdsPage + derived
 * option-списки (offer/account/campaign/adset) из загруженных строк.
 *
 * Выделено из routes/ads/index.tsx (god-component >500 строк). Вызывается ПОСЛЕ
 * useAds (нужны реально загруженные строки) с состоянием из useAdsFilterState.
 */
import { useMemo } from "react";
import type { AdSnapshot } from "@fb/shared";
import { adAccountId } from "@/components/domain/ads/adHelpers";
import type { AdsFilterState } from "./useAdsFilterState";

function uniqueSorted(values: Iterable<string | null | undefined>): string[] {
  const set = new Set<string>();
  for (const v of values) if (v) set.add(v);
  return [...set].sort();
}

export function useFilteredAdsRows(allRows: AdSnapshot[], filters: AdsFilterState) {
  const offerOptions = useMemo(() => uniqueSorted(allRows.map((r) => r.offer_code)), [allRows]);
  const accountOptions = useMemo(
    () => uniqueSorted(allRows.map((r) => adAccountId(r))),
    [allRows],
  );
  const campaignOptions = useMemo(
    () => uniqueSorted(allRows.map((r) => r.campaign_name)),
    [allRows],
  );
  const adsetOptions = useMemo(() => uniqueSorted(allRows.map((r) => r.adset_name)), [allRows]);

  // Клиентская фильтрация + сортировка по spend desc (как в эталоне).
  const rows = useMemo<AdSnapshot[]>(() => {
    const q = filters.search.trim().toLowerCase();
    const { selectedOffers: offers, selectedAccounts: accounts, selectedCampaigns: campaigns, selectedAdsets: adsets } = filters;
    const out = allRows.filter((r) => {
      if (q) {
        const hit =
          r.ad_name.toLowerCase().includes(q) ||
          r.fb_ad_id.includes(q) ||
          (r.offer_code?.toLowerCase().includes(q) ?? false);
        if (!hit) return false;
      }
      if (offers.size > 0 && !(r.offer_code && offers.has(r.offer_code))) return false;
      if (accounts.size > 0) {
        const acc = adAccountId(r);
        if (!(acc && accounts.has(acc))) return false;
      }
      if (campaigns.size > 0 && !(r.campaign_name && campaigns.has(r.campaign_name))) return false;
      if (adsets.size > 0 && !(r.adset_name && adsets.has(r.adset_name))) return false;
      return true;
    });
    out.sort((a, b) => {
      const sa = Number.parseFloat(a.metrics?.spend ?? "0") || 0;
      const sb = Number.parseFloat(b.metrics?.spend ?? "0") || 0;
      return sb - sa;
    });
    return out;
  }, [allRows, filters]);

  return { rows, offerOptions, accountOptions, campaignOptions, adsetOptions };
}
