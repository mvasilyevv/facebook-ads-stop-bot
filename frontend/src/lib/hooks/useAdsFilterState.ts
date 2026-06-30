/**
 * useAdsFilterState — состояние фильтров AdsPage (без зависимости от загруженных
 * строк — selectedStates нужен ДО fetch'а, как server-side query-параметр).
 *
 * Выделено из routes/ads/index.tsx (god-component >500 строк). Производные данные
 * (filtered rows + option-списки) — отдельный хук useFilteredAdsRows, вызывается
 * ПОСЛЕ useAds (нужны загруженные строки).
 */
import { useCallback, useState } from "react";
import type { AlertState } from "@fb/shared";

export interface AdsFilterState {
  search: string;
  selectedStates: Set<AlertState>;
  selectedOffers: Set<string>;
  selectedAccounts: Set<string>;
  selectedCampaigns: Set<string>;
  selectedAdsets: Set<string>;
}

function toggleInSet<T>(set: Set<T>, value: T): Set<T> {
  const next = new Set(set);
  if (next.has(value)) {
    next.delete(value);
  } else {
    next.add(value);
  }
  return next;
}

export function useAdsFilterState(initialStates: Set<AlertState>) {
  const [filters, setFilters] = useState<AdsFilterState>(() => ({
    search: "",
    selectedStates: initialStates,
    selectedOffers: new Set<string>(),
    selectedAccounts: new Set<string>(),
    selectedCampaigns: new Set<string>(),
    selectedAdsets: new Set<string>(),
  }));

  const setSearch = useCallback((v: string) => {
    setFilters((p) => ({ ...p, search: v }));
  }, []);

  const toggleState = useCallback((s: AlertState) => {
    setFilters((p) => ({ ...p, selectedStates: toggleInSet(p.selectedStates, s) }));
  }, []);

  const toggleOffer = useCallback((o: string) => {
    setFilters((p) => ({ ...p, selectedOffers: toggleInSet(p.selectedOffers, o) }));
  }, []);

  const toggleAccount = useCallback((a: string) => {
    setFilters((p) => ({ ...p, selectedAccounts: toggleInSet(p.selectedAccounts, a) }));
  }, []);

  const toggleCampaign = useCallback((c: string) => {
    setFilters((p) => ({ ...p, selectedCampaigns: toggleInSet(p.selectedCampaigns, c) }));
  }, []);

  const toggleAdset = useCallback((a: string) => {
    setFilters((p) => ({ ...p, selectedAdsets: toggleInSet(p.selectedAdsets, a) }));
  }, []);

  const clearAll = useCallback(() => {
    setFilters({
      search: "",
      selectedStates: new Set(),
      selectedOffers: new Set(),
      selectedAccounts: new Set(),
      selectedCampaigns: new Set(),
      selectedAdsets: new Set(),
    });
  }, []);

  return {
    filters,
    setSearch,
    toggleState,
    toggleOffer,
    toggleAccount,
    toggleCampaign,
    toggleAdset,
    clearAll,
  };
}
