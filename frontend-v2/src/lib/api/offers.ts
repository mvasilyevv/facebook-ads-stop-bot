/**
 * Hooks для /offers-страницы.
 */

import { useQuery, useMutation, useQueryClient } from "@tanstack/react-query";
import { apiClient } from "./client";
import type { Offer, OfferCompareRow, OfferRules } from "@/lib/types/api";

const KEYS = {
  list: (include_inactive?: boolean) => ["offers", "list", include_inactive] as const,
  compare: (days: number) => ["offers", "compare", days] as const,
  rules: (id: string) => ["offers", id, "rules"] as const,
};

export function useOffers(include_inactive = false) {
  return useQuery({
    queryKey: KEYS.list(include_inactive),
    queryFn: () => apiClient.get<Offer[]>("/offers", { include_inactive }),
  });
}

export function useOffersCompare(days = 7) {
  return useQuery({
    queryKey: KEYS.compare(days),
    queryFn: () => apiClient.get<OfferCompareRow[]>("/offers/compare", { days }),
  });
}

export function useOfferRules(id: string | null) {
  return useQuery({
    queryKey: KEYS.rules(id ?? ""),
    queryFn: () => apiClient.get<OfferRules>(`/offers/${id}/rules`),
    enabled: !!id,
  });
}

export function useCreateOffer() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (data: { code: string; name: string; vertical?: string | null }) =>
      apiClient.post<Offer>("/offers", data),
    onSuccess: () => qc.invalidateQueries({ queryKey: ["offers"] }),
  });
}

export function useUpdateOffer() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: ({ id, data }: { id: string; data: Partial<Offer> }) =>
      apiClient.put<Offer>(`/offers/${id}`, data),
    onSuccess: () => qc.invalidateQueries({ queryKey: ["offers"] }),
  });
}

export function useDeleteOffer() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (id: string) => apiClient.delete<void>(`/offers/${id}`),
    onSuccess: () => qc.invalidateQueries({ queryKey: ["offers"] }),
  });
}

export function useUpsertOfferRules() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: ({ id, data }: { id: string; data: Partial<OfferRules> }) =>
      apiClient.put<OfferRules>(`/offers/${id}/rules`, data),
    onSuccess: (_, vars) =>
      qc.invalidateQueries({ queryKey: KEYS.rules(vars.id) }),
  });
}

export const offersKeys = KEYS;
