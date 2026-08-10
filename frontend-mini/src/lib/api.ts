/** TMA OpenAPI hooks. Authentication is owned by `tmaFetchApi` in auth.ts. */
import { useMutation, useQueryClient } from "@tanstack/react-query";
import { buildOfferRulesBody, type OfferRulesDraft } from "@fb/features/offers";
import type { components } from "@fb/shared/api/generated";
import { dataOrThrow } from "@fb/operator-api";

import { tmaApi, tmaFetchApi } from "./auth";

export * from "./settingsApi";

export type Offer = components["schemas"]["OfferOut"];
export type OfferRules = components["schemas"]["OfferRuleOut"];
export type OfferCreatePayload = components["schemas"]["OfferCreateIn"];
export type OfferUpdatePayload = components["schemas"]["OfferUpdateIn"];

export const QK = { offers: ["get", "/api/offers"] as const };
export const QK_EXT = {
  offerRules: ["get", "/api/offers/{offer_id}/rules"] as const,
};

export function useOffers() {
  return tmaApi.useQuery(
    "get",
    "/api/offers",
    { params: { query: { include_inactive: true } } },
    { staleTime: 30_000 },
  );
}

export function useCreateOffer() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (body: OfferCreatePayload) =>
      dataOrThrow(tmaFetchApi.POST("/api/offers", { body })),
    onSuccess: () => void qc.invalidateQueries({ queryKey: QK.offers }),
  });
}

export function useUpdateOffer() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: ({
      id,
      payload,
    }: {
      id: string;
      payload: OfferUpdatePayload;
    }) =>
      dataOrThrow(
        tmaFetchApi.PUT("/api/offers/{offer_id}", {
          params: { path: { offer_id: id } },
          body: payload,
        }),
      ),
    onSuccess: () => void qc.invalidateQueries({ queryKey: QK.offers }),
  });
}

export function useOfferRules(offerId: string, enabled = true) {
  return tmaApi.useQuery(
    "get",
    "/api/offers/{offer_id}/rules",
    { params: { path: { offer_id: offerId } } },
    { enabled: enabled && !!offerId, staleTime: 30_000 },
  );
}

export function useUpdateOfferRules() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: ({
      offerId,
      payload,
    }: {
      offerId: string;
      payload: OfferRulesDraft;
    }) =>
      dataOrThrow(
        tmaFetchApi.PUT("/api/offers/{offer_id}/rules", {
          params: { path: { offer_id: offerId } },
          body: buildOfferRulesBody(payload),
        }),
      ),
    onSuccess: () => void qc.invalidateQueries({ queryKey: QK_EXT.offerRules }),
  });
}

export function useRulesPreview(params: {
  cpa: string | null;
  currency: string;
  stop_percent_of_rule: number;
  warning_percent_of_stop: number;
}) {
  const currency = params.currency.trim().toUpperCase();
  return tmaApi.useQuery(
    "get",
    "/api/offers/rules/preview",
    {
      params: {
        query: {
          cpa: params.cpa ?? "",
          currency,
          stop_percent_of_rule: params.stop_percent_of_rule,
          warning_percent_of_stop: params.warning_percent_of_stop,
        },
      },
    },
    {
      enabled: params.cpa != null && /^[A-Z]{3}$/.test(currency),
      staleTime: 60_000,
    },
  );
}
