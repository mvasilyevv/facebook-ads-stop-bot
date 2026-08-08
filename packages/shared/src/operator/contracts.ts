/**
 * Operator-console types are aliases over the generated FastAPI OpenAPI
 * contract. Web and TMA may build view-models on top, but do not maintain a
 * second handwritten response schema.
 */

import type { components, operations } from "../api/generated";

export type DataState = components["schemas"]["DataState"];
export type OperatorSeverity = components["schemas"]["OperatorSeverity"];
export type OperatorActionState = components["schemas"]["OperatorActionState"];
export type OperatorWindow =
  components["schemas"]["OperatorSnapshotMeta"]["window"];

export type ApiProblem = components["schemas"]["ApiProblem"];
export type OperatorIssue = components["schemas"]["OperatorIssue"];

type GeneratedSection =
  components["schemas"]["OperatorSection_OperatorEconomyData_"];

/** Generic component-facing projection of the generated concrete sections. */
export type OperatorSection<T> = Omit<GeneratedSection, "data"> & {
  data: T | null;
};

export type OperatorCabinetDay = components["schemas"]["OperatorCabinetDay"];
export type OperatorScopeEvidence =
  components["schemas"]["OperatorScopeEvidence"];
export type OperatorSnapshotMeta =
  components["schemas"]["OperatorSnapshotMeta"];
export type OperatorAttentionItem =
  components["schemas"]["OperatorAttentionItem"];
export type OperatorEconomyTotals =
  components["schemas"]["OperatorEconomyTotals"];
export type OperatorSpendPoint = components["schemas"]["OperatorSpendPoint"];
export type OperatorEconomyData = components["schemas"]["OperatorEconomyData"];
export type OperatorCabinetLedgerRow =
  components["schemas"]["OperatorCabinetLedgerRow"];
export type OperatorCurrencyGroup =
  components["schemas"]["OperatorCurrencyGroup"];
export type OperatorPortfolioData =
  components["schemas"]["OperatorPortfolioData"];
export type OperatorFunnelStage = components["schemas"]["OperatorFunnelStage"];
export type OperatorFunnelStageKey = OperatorFunnelStage["key"];
export type OperatorFunnelData = components["schemas"]["OperatorFunnelData"];
export type OperatorActionItem = components["schemas"]["OperatorActionItem"];
export type OperatorActionsResponse =
  components["schemas"]["OperatorActionsResponse"];
export type OperatorAdRow = components["schemas"]["OperatorAdRow"];
export type OperatorAdsResponse = components["schemas"]["OperatorAdsResponse"];
export type OperatorCommandResponse =
  components["schemas"]["OperatorCommandResponse"];
export type OperatorIncidentAckResponse =
  components["schemas"]["OperatorIncidentAckResponse"];
export type OperatorWorkerState = components["schemas"]["OperatorWorkerState"];
export type OperatorSystemData = components["schemas"]["OperatorSystemData"];
export type OperatorSnapshot = components["schemas"]["OperatorSnapshot"];

export type OperatorSnapshotQuery = NonNullable<
  operations["get_operator_snapshot_api_operator_snapshot_get"]["parameters"]["query"]
>;
export type OperatorActionsQuery = NonNullable<
  operations["get_operator_actions_api_operator_actions_get"]["parameters"]["query"]
>;
export type OperatorAdsQuery = NonNullable<
  operations["get_operator_ads_api_operator_ads_get"]["parameters"]["query"]
>;
