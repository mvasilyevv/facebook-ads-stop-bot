import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import {
  makeOperatorScopeEvidence,
  makeOperatorSnapshot,
} from "@fb/shared/operator/testFixture";

vi.mock("@/lib/tg", () => ({
  getInitData: () => "signed_init_data",
}));

import { tmaOperatorFetch } from "@/lib/operatorApi";
import { loginToBackend, logout } from "@/lib/auth";

const COMMAND_RESPONSE = {
  task_id: 42,
  public_id: "#42",
  state: "queued",
  created: true,
  correlation_id: "correlation-42",
};

const EMPTY_ACTIONS_RESPONSE = {
  scope: makeOperatorScopeEvidence(),
  state: "empty",
  as_of: null,
  freshness_seconds: null,
  sources: ["postgresql"],
  issues: [],
  items: [],
  next_cursor: null,
};

interface RequestSnapshot {
  authorization: string | null;
  body: string;
  headers: Array<[string, string]>;
  method: string;
  url: string;
}

async function snapshotRequest(request: Request): Promise<RequestSnapshot> {
  return {
    authorization: request.headers.get("Authorization"),
    body: await request.clone().text(),
    headers: [...request.headers.entries()].filter(
      ([name]) => name.toLowerCase() !== "authorization",
    ),
    method: request.method,
    url: request.url,
  };
}

describe("tmaOperatorFetch", () => {
  beforeEach(() => {
    logout();
    localStorage.clear();
    sessionStorage.clear();
  });

  afterEach(() => {
    vi.restoreAllMocks();
  });

  it("replays a POST exactly once after 401 with only the rotated token", async () => {
    globalThis.fetch = vi.fn().mockResolvedValueOnce(
      new Response(JSON.stringify({ token: "expired_token", role: "owner" }), {
        status: 200,
        headers: { "Content-Type": "application/json" },
      }),
    );
    await loginToBackend();
    const attempts: RequestSnapshot[] = [];
    const loginRequest = { body: "", hadAuthorization: false };

    globalThis.fetch = vi.fn(
      async (input: RequestInfo | URL, init?: RequestInit) => {
        const request =
          input instanceof Request ? input : new Request(input, init);
        if (new URL(request.url).pathname === "/api/tma/auth") {
          loginRequest.hadAuthorization = request.headers.has("Authorization");
          loginRequest.body = await request.text();
          return new Response(
            JSON.stringify({ token: "rotated_token", role: "owner" }),
            { status: 200, headers: { "Content-Type": "application/json" } },
          );
        }

        attempts.push(await snapshotRequest(request));
        return attempts.length === 1
          ? new Response(null, { status: 401 })
          : new Response(JSON.stringify(COMMAND_RESPONSE), {
              status: 200,
              headers: { "Content-Type": "application/json" },
            });
      },
    ) as typeof fetch;

    const body = JSON.stringify({ action: "pause", reason: "threshold" });
    const response = await tmaOperatorFetch(
      "https://operator.test/api/operator/ads/ad-42/pause",
      {
        method: "POST",
        headers: {
          Authorization: "Bearer caller_supplied_token",
          "Content-Type": "application/json",
          "Idempotency-Key": "mutation-42",
          "X-Correlation-Id": "correlation-42",
        },
        body,
      },
    );

    expect(response.status).toBe(200);
    expect(attempts).toHaveLength(2);
    expect(attempts[0]).toMatchObject({
      authorization: "Bearer expired_token",
      body,
      method: "POST",
    });
    expect(attempts[1]).toEqual({
      ...attempts[0],
      authorization: "Bearer rotated_token",
    });
    expect(loginRequest.hadAuthorization).toBe(false);
    expect(loginRequest.body).toBe(
      JSON.stringify({ init_data: "signed_init_data" }),
    );
    expect(loginRequest.body).not.toContain("expired_token");
    expect(loginRequest.body).not.toContain("rotated_token");
  });

  it("shares one token refresh between concurrent 401 responses", async () => {
    globalThis.fetch = vi.fn().mockResolvedValueOnce(
      new Response(JSON.stringify({ token: "expired_token", role: "owner" }), {
        status: 200,
        headers: { "Content-Type": "application/json" },
      }),
    );
    await loginToBackend();
    const retryTokens: Array<string | null> = [];
    let loginCalls = 0;
    let operatorCalls = 0;

    globalThis.fetch = vi.fn(
      async (input: RequestInfo | URL, init?: RequestInit) => {
        const request =
          input instanceof Request ? input : new Request(input, init);
        if (new URL(request.url).pathname === "/api/tma/auth") {
          loginCalls += 1;
          return new Response(
            JSON.stringify({ token: "rotated_token", role: "owner" }),
            { status: 200, headers: { "Content-Type": "application/json" } },
          );
        }

        operatorCalls += 1;
        if (operatorCalls <= 2) return new Response(null, { status: 401 });
        retryTokens.push(request.headers.get("Authorization"));
        const payload = request.url.endsWith("/snapshot")
          ? makeOperatorSnapshot()
          : EMPTY_ACTIONS_RESPONSE;
        return new Response(JSON.stringify(payload), {
          status: 200,
          headers: { "Content-Type": "application/json" },
        });
      },
    ) as typeof fetch;

    const [first, second] = await Promise.all([
      tmaOperatorFetch("https://operator.test/api/operator/snapshot"),
      tmaOperatorFetch("https://operator.test/api/operator/actions"),
    ]);

    expect(first.status).toBe(200);
    expect(second.status).toBe(200);
    expect(loginCalls).toBe(1);
    expect(retryTokens).toEqual([
      "Bearer rotated_token",
      "Bearer rotated_token",
    ]);
  });

  it("rejects malformed successful operator JSON before caching or rendering it", async () => {
    globalThis.fetch = vi.fn(
      async () =>
        new Response(JSON.stringify({ rows: [null] }), {
          status: 200,
          headers: { "Content-Type": "application/json" },
        }),
    ) as typeof fetch;

    await expect(
      tmaOperatorFetch("https://operator.test/api/operator/ads"),
    ).rejects.toThrow("Некорректный ответ API: /api/operator/ads");
  });

  it("rejects semantically contradictory operator JSON", async () => {
    globalThis.fetch = vi.fn(
      async () =>
        new Response(
          JSON.stringify({
            state: "empty",
            as_of: null,
            freshness_seconds: null,
            sources: ["postgresql"],
            issues: [],
            items: [makeOperatorSnapshot().actions.data?.items[0]],
            next_cursor: null,
          }),
          {
            status: 200,
            headers: { "Content-Type": "application/json" },
          },
        ),
    ) as typeof fetch;

    await expect(
      tmaOperatorFetch("https://operator.test/api/operator/actions"),
    ).rejects.toThrow("Некорректный ответ API: /api/operator/actions");
  });
});
