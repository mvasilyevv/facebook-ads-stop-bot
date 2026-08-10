import { createOperatorFetchClient, createOperatorQueryClient } from "@fb/operator-api";

import { redirectToLoginOnUnauthorized } from "./client";
import { validateOperatorPayload } from "./operatorPayload";

const generatedClientOptions = {
  baseUrl: globalThis.location?.origin ?? "http://localhost",
  fetch: generatedFetch,
};

export const generatedApi = createOperatorQueryClient(generatedClientOptions);
/** Raw generated client for one-off commands outside React Query hooks. */
export const generatedFetchApi = createOperatorFetchClient(generatedClientOptions);

async function generatedFetch(input: RequestInfo | URL, init?: RequestInit): Promise<Response> {
  const response = await fetch(input, init);
  if (response.status === 401) redirectToLoginOnUnauthorized(response);
  if (response.ok) {
    const rawUrl = input instanceof Request ? input.url : String(input);
    const path = new URL(rawUrl, globalThis.location?.origin ?? "http://localhost").pathname;
    if (path.startsWith("/api/operator/")) {
      const body: unknown = await response
        .clone()
        .json()
        .catch(() => {
          throw new Error(`Некорректный JSON API: ${path}`);
        });
      validateOperatorPayload(path, body);
    }
  }
  return response;
}
