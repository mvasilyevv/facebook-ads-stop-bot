import createClient from "openapi-fetch";
import createQueryClient from "openapi-react-query";

import type { paths as GeneratedPaths } from "@fb/shared/api/generated";

export interface OperatorClientOptions {
  baseUrl?: string;
  fetch?: typeof globalThis.fetch;
}

export function createOperatorFetchClient(options: OperatorClientOptions = {}) {
  return createClient<GeneratedPaths>({
    baseUrl: options.baseUrl ?? "",
    fetch: options.fetch,
  });
}

export function createOperatorQueryClient(options: OperatorClientOptions = {}) {
  return createQueryClient(createOperatorFetchClient(options));
}

export type OperatorQueryClient = ReturnType<typeof createOperatorQueryClient>;

export {
  GeneratedApiError,
  apiProblemMessage,
  dataOrThrow,
  isApiProblem,
  noContentOrThrow,
  safeApiProblemMessage,
} from "./result";
export type { GeneratedApiResult } from "./result";
export {
  createOperatorDisplayPreferenceHooks,
  isOperatorDisplayTimezoneCandidate,
  OPERATOR_DISPLAY_PREFERENCE_QUERY_KEY,
} from "./displayPreferences";
export type {
  OperatorDisplayPreference,
  OperatorDisplayPreferenceUpdate,
} from "./displayPreferences";
export { useOperatorRealtime } from "./realtime";
export type {
  OperatorActionProjectionFetcher,
  OperatorAuthFailureHandler,
  OperatorRealtimeStatus,
  OperatorSnapshotFetcher,
  OperatorWsEvent,
} from "./realtime";
export {
  OPERATOR_REALTIME_READ_MODEL_PATHS,
  reconcileOperatorReadModels,
  reconcileOperatorSnapshots,
} from "./reconciliation";
export type { OperatorCanonicalSnapshotFetcher } from "./reconciliation";
export {
  OperatorRealtimeStatusProvider,
  useOperatorRealtimeStatus,
} from "./realtimeContext";
