/**
 * API-хуки для Offers-страницы.
 *
 * Эндпоинты:
 *   GET  /api/offers                      → Offer[]
 *   GET  /api/offers/compare?days=N       → OfferCompareRow[]
 *   POST /api/offers                      → Offer
 *   PUT  /api/offers/{id}                 → Offer
 *   DELETE /api/offers/{id}               → 204
 *   GET  /api/offers/{id}/rules           → OfferRules
 *   PUT  /api/offers/{id}/rules           → OfferRules
 *   GET  /api/offers/rules/preview        → RulePreviewOut
 */

import { useQuery, useMutation, useQueryClient } from "@tanstack/react-query";
import { apiGet, apiSend } from "./client";
import type { Offer, OfferRules } from "@fb/shared";
import type { components } from "@fb/shared/api/generated";

type OfferCompareRow = components["schemas"]["OfferCompareRow"];
// RulePreviewOut — генерируется из OpenAPI; если тип не экспортирован, используем unknown.
type RulePreviewOut = Record<string, unknown>;

// ─── Список офферов ───────────────────────────────────────────────────────────

export function useOffers(includeInactive?: boolean) {
  return useQuery<Offer[]>({
    queryKey: ["offers", { includeInactive }],
    queryFn: ({ signal }) =>
      apiGet<Offer[]>("/offers", includeInactive ? { include_inactive: true } : undefined, signal),
    staleTime: 30_000,
  });
}

// ─── Сравнение офферов ────────────────────────────────────────────────────────

export function useOffersCompare(days?: number) {
  return useQuery<OfferCompareRow[]>({
    queryKey: ["offers", "compare", days],
    queryFn: ({ signal }) =>
      apiGet<OfferCompareRow[]>("/offers/compare", days ? { days } : undefined, signal),
    staleTime: 60_000,
  });
}

// ─── Создание оффера ──────────────────────────────────────────────────────────

interface OfferCreateIn {
  code: string;
  name: string;
  vertical?: string;
  is_active?: boolean;
  /** Мульти-кабинет: кабинеты оффера (числовые ID без act_), минимум 1. */
  ad_account_ids: string[];
}

export function useCreateOffer() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (data: OfferCreateIn) => apiSend<Offer>("POST", "/offers", data),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ["offers"] });
    },
  });
}

// ─── Обновление оффера ────────────────────────────────────────────────────────

export function useUpdateOffer(offerId: string) {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (data: Partial<OfferCreateIn>) =>
      apiSend<Offer>("PUT", `/offers/${offerId}`, data),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ["offers"] });
    },
  });
}

// ─── Удаление оффера (soft) ───────────────────────────────────────────────────

export function useDeleteOffer() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (offerId: string) => apiSend<null>("DELETE", `/offers/${offerId}`),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ["offers"] });
    },
  });
}

// ─── Правила оффера ───────────────────────────────────────────────────────────

export function useOfferRules(offerId: string) {
  return useQuery<OfferRules>({
    queryKey: ["offers", offerId, "rules"],
    queryFn: ({ signal }) => apiGet<OfferRules>(`/offers/${offerId}/rules`, undefined, signal),
    enabled: !!offerId,
    staleTime: 30_000,
  });
}

export function useUpdateOfferRules(offerId: string) {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (data: Partial<OfferRules>) =>
      apiSend<OfferRules>("PUT", `/offers/${offerId}/rules`, data),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ["offers", offerId, "rules"] });
    },
  });
}

// ─── Preview правил ───────────────────────────────────────────────────────────

export function useRulesPreview(params?: { offer_id?: string }) {
  return useQuery<RulePreviewOut>({
    queryKey: ["offers", "rules", "preview", params],
    queryFn: ({ signal }) =>
      apiGet<RulePreviewOut>("/offers/rules/preview", params as Record<string, string | number | boolean | null | undefined>, signal),
    staleTime: 60_000,
    enabled: !!params?.offer_id,
  });
}
