import { describe, expect, it } from "vitest";

import { makeOperatorScopeEvidence, makeOperatorSnapshot } from "@fb/shared/operator/testFixture";

import { ApiError } from "@/lib/api/client";
import { validateOperatorPayload } from "@/lib/api/operatorPayload";

function makeIncidentDetail() {
  const incident = makeOperatorSnapshot().attention.data?.items[0];
  if (!incident) throw new Error("operator fixture must contain an incident");
  return {
    state: "ready",
    as_of: "2026-07-18T10:15:00Z",
    freshness_seconds: 0,
    sources: ["incidents", "meta_account_snapshot"],
    issues: [],
    timezone: "Europe/Kaliningrad",
    timezone_known: true,
    scope: makeOperatorScopeEvidence(),
    incident: {
      id: incident.id,
      severity: incident.severity,
      status: "open",
      title: incident.title,
      summary: incident.summary,
      reason: incident.reason,
      occurred_at: incident.occurred_at,
      account_id: "123",
      target: incident.target,
      action: {
        label: "Открыть",
        href: `/incidents/${incident.id}`,
      },
      requires_usd_evidence: true,
    },
  };
}

function makeIncidentAck() {
  return {
    incident_id: "2b80da44-ea54-4aeb-8f83-8101d8f58ee6",
    status: "acknowledged",
    acknowledged_at: "2026-07-18T10:16:00Z",
    correlation_id: "f6291086-0cb9-49a8-827c-df8106c86286",
  };
}

describe("operator response runtime guards", () => {
  it("accepts a complete snapshot", () => {
    const snapshot = makeOperatorSnapshot();
    expect(validateOperatorPayload("/api/operator/snapshot", snapshot)).toBe(snapshot);
  });

  it("rejects malformed nested rows and sections before they enter the query cache", () => {
    const snapshot = makeOperatorSnapshot();
    expect(() =>
      validateOperatorPayload("/api/operator/snapshot", {
        ...snapshot,
        actions: { ...snapshot.actions, data: { items: [null] } },
      }),
    ).toThrow(ApiError);
    expect(() =>
      validateOperatorPayload("/api/operator/ads", {
        state: "ready",
        as_of: "2026-07-18T10:14:45Z",
        freshness_seconds: 15,
        sources: ["postgresql"],
        issues: [],
        rows: [null],
        page: 1,
        page_size: 50,
        total: 1,
        pages: 1,
      }),
    ).toThrow(ApiError);
  });

  it.each([
    "/api/operator/snapshot",
    "/api/operator/actions",
    "/api/operator/ads",
    "/api/operator/incidents",
    "/api/operator/incidents/2b80da44-ea54-4aeb-8f83-8101d8f58ee6",
    "/api/operator/incidents/2b80da44-ea54-4aeb-8f83-8101d8f58ee6/ack",
  ])("rejects malformed %s payload without retryable rendering crashes", (path) => {
    expect(() => validateOperatorPayload(path, {})).toThrow(ApiError);
    expect(() => validateOperatorPayload(path, {})).toThrow(`Некорректный ответ API: ${path}`);
  });

  it("accepts generated incident detail and acknowledgement shapes", () => {
    const detail = makeIncidentDetail();
    const acknowledgement = makeIncidentAck();
    expect(
      validateOperatorPayload(
        "/api/operator/incidents/2b80da44-ea54-4aeb-8f83-8101d8f58ee6",
        detail,
      ),
    ).toBe(detail);
    expect(
      validateOperatorPayload(
        "/api/operator/incidents/2b80da44-ea54-4aeb-8f83-8101d8f58ee6/ack",
        acknowledgement,
      ),
    ).toBe(acknowledgement);
  });

  it.each([
    [
      "detail status",
      "/api/operator/incidents/2b80da44-ea54-4aeb-8f83-8101d8f58ee6",
      () => {
        const detail = makeIncidentDetail();
        return {
          ...detail,
          incident: { ...detail.incident, status: "closed" },
        };
      },
    ],
    [
      "nested incident target",
      "/api/operator/incidents/2b80da44-ea54-4aeb-8f83-8101d8f58ee6",
      () => {
        const detail = makeIncidentDetail();
        return {
          ...detail,
          incident: {
            ...detail.incident,
            target: { ...detail.incident.target, kind: "recipient" },
          },
        };
      },
    ],
    [
      "nested issue",
      "/api/operator/incidents/2b80da44-ea54-4aeb-8f83-8101d8f58ee6",
      () => ({
        ...makeIncidentDetail(),
        issues: [
          {
            code: "cabinet_timezone_unknown",
            title: "Timezone is unknown",
            detail: null,
            severity: "degraded",
            correlation_id: null,
          },
        ],
      }),
    ],
    [
      "acknowledgement status",
      "/api/operator/incidents/2b80da44-ea54-4aeb-8f83-8101d8f58ee6/ack",
      () => ({ ...makeIncidentAck(), status: "open" }),
    ],
    [
      "acknowledgement timestamp",
      "/api/operator/incidents/2b80da44-ea54-4aeb-8f83-8101d8f58ee6/ack",
      () => ({ ...makeIncidentAck(), acknowledged_at: "not-a-date" }),
    ],
  ])("rejects malformed %s fields", (_caseName, path, payload) => {
    expect(() => validateOperatorPayload(path, payload())).toThrow(ApiError);
  });

  it("rejects future operator endpoints until a generated guard is registered", () => {
    expect(() => validateOperatorPayload("/api/operator/unregistered", {})).toThrow(ApiError);
  });

  it("accepts canonical empty ads and actions", () => {
    expect(
      validateOperatorPayload("/api/operator/ads", {
        scope: makeOperatorScopeEvidence(),
        state: "empty",
        as_of: null,
        freshness_seconds: null,
        sources: ["postgresql"],
        issues: [],
        rows: [],
        page: 1,
        page_size: 50,
        total: 0,
        pages: 0,
      }),
    ).toBeTruthy();
    expect(
      validateOperatorPayload("/api/operator/actions", {
        scope: makeOperatorScopeEvidence(),
        state: "empty",
        as_of: null,
        freshness_seconds: null,
        sources: ["postgresql"],
        issues: [],
        items: [],
        next_cursor: null,
      }),
    ).toBeTruthy();
  });
});
