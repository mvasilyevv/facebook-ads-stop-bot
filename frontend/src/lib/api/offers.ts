/** Generated OpenAPI hooks for offer configuration. */

import { useMutation, useQueryClient } from "@tanstack/react-query";
import { buildOfferRulesBody, type OfferRulesDraft } from "@fb/features/offers";
import type { components } from "@fb/shared/api/generated";
import { dataOrThrow, noContentOrThrow } from "@fb/operator-api";
import { generatedApi, generatedFetchApi } from "./generatedClient";

export type Offer = components["schemas"]["OfferOut"];
export type OfferRules = components["schemas"]["OfferRuleOut"];
export type OfferCreateIn = components["schemas"]["OfferCreateIn"];
export type OfferUpdateIn = components["schemas"]["OfferUpdateIn"];

export function useOffers(includeInactive?: boolean) {
  return generatedApi.useQuery(
    "get",
    "/api/offers",
    { params: { query: { include_inactive: includeInactive || undefined } } },
    { staleTime: 30_000 },
  );
}

export function useCreateOffer() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (body: OfferCreateIn) => dataOrThrow(generatedFetchApi.POST("/api/offers", { body })),
    onSuccess: () => void qc.invalidateQueries({ queryKey: ["get", "/api/offers"] }),
  });
}

export function useUpdateOffer(offerId: string) {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (body: OfferUpdateIn) =>
      dataOrThrow(generatedFetchApi.PUT("/api/offers/{offer_id}", { params: { path: { offer_id: offerId } }, body })),
    onSuccess: () => void qc.invalidateQueries({ queryKey: ["get", "/api/offers"] }),
  });
}

/** Soft-deactivate an offer; historical data and configuration are retained. */
export function useDeactivateOffer() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (offerId: string) =>
      noContentOrThrow(
        generatedFetchApi.DELETE("/api/offers/{offer_id}", {
          params: { path: { offer_id: offerId } },
        }),
      ),
    onSuccess: () => void qc.invalidateQueries({ queryKey: ["get", "/api/offers"] }),
  });
}

export function useOfferRules(offerId: string) {
  return generatedApi.useQuery(
    "get",
    "/api/offers/{offer_id}/rules",
    { params: { path: { offer_id: offerId } } },
    { enabled: !!offerId, staleTime: 30_000 },
  );
}

export function useUpdateOfferRules(offerId: string) {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (body: OfferRulesDraft) =>
      dataOrThrow(generatedFetchApi.PUT("/api/offers/{offer_id}/rules", { params: { path: { offer_id: offerId } }, body: buildOfferRulesBody(body) })),
    onSuccess: () => void qc.invalidateQueries({ queryKey: ["get", "/api/offers/{offer_id}/rules"] }),
  });
}

export function useSaveOfferRules() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: ({ offerId, data }: { offerId: string; data: OfferRulesDraft }) =>
      dataOrThrow(generatedFetchApi.PUT("/api/offers/{offer_id}/rules", { params: { path: { offer_id: offerId } }, body: buildOfferRulesBody(data) })),
    onSuccess: () => {
      void qc.invalidateQueries({ queryKey: ["get", "/api/offers"] });
      void qc.invalidateQueries({ queryKey: ["get", "/api/offers/{offer_id}/rules"] });
    },
  });
}

export function useRulesPreview(params: {
  cpa: string | null;
  currency: string;
  stop_percent_of_rule: number;
  warning_percent_of_stop: number;
}) {
  const currency = params.currency.trim().toUpperCase();
  const enabled = params.cpa != null && /^[A-Z]{3}$/.test(currency);
  return generatedApi.useQuery(
    "get",
    "/api/offers/rules/preview",
    { params: { query: { cpa: params.cpa ?? "", currency, stop_percent_of_rule: params.stop_percent_of_rule, warning_percent_of_stop: params.warning_percent_of_stop } } },
    { enabled, staleTime: 60_000 },
  );
}
