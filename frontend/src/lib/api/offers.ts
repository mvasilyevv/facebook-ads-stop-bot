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
 */

import { useQuery, useMutation, useQueryClient } from "@tanstack/react-query";
import { apiGet, apiSend } from "./client";
import type { Offer, OfferRules } from "@fb/shared";
import type { components } from "@fb/shared/api/generated";

type OfferCompareRow = components["schemas"]["OfferCompareRow"];
type RulePreviewOut = components["schemas"]["RulePreviewOut"];

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
  /** FB Pixel ID оффера (числовой; null/пусто — не задан). */
  pixel_id?: string | null;
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

/**
 * useSaveOfferRules — PUT правил для ПРОИЗВОЛЬНОГО offerId (id в payload мутации).
 * Нужен для flow создания: id появляется только после POST /offers, поэтому
 * useUpdateOfferRules(id) с id-в-конструкторе там не годится.
 */
export function useSaveOfferRules() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: ({ offerId, data }: { offerId: string; data: Partial<OfferRules> }) =>
      apiSend<OfferRules>("PUT", `/offers/${offerId}/rules`, data),
    onSuccess: (_res, vars) => {
      qc.invalidateQueries({ queryKey: ["offers", vars.offerId, "rules"] });
      qc.invalidateQueries({ queryKey: ["offers"] });
    },
  });
}

// ─── Live-разбивка порогов (авторасчёт от CPA + чувствительности) ───────────────

/**
 * GET /offers/rules/preview — рассчитывает $-пороги (CPC/CPL/CPR + spend-диапазоны)
 * из CPA и процентов чувствительности. Тот же RuleContext, что и у observer'а, —
 * значения в preview ТОЧНО совпадают с реальными стоп-порогами.
 * enabled только при cpa > 0; placeholderData держит прошлый результат, чтобы
 * таблица не мигала при движении ползунка.
 */
export function useRulesPreview(params: {
  cpa: number | null;
  stop_percent_of_rule: number;
  warning_percent_of_stop: number;
}) {
  const enabled = params.cpa != null && params.cpa > 0;
  return useQuery<RulePreviewOut>({
    queryKey: ["offers", "rules", "preview", params],
    queryFn: ({ signal }) =>
      apiGet<RulePreviewOut>(
        "/offers/rules/preview",
        {
          cpa: params.cpa as number,
          stop_percent_of_rule: params.stop_percent_of_rule,
          warning_percent_of_stop: params.warning_percent_of_stop,
        },
        signal,
      ),
    enabled,
    staleTime: 60_000,
    placeholderData: (prev) => prev,
  });
}

