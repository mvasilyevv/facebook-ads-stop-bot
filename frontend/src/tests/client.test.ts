import { afterEach, describe, expect, it, vi } from "vitest";

import {
  ApiError,
  invalidApiPayload,
  redirectToLoginOnUnauthorized,
  shouldRetryApiQuery,
} from "@/lib/api/client";

describe("generated API payload guard", () => {
  it("turns structurally invalid JSON into a non-retryable query error", () => {
    const error = invalidApiPayload("/api/operator/snapshot", { unexpected: true });

    expect(error).toBeInstanceOf(ApiError);
    expect(error.payload).toEqual({ unexpected: true });
    expect(shouldRetryApiQuery(0, error)).toBe(false);
  });
});

describe("Telegram session expiry redirect", () => {
  afterEach(() => {
    vi.restoreAllMocks();
    vi.unstubAllGlobals();
  });

  it("uses the same-origin login URL supplied by the server", () => {
    const assign = vi.fn();
    vi.stubGlobal("window", {
      location: {
        pathname: "/campaigns",
        search: "?tab=active",
        hash: "#run",
        origin: "https://app.adpulse.su",
        assign,
      },
    });
    const response = new Response(null, {
      status: 401,
      headers: { "X-Auth-Login-Url": "/auth/login?return_to=%2Fcampaigns" },
    });

    redirectToLoginOnUnauthorized(response);

    expect(assign).toHaveBeenCalledWith("https://app.adpulse.su/auth/login?return_to=%2Fcampaigns");
  });

  it("rejects a cross-origin login URL", () => {
    const assign = vi.fn();
    vi.stubGlobal("window", {
      location: {
        pathname: "/",
        search: "",
        hash: "",
        origin: "https://app.adpulse.su",
        assign,
      },
    });
    const response = new Response(null, {
      status: 401,
      headers: { "X-Auth-Login-Url": "https://evil.example/steal" },
    });

    redirectToLoginOnUnauthorized(response);

    expect(assign).not.toHaveBeenCalled();
  });
});

describe("query retry policy", () => {
  it("does not retry ApiProblem, invalid payloads or client errors", () => {
    expect(
      shouldRetryApiQuery(0, {
        code: "VALIDATION_ERROR",
        message: "Некорректный запрос",
        correlation_id: "corr-422",
        field_errors: null,
      }),
    ).toBe(false);
    expect(shouldRetryApiQuery(0, new ApiError("Некорректный ответ API: /offers", 502, {}))).toBe(
      false,
    );
    expect(shouldRetryApiQuery(0, new ApiError("not found", 404, null))).toBe(false);
  });

  it("retries transient failures at most twice", () => {
    const error = new Error("network down");
    expect(shouldRetryApiQuery(0, error)).toBe(true);
    expect(shouldRetryApiQuery(1, error)).toBe(true);
    expect(shouldRetryApiQuery(2, error)).toBe(false);
  });
});
