import { describe, expect, it } from "vitest";

import {
  makeOperatorScopeEvidence,
  makeOperatorSnapshot,
} from "../testFixture";
import {
  OperatorPayloadValidationError,
  validateOperatorPayload,
} from "../runtimeValidation";

const ACTION = makeOperatorSnapshot().actions.data!.items[0]!;

function adsResponse() {
  return {
    state: "ready",
    as_of: "2026-07-18T10:14:45Z",
    freshness_seconds: 15,
    sources: ["meta", "adsetpro"],
    issues: [],
    scope: makeOperatorScopeEvidence(),
    rows: [
      {
        id: "ad-row-1",
        fb_ad_id: "120001",
        name: "GH_CR2",
        campaign_id: "campaign-1",
        campaign_name: "Campaign 1",
        adset_id: "adset-1",
        adset_name: "Ad set 1",
        account_id: "123",
        delivery_status: "ACTIVE",
        data_state: "ready",
        severity: "ok",
        as_of: "2026-07-18T10:14:45Z",
        metrics: {
          spend: "18.40",
          impressions: 1000,
          clicks: 42,
          registrations: 5,
          ftd: 1,
          confirmed_deposits: 1,
          cpc: "0.4381",
          cost_per_registration: "3.68",
        },
        active_action: null,
      },
    ],
    page: 1,
    page_size: 50,
    total: 1,
    pages: 1,
  };
}

function incidentResponse() {
  return {
    state: "ready",
    as_of: "2026-07-18T10:14:45Z",
    freshness_seconds: 0,
    sources: ["incidents", "meta_account_snapshot"],
    issues: [],
    timezone: "Europe/Kaliningrad",
    timezone_known: true,
    status: "open",
    incident: makeOperatorSnapshot().attention.data!.items[0]!,
  };
}

describe("operator semantic runtime validation", () => {
  it("rejects ready snapshot sections without current evidence", () => {
    const snapshot = makeOperatorSnapshot();

    for (const invalidSection of [
      { ...snapshot.economy, data: null },
      { ...snapshot.economy, as_of: null },
      { ...snapshot.economy, freshness_seconds: null },
      {
        ...snapshot.economy,
        issues: [
          {
            code: "ECONOMY_INCOMPLETE",
            title: "Экономика не подтверждена",
            detail: null,
            severity: "critical",
            correlation_id: "corr-economy",
          },
        ],
      },
    ]) {
      expect(() =>
        validateOperatorPayload("/api/operator/snapshot", {
          ...snapshot,
          economy: invalidSection,
        }),
      ).toThrow(OperatorPayloadValidationError);
    }
  });

  it("rejects empty collection sections which still contain rows", () => {
    const snapshot = makeOperatorSnapshot();

    expect(() =>
      validateOperatorPayload("/api/operator/snapshot", {
        ...snapshot,
        attention: { ...snapshot.attention, state: "empty" },
      }),
    ).toThrow(OperatorPayloadValidationError);
    expect(() =>
      validateOperatorPayload("/api/operator/snapshot", {
        ...snapshot,
        actions: { ...snapshot.actions, state: "empty" },
      }),
    ).toThrow(OperatorPayloadValidationError);
  });

  it("rejects ready collection sections which contain no rows", () => {
    const snapshot = makeOperatorSnapshot();
    expect(() =>
      validateOperatorPayload("/api/operator/snapshot", {
        ...snapshot,
        actions: {
          ...snapshot.actions,
          data: { items: [] },
        },
      }),
    ).toThrow(OperatorPayloadValidationError);
  });

  it("rejects attention actions outside the internal route allowlist", () => {
    const snapshot = makeOperatorSnapshot();
    const item = snapshot.attention.data!.items[0]!;

    expect(() =>
      validateOperatorPayload("/api/operator/snapshot", {
        ...snapshot,
        attention: {
          ...snapshot.attention,
          data: {
            items: [
              {
                ...item,
                action: {
                  label: "Открыть",
                  href: "https://example.invalid/operator",
                },
              },
            ],
          },
        },
      }),
    ).toThrow(OperatorPayloadValidationError);
  });

  it("rejects contradictory action state and cursor semantics", () => {
    expect(() =>
      validateOperatorPayload("/api/operator/actions", {
        state: "empty",
        as_of: null,
        freshness_seconds: null,
        sources: ["postgresql"],
        issues: [],
        scope: makeOperatorScopeEvidence(),
        items: [ACTION],
        next_cursor: null,
      }),
    ).toThrow(OperatorPayloadValidationError);
    expect(() =>
      validateOperatorPayload("/api/operator/actions", {
        state: "ready",
        as_of: ACTION.updated_at,
        freshness_seconds: 0,
        sources: ["postgresql"],
        issues: [],
        scope: makeOperatorScopeEvidence(),
        items: [ACTION],
        next_cursor: 999,
      }),
    ).toThrow(OperatorPayloadValidationError);
  });

  it("never accepts ready top-level responses with unresolved issues", () => {
    const issue = {
      code: "SOURCE_DEGRADED",
      title: "Источник подтверждён не полностью",
      detail: null,
      severity: "warning",
      correlation_id: "corr-source",
    };
    const actions = {
      state: "ready",
      as_of: ACTION.updated_at,
      freshness_seconds: 0,
      sources: ["postgresql"],
      issues: [issue],
      scope: makeOperatorScopeEvidence(),
      items: [ACTION],
      next_cursor: null,
    };
    const ads = { ...adsResponse(), issues: [issue] };
    const incident = { ...incidentResponse(), issues: [issue] };

    expect(() =>
      validateOperatorPayload("/api/operator/actions", actions),
    ).toThrow(OperatorPayloadValidationError);
    expect(() => validateOperatorPayload("/api/operator/ads", ads)).toThrow(
      OperatorPayloadValidationError,
    );
    expect(() =>
      validateOperatorPayload("/api/operator/incidents/incident-1", incident),
    ).toThrow(OperatorPayloadValidationError);
  });

  it("rejects an empty incident response which still carries an incident", () => {
    expect(() =>
      validateOperatorPayload("/api/operator/incidents/incident-1", {
        ...incidentResponse(),
        state: "empty",
      }),
    ).toThrow(OperatorPayloadValidationError);
  });

  it("rejects invalid or falsely confirmed incident timezones", () => {
    expect(() =>
      validateOperatorPayload("/api/operator/incidents/incident-1", {
        ...incidentResponse(),
        timezone: "Mars/Olympus",
      }),
    ).toThrow(OperatorPayloadValidationError);
    expect(() =>
      validateOperatorPayload("/api/operator/incidents/incident-1", {
        ...incidentResponse(),
        timezone_known: false,
      }),
    ).toThrow(OperatorPayloadValidationError);
  });

  it("rejects non-RFC3339 and impossible timestamps", () => {
    const snapshot = makeOperatorSnapshot();
    for (const generatedAt of [
      "0",
      "2026-07-29",
      "2026-07-29T12:00:00",
      "2026-02-30T00:00:00Z",
    ]) {
      expect(() =>
        validateOperatorPayload("/api/operator/snapshot", {
          ...snapshot,
          meta: { ...snapshot.meta, generated_at: generatedAt },
        }),
      ).toThrow(OperatorPayloadValidationError);
    }
  });

  it("rejects reversed cabinet-day boundaries", () => {
    const snapshot = makeOperatorSnapshot();
    expect(() =>
      validateOperatorPayload("/api/operator/snapshot", {
        ...snapshot,
        meta: {
          ...snapshot.meta,
          cabinet_day: {
            starts_at: "2026-07-19T00:00:00+02:00",
            ends_at: "2026-07-18T00:00:00+02:00",
          },
        },
      }),
    ).toThrow(OperatorPayloadValidationError);
  });

  it("rejects inconsistent ad state, totals and pagination", () => {
    const valid = adsResponse();
    expect(validateOperatorPayload("/api/operator/ads", valid)).toBe(valid);

    for (const invalid of [
      { ...valid, state: "empty" },
      { ...valid, total: 51, pages: 1 },
      { ...valid, page: 2, total: 1, pages: 1 },
      { ...valid, total: 2, pages: 1 },
      {
        ...valid,
        rows: [{ ...valid.rows[0]!, as_of: null }],
      },
      {
        ...valid,
        rows: [{ ...valid.rows[0]!, data_state: "stale" }],
      },
      {
        ...valid,
        state: "partial",
        rows: [{ ...valid.rows[0]!, data_state: "empty" }],
      },
    ]) {
      expect(() =>
        validateOperatorPayload("/api/operator/ads", invalid),
      ).toThrow(OperatorPayloadValidationError);
    }
  });

  it("rejects unsupported or contradictory account-context evidence", () => {
    const valid = adsResponse();
    for (const scope of [
      { ...valid.scope, currency: "ZZZ" },
      { ...valid.scope, currency_state: "mixed", currency: "USD" },
      {
        ...valid.scope,
        cabinet_timezone_state: "unknown",
        cabinet_timezone: "Europe/Kaliningrad",
      },
      { ...valid.scope, display_timezone: "Mars/Olympus" },
    ]) {
      expect(() =>
        validateOperatorPayload("/api/operator/ads", { ...valid, scope }),
      ).toThrow(OperatorPayloadValidationError);
    }
  });

  it("accepts a reviewed backend currency absent from Intl.supportedValuesOf", () => {
    const valid = adsResponse();
    const payload = {
      ...valid,
      scope: {
        ...valid.scope,
        currency: "VED",
      },
    };

    expect(validateOperatorPayload("/api/operator/ads", payload)).toBe(payload);
  });

  it("rejects money values when response currency is not confirmed", () => {
    const valid = adsResponse();
    const unknownScope = {
      ...valid.scope,
      currency: null,
      currency_state: "unknown",
      missing_currency_account_ids: ["act_123"],
      currency_observed_at: null,
    };
    expect(() =>
      validateOperatorPayload("/api/operator/ads", {
        ...valid,
        state: "partial",
        scope: unknownScope,
      }),
    ).toThrow(OperatorPayloadValidationError);

    const snapshot = makeOperatorSnapshot();
    expect(() =>
      validateOperatorPayload("/api/operator/snapshot", {
        ...snapshot,
        meta: {
          ...snapshot.meta,
          currency: null,
          currency_state: "unknown",
          missing_currency_account_ids: ["act_123"],
          currency_observed_at: null,
        },
        economy: { ...snapshot.economy, state: "partial" },
        funnel: { ...snapshot.funnel, state: "partial" },
      }),
    ).toThrow(OperatorPayloadValidationError);
  });
});
