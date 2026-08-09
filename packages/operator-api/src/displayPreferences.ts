import { useQueryClient } from "@tanstack/react-query";
import type { components } from "@fb/shared/api/generated";

import type { OperatorQueryClient } from "./index";

export type OperatorDisplayPreference =
  components["schemas"]["OperatorDisplayPreferenceResponse"];
export type OperatorDisplayPreferenceUpdate =
  components["schemas"]["OperatorDisplayPreferencePutRequest"];

export const OPERATOR_DISPLAY_PREFERENCE_QUERY_KEY = [
  "get",
  "/api/operator/preferences/display",
] as const;

/**
 * Client-side shape check only. The backend IANA database is authoritative;
 * clients must not reject a valid zone merely because a device tzdb is older.
 */
export function isOperatorDisplayTimezoneCandidate(value: string): boolean {
  const normalized = value.trim();
  return (
    normalized.length > 0 && normalized.length <= 64 && !/\s/.test(normalized)
  );
}

/**
 * Bind the same typed preference lifecycle to a surface-specific transport.
 * Web supplies its panel-cookie client; TMA supplies its rotating Bearer client.
 */
export function createOperatorDisplayPreferenceHooks(api: OperatorQueryClient) {
  function useOperatorDisplayPreference(enabled = true) {
    return api.useQuery(
      "get",
      "/api/operator/preferences/display",
      {},
      {
        enabled,
        staleTime: 60_000,
        retry: false,
      },
    );
  }

  function useUpdateOperatorDisplayPreference() {
    const queryClient = useQueryClient();
    return api.useMutation("put", "/api/operator/preferences/display", {
      meta: { suppressGlobalError: true },
      onSuccess: (preference) => {
        queryClient.setQueriesData(
          { queryKey: OPERATOR_DISPLAY_PREFERENCE_QUERY_KEY },
          preference,
        );
      },
    });
  }

  return {
    useOperatorDisplayPreference,
    useUpdateOperatorDisplayPreference,
  };
}
